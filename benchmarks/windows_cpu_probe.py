"""Diagnostic only: pair Windows process CPU seconds with raw cycle counts.

The frozen regression report is not rewritten. Cycles are never converted to
CPU seconds or substituted into its 1.25 CPU-seconds/byte release gate.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import queue
from statistics import median
import subprocess
import threading
import uuid

import run as engine
from regression import ARMS, candidate_input, fingerprints, load, save, schedule

BASE_SNAPSHOT = engine.snapshot


def process_cycles(pid):
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.QueryProcessCycleTime.argtypes = [W.HANDLE, C.POINTER(C.c_ulonglong)]
    kernel.QueryProcessCycleTime.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.CloseHandle.restype = W.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise C.WinError(C.get_last_error())
    try:
        count = C.c_ulonglong()
        if not kernel.QueryProcessCycleTime(handle, C.byref(count)):
            raise C.WinError(C.get_last_error())
        return count.value
    finally:
        kernel.CloseHandle(handle)


def snapshot_with_cycles(process):
    result = BASE_SNAPSHOT(process)
    for member in result["processes"]:
        member["cpu_cycles"] = process_cycles(member["pid"])
    result["cpu_cycles"] = sum(member["cpu_cycles"] for member in result["processes"])
    return result


def summarize(rows):
    result = []
    names = sorted({row["case"]["name"] for row in rows})
    for name in names:
        arms = {}
        for arm in ARMS:
            group = [row for row in rows if row["case"]["name"] == name and row["variant"] == arm]
            valid = len(group) == 5 and all(row["valid"] and not row["metrics"].get("failed") and not row["metrics"]["warmup"].get("failed") for row in group)
            if not valid:
                arms[arm] = dict(valid=False)
                continue
            business = sum(row["metrics"]["successful_bytes"] for row in group)
            cpu = sum(row["resources"]["cpu_seconds"] for row in group)
            cycles = sum(row["diagnostic_cycles"] for row in group)
            arms[arm] = dict(valid=True, cpu_seconds_per_byte=cpu/business, cycles_per_byte=cycles/business,
                             cycles=cycles, cpu_seconds=cpu, business_bytes=business,
                             median_mib_per_second=median(row["metrics"]["mib_per_second"] for row in group),
                             zero_cpu_with_positive_cycles=sum(row["resources"]["cpu_seconds"] == 0 and row["diagnostic_cycles"] > 0 for row in group))
        ratios = {}
        if all(arms[arm]["valid"] for arm in ARMS):
            for arm in ARMS[1:]:
                ratios[arm] = {key: arms[arm][key]/arms["control-a"][key] if arms["control-a"][key] > 0 else None
                               for key in ("cpu_seconds_per_byte", "cycles_per_byte", "median_mib_per_second")}
        result.append(dict(case=name, arms=arms, ratios_vs_control_a=ratios))
    return dict(scope="independent CPU accounting diagnosis, not a replacement performance gate", cases=result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True, type=Path)
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows diagnostic only")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = load(args.experiment)
    wanted = {"download-h1-c1", "gzip-h1", "slow-h1", "slow-h2", "gzip-h2"}
    selected = list({task["case"]["name"]: task["case"] for task in original["plan"] if task["case"]["name"] in wanted}.values())
    assert len(selected) == 5
    control = engine.fastls_input("installed")
    candidate = candidate_input(args.candidate_root)
    assert candidate == original["inputs"]["candidate"] and control == original["inputs"]["control-a"]
    inputs = dict(zip(ARMS, (control, control, candidate)))
    baseline = args.experiment.resolve().parents[2] / "g0-baseline-20261027/stage3"
    # Read-only checks use the same frozen policy and historic input files.
    before = fingerprints(inputs, args.fixture, baseline / "regression-policy-v1.json",
                          [baseline / "full-transfer/results.json", baseline / "full-small/results.json"])
    assert not before["harness"]["dirty_files"]
    for name, value in original["fingerprints_before"]["harness_files"].items():
        assert before["harness_files"][name] == value, "original sampler/worker changed"
    plan = schedule(selected)
    report = dict(scope="diagnostic only; original 150-run gate remains unchanged", original_experiment=str(args.experiment),
                  original_verdict=original["evaluation"]["status"], inputs=inputs, fingerprints_before=before,
                  plan=plan, cpu_cycle_units="raw process CPU cycles; no frequency conversion", runs=[])
    save(output / "plan.json", {key:value for key,value in report.items() if key != "runs"})
    engine.snapshot = snapshot_with_cycles
    with (output / "fixture.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(args.fixture)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            lines=queue.Queue()
            thread=threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True)
            thread.start()
            fixture=json.loads(lines.get(timeout=20))
            thread.join(2)
            for number, task in enumerate(plan):
                spec=dict(case=task["case"],client="fastls",round=task["round"],fixture=fixture,run_id=uuid.uuid4().hex,
                          fastls_input=inputs[task["variant"]],seconds=30,warmup_seconds=.05,hold_ms=5000,slow_bytes_per_second=16*1048576)
                save(output / "progress.json",dict(completed=number,total=len(plan),current=task,running=True))
                try:
                    row=engine.execute(spec,process,output)
                    resources=row["resources"]
                    assert {p["pid"] for p in resources["baseline"]["processes"]} == {p["pid"] for p in resources["finished"]["processes"]}
                    row["diagnostic_cycles"]=resources["finished"]["cpu_cycles"]-resources["baseline"]["cpu_cycles"]
                    assert row["diagnostic_cycles"]>0
                except Exception as error:
                    row=dict(case=task["case"],client="fastls",round=task["round"],valid=False,error=repr(error))
                row.update(variant=task["variant"],run_id=spec["run_id"])
                report["runs"].append(row)
                save(output / "results.json",report)
                print(json.dumps(dict(completed=number+1,total=len(plan),case=task["case"]["name"],variant=task["variant"],valid=row["valid"])),flush=True)
        finally:
            process.stdin.close()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            process.stdout.close()
    after=fingerprints(inputs,args.fixture,baseline / "regression-policy-v1.json",[baseline / "full-transfer/results.json",baseline / "full-small/results.json"])
    report.update(fingerprints_after=after,integrity=before==after,summary=summarize(report["runs"]))
    report["success"]=report["integrity"] and len(report["runs"])==75 and all(row["valid"] for row in report["runs"])
    save(output / "results.json",report)
    save(output / "summary.json",report["summary"])
    save(output / "progress.json",dict(completed=len(report["runs"]),total=len(plan),running=False,success=report["success"]))
    print(json.dumps(dict(success=report["success"],runs=len(report["runs"]))))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
