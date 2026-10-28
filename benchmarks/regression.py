"""Interleaved published/published/candidate gate using the frozen G0 workload."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import platform
import queue
import random
import re
from statistics import median
import subprocess
import sys
import threading
import uuid

import psutil

from common import cases
from report import render
from run import ROOT, execute, fastls_input, revision, sdk_digests, versions
from worker import expected_digest

ARMS = ("control-a", "control-b", "candidate")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def schedule(selected, rounds=5):
    plan = []
    for number in range(rounds):
        ordered = list(selected)
        random.Random(6027 + number).shuffle(ordered)
        for index, case in enumerate(ordered):
            arms = list(ARMS)
            if number % 2:
                arms.reverse()
            shift = (number + index) % len(arms)
            for arm in arms[shift:] + arms[:shift]:
                plan.append(dict(case=case, variant=arm, round=number + 1))
    return plan


def invalid_reason(run):
    m = run.get("metrics", {})
    warm = m.get("warmup", {})
    if not run.get("valid") or run.get("exit_code") != 0:
        return "worker failed"
    if not m.get("success") or m.get("failed") != 0 or m.get("errors") or m.get("contract_errors"):
        return "measurement/contract failed"
    if not warm.get("success") or warm.get("failed") != 0 or warm.get("errors"):
        return "warmup failed"
    if m.get("successful_bytes") != m["success"] * run["case"]["size"]:
        return "business byte count mismatch"
    expected = {"h1": "HTTP/1.1", "h2": "HTTP/2.0"}[run["case"]["protocol"]]
    protocols = {c["protocol"] for c in m.get("server", {}).get("connections", {}).values()}
    if protocols != {expected} or not m.get("tls_wrong_ca_rejected"):
        return "protocol/TLS mismatch"
    native = m.get("native_final", {})
    if any(native.get(key) != 0 for key in ("open_requests", "open_sessions", "workers", "notifiers")):
        return "native handles missing or unrecovered"
    return None


def aggregate(rows):
    if not rows or any(invalid_reason(row) for row in rows):
        return None
    result = {}
    for key in ("rps", "mib_per_second"):
        values = [row["metrics"].get(key) for row in rows]
        if any(value is None or not math.isfinite(value) or value <= 0 for value in values):
            return None
        result[key] = median(values)
    for key in ("cpu_seconds", "peak_rss_bytes"):
        values = [row.get("resources", {}).get(key) for row in rows]
        if any(value is None or not math.isfinite(value) or value < 0 for value in values):
            return None
        result[key] = median(values)
    # Pool five rounds to avoid a single short transfer's Windows CPU tick
    # becoming a zero denominator. No missing round is replaced by zero.
    result["cpu_seconds_per_byte"] = sum(row["resources"]["cpu_seconds"] for row in rows) / sum(row["metrics"]["successful_bytes"] for row in rows)
    enough = all(row["metrics"]["success"] >= 20 for row in rows)
    result["p95_applicable"] = enough
    values = [row["metrics"].get("p95_seconds") for row in rows]
    if enough:
        if any(value is None or not math.isfinite(value) or value <= 0 for value in values):
            return None
        result["p95_seconds"] = median(values)
    return result


def compare(reference, target, policy, slow=False):
    if reference is None or target is None:
        return dict(status="unevaluated", reason="missing or invalid required metric")
    rules = policy["candidate_vs_fresh_fastls_control"]
    checks = {}
    for key, rule in (("rps", "minimum_median_rps_ratio"), ("mib_per_second", "minimum_median_mib_per_second_ratio")):
        ratio = target[key] / reference[key]
        checks[key] = dict(ratio=ratio, minimum=rules[rule], passed=ratio >= rules[rule], applicable=not slow)
    cpu = reference["cpu_seconds_per_byte"]
    cpu_measurable = cpu > 0 and target["cpu_seconds_per_byte"] > 0
    checks["cpu_per_byte"] = dict(ratio=target["cpu_seconds_per_byte"] / cpu if cpu_measurable else None,
                                   maximum=rules["maximum_cpu_seconds_per_successful_business_byte_ratio"],
                                   passed=cpu_measurable and target["cpu_seconds_per_byte"] / cpu <= rules["maximum_cpu_seconds_per_successful_business_byte_ratio"], applicable=True)
    allowance = max(16 * 1048576, .2 * reference["peak_rss_bytes"])
    checks["peak_rss"] = dict(addition_bytes=target["peak_rss_bytes"] - reference["peak_rss_bytes"], maximum_addition_bytes=allowance,
                              passed=target["peak_rss_bytes"] <= reference["peak_rss_bytes"] + allowance, applicable=True)
    if reference["p95_applicable"] and target["p95_applicable"]:
        ratio = target["p95_seconds"] / reference["p95_seconds"]
        checks["p95"] = dict(ratio=ratio, maximum=rules["maximum_median_p95_latency_ratio_when_available"],
                              passed=ratio <= rules["maximum_median_p95_latency_ratio_when_available"], applicable=True)
    else:
        checks["p95"] = dict(applicable=False, reason="fewer than 20 successful responses in at least one round")
    if any(check.get("ratio", 1) is None for check in checks.values() if check["applicable"]):
        return dict(status="unevaluated", checks=checks, reason="zero CPU denominator")
    return dict(status="passed" if all(check["passed"] for check in checks.values() if check["applicable"]) else "failed", checks=checks)


def evaluate(runs, historical, selected, policy, integrity):
    rows = []
    rounds = policy["eligibility"]["minimum_rounds_per_variant"]
    for case in selected:
        name = case["name"]
        groups = {arm: [r for r in runs if r["variant"] == arm and r["case"]["name"] == name] for arm in ARMS}
        reasons = [f"{arm}: expected {rounds} complete rounds" for arm, group in groups.items()
                   if len(group) != rounds or {r["round"] for r in group} != set(range(1, rounds + 1))]
        reasons += [f"{arm} round {r['round']}: {why}" for arm, group in groups.items() for r in group if (why := invalid_reason(r))]
        summaries = {arm: aggregate(group) for arm, group in groups.items()}
        if any(value is None for value in summaries.values()):
            reasons.append("required metrics missing/invalid")
        old = aggregate(historical.get(name, []))
        if old is None:
            reasons.append("historical baseline missing/invalid")
        drift = {}
        if old and all(summaries[arm] for arm in ARMS[:2]):
            maximum = policy["eligibility"]["maximum_control_throughput_drift_from_recorded_baseline_fraction"]
            for arm in ARMS[:2]:
                ratio = summaries[arm]["mib_per_second"] / old["mib_per_second"]
                drift[arm] = dict(ratio=ratio, maximum_fraction=maximum, passed=abs(ratio - 1) <= maximum)
                if not drift[arm]["passed"]:
                    reasons.append(f"{arm}: historical throughput drift exceeds {maximum:.0%}")
        aa = dict(forward=compare(summaries["control-a"], summaries["control-b"], policy, case["workload"] == "slow"),
                  reverse=compare(summaries["control-b"], summaries["control-a"], policy, case["workload"] == "slow"))
        if any(value["status"] != "passed" for value in aa.values()):
            reasons.append("A/A check failed or unevaluated")
        if not integrity:
            reasons.append("source or environment integrity mismatch")
        candidate = compare(summaries["control-a"], summaries["candidate"], policy, case["workload"] == "slow")
        # Preserve ratios for diagnosis; failures of prerequisites never become
        # a pass or an asserted performance regression in the candidate.
        if reasons:
            candidate["diagnostic_status"] = candidate["status"]
            candidate["status"] = "ineligible"
        rows.append(dict(case=name, summaries=summaries, historical=old, drift=drift, aa=aa,
                         eligible=not reasons, eligibility_failures=reasons, candidate=candidate))
    status = "ineligible" if any(not row["eligible"] for row in rows) else "passed" if all(row["candidate"]["status"] == "passed" for row in rows) else "failed"
    return dict(status=status, success=status == "passed", planned_runs=rounds * len(selected) * 3,
                actual_runs=len(runs), cases=rows)


def candidate_input(root):
    root = root.resolve()
    info = load(root / "dist/build-info.json")
    filename = "fastls.dll" if os.name == "nt" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"
    library = root / "dist" / filename
    if info["library_sha256"] != sha(library):
        raise RuntimeError("invalid candidate native artifact")
    sources = info['sources']
    if info.get('local_dependencies') is False:
        graph = info.get('module_graph')
        if not graph or graph != load(root / 'dist/module-inputs.json'):
            raise RuntimeError('missing or inconsistent formal module inputs')
        if info.get('module_files') != {p:sha(root/p) for p in ('go.mod','go.sum')}:
            raise RuntimeError('formal module files changed since build')
        expected = {}
        for name in ('fastls','ftransport','imitate'):
            path = 'github.com/FastTLS/' + name
            rows = [m for m in graph if m['Path']==path]
            if len(rows)!=1:raise RuntimeError('formal source missing or duplicated: '+name)
            module=rows[0];origin=module.get('Origin',{});version=module.get('Version')
            if module.get('Replace') or not all(module.get(k) for k in ('Sum','GoModSum')) or origin.get('Ref')!='refs/tags/'+str(version):
                raise RuntimeError('invalid formal module provenance: '+name)
            if not re.fullmatch(r'[0-9a-f]{40}',origin.get('Hash','')) or not re.search(re.escape(path)+r'\s+'+re.escape(version)+r'(?:\s|$)',(root/'go.mod').read_text()):
                raise RuntimeError('formal selected version/source mismatch: '+name)
            expected[name]=origin['Hash']
        if {k:v for k,v in sources.items() if k!='fastls-ffi'}!=expected:
            raise RuntimeError('formal source commits differ from module origins')
        states={'fastls-ffi':sources['fastls-ffi']}
    elif info.get('local_dependencies') is True:
        states=sources
    else:raise RuntimeError('candidate dependency mode missing')
    for name, commit in states.items():
        state = revision(root if name == "fastls-ffi" else root.parent / name)
        if state != dict(commit=commit, dirty_files=[]):
            raise RuntimeError(f"candidate source differs from build metadata: {name}")
    return dict(source="checkout", python_path=str(root / "python"), library=str(library), build_info=info, sources=info["sources"])


def fingerprints(inputs, fixture, policy_file, historical_files):
    result = dict(harness=revision(ROOT), harness_files={path.name: sha(path) for path in (ROOT / "benchmarks").glob("*.py")},
                  fixture=sha(fixture), policy=sha(policy_file), lock=sha(ROOT / "benchmarks/requirements.lock"),
                  historical={str(path): sha(path) for path in historical_files}, packages=versions(),
                  runtime=dict(python=sys.version, executable=sys.executable, platform=platform.platform(), machine=platform.machine(),
                               cpu=platform.processor(), logical_cpus=psutil.cpu_count(), memory_bytes=psutil.virtual_memory().total), inputs={})
    for arm, value in inputs.items():
        library = Path(value["library"])
        result["inputs"][arm] = dict(library=sha(library), metadata=sha(library.parent / "build-info.json"), sdk=sdk_digests(value))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    baseline = args.baseline_dir.resolve()
    fixture = args.fixture.resolve()
    policy_file = baseline / "regression-policy-v1.json"
    policy = load(policy_file)
    history_files = [baseline / "full-transfer/results.json", baseline / "full-small/results.json"]
    old_reports = [load(path) for path in history_files]
    historical = {}
    for report in old_reports:
        for run in report["runs"]:
            if run["client"] == "fastls":
                historical.setdefault(run["case"]["name"], []).append(run)
    selected = [case for case in cases("full") if case["name"] in historical]
    assert len(selected) == 10 and len(historical) == 10
    control = fastls_input("installed")
    candidate = candidate_input(args.candidate_root)
    for report in old_reports:
        assert report["success"] and not report["source_changed_during_run"]
        assert report["fixture_sha256"] == sha(fixture) and report["library_sha256"] == sha(control["library"])
        assert report["packages"] == versions() and report["platform"] == platform.platform()
        assert report["python"] == sys.version and report["machine"] == platform.machine()
        assert report["cpu"] == platform.processor() and report["logical_cpus"] == psutil.cpu_count()
        assert report["memory_bytes"] == psutil.virtual_memory().total
        for case in selected:
            if case["name"] in report["selected_case_names"]:
                assert all(r["case"] == case for r in report["runs"] if r["case"]["name"] == case["name"])
    inputs = {"control-a": control, "control-b": control, "candidate": candidate}
    plan = schedule(selected, policy["eligibility"]["minimum_rounds_per_variant"])
    before = fingerprints(inputs, fixture, policy_file, history_files)
    assert not before["harness"]["dirty_files"], "commit the driver before measuring"
    for report in old_reports:
        assert all(before["harness_files"][name] == value for name, value in report["benchmark_sources"].items()), "historical worker/sampler changed"
    experiment = dict(created_at=datetime.now(timezone.utc).isoformat(), profile="full", rounds=5,
                      design="three arms, new independent worker per run; five interleaved blocks; candidate verdict only after A/A eligibility",
                      aa_rule="apply frozen candidate thresholds in both directions; no thresholds changed after observations",
                      cpu_rule="sum measured CPU over all five rounds / sum successful business bytes; no missing values or zeros substituted",
                      scope="same Windows AMD64 host, CPython 3.11, selected portable H1/H2; no H3/browser/other-platform certification",
                      policy=policy, policy_sha256=sha(policy_file), inputs=inputs, fingerprints_before=before, plan=plan,
                      expected_body_sha256={case["name"]: expected_digest(case["size"]) for case in selected}, runs=[])
    save(output / "plan.json", {key: value for key, value in experiment.items() if key != "runs"})
    save(output / "results.json", experiment)
    with (output / "fixture.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(fixture)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
            reader.start()
            fixture_info = json.loads(lines.get(timeout=20))
            reader.join(2)
            for index, task in enumerate(plan):
                spec = dict(case=task["case"], client="fastls", round=task["round"], fixture=fixture_info,
                            run_id=uuid.uuid4().hex, fastls_input=inputs[task["variant"]], seconds=30,
                            warmup_seconds=10 if task["case"]["workload"] == "small" else .05,
                            hold_ms=5000, slow_bytes_per_second=16 * 1048576)
                save(output / "progress.json", dict(completed=index, total=len(plan), current=task, running=True))
                try:
                    result = execute(spec, process, output)
                except Exception as error:
                    result = dict(case=task["case"], client="fastls", round=task["round"], valid=False,
                                  exit_code=None, error=repr(error), metrics={}, resources={})
                result["variant"] = task["variant"]
                result["run_id"] = spec["run_id"]
                experiment["runs"].append(result)
                save(output / "results.json", experiment)
                print(json.dumps(dict(completed=index + 1, total=len(plan), case=task["case"]["name"], variant=task["variant"],
                                      round=task["round"], valid=result["valid"])), flush=True)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
    after = fingerprints(inputs, fixture, policy_file, history_files)
    experiment["fingerprints_after"] = after
    experiment["integrity"] = before == after and candidate_input(args.candidate_root) == candidate
    experiment["evaluation"] = evaluate(experiment["runs"], historical, selected, policy, experiment["integrity"])
    save(output / "results.json", experiment)
    save(output / "evaluation.json", experiment["evaluation"])
    save(output / "progress.json", dict(completed=len(experiment["runs"]), total=len(plan), running=False,
                                        verdict=experiment["evaluation"]["status"]))
    reports = []
    for arm in ARMS:
        reports.append(dict(environment_id=arm, profile="full", rounds=5, fastls_input=inputs[arm],
                            source_changed_during_run=not experiment["integrity"], runs=[r for r in experiment["runs"] if r["variant"] == arm]))
    render(reports, output / "report.html")
    page = (output / "report.html").read_text(encoding="utf-8")
    notice = '<p class="notice">G1 回归结论：<strong>' + html.escape(experiment["evaluation"]["status"]) + '</strong>。A/A 或历史漂移不通过时，候选数值仅供诊断。<a href="evaluation.json">逐场景门禁</a> · <a href="results.json">完整结果</a></p>'
    (output / "report.html").write_text(page.replace('<p id="scope"', notice + '<p id="scope"'), encoding="utf-8")
    print(json.dumps(dict(verdict=experiment["evaluation"]["status"], runs=len(experiment["runs"]))))
    return 0 if experiment["evaluation"]["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
