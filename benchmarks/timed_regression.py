"""Independent v3 certification: v2 performance rules plus frozen timing eligibility."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import subprocess
import threading
import uuid

from common import cases
from continuous_regression import evaluate as evaluate_v2, window, MINIMUM_POOLED_CPU_SECONDS
from regression import (ARMS, candidate_input, fingerprints, load, save, schedule, sha)
from report import render
from run import execute, fastls_input
from worker import expected_digest

PROTOCOL = 'g1-continuous-cpu-v3-timed'
# Selected before any v3 measurement, from R1's independent controls (maximum
# window excess 1.235s, stop lag .866s and marker queue delay .916s). These
# qualify observation quality; the original performance policy is unchanged.
TIMING_POLICY = dict(maximum_window_excess_seconds=2.0,
    maximum_window_excess_fraction=.10, maximum_boundary_lag_seconds=2.0,
    maximum_marker_queue_delay_seconds=2.0, maximum_snapshot_cost_seconds=2.0,
    maximum_client_sample_gap_seconds=2.0)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timing_check(row):
    """Missing, inconsistent or delayed timing is ineligible, never substituted."""
    failures = []
    values = {}
    try:
        resources = row['resources']; diagnostics = row['diagnostics']
        markers = row['markers']; costs = diagnostics['snapshot_costs']
        worker = row['metrics']['elapsed_seconds']
        baseline = resources['baseline']['at']; finish = resources['finished']['at']
        if not all(finite(x) for x in (worker, baseline, finish)) or worker <= 0 or finish <= baseline:
            raise ValueError('invalid measurement timestamps')
        boundary = {}
        for stage in ('measure_start', 'measure_stop'):
            found = [m for m in markers if m['stage'] == stage]
            if len(found) != 1 or found[0].get('after_failure'):
                raise ValueError('missing or duplicate lifecycle boundary')
            m = found[0]
            stamps = [m[k] for k in ('worker_at', 'controller_received_at', 'controller_handled_at')]
            if not all(finite(x) for x in stamps) or stamps != sorted(stamps):
                raise ValueError('inconsistent lifecycle clock order')
            observed = baseline if stage == 'measure_start' else finish
            if observed < stamps[-1]: raise ValueError('snapshot precedes boundary handling')
            boundary[stage] = m
            lag = observed - m['worker_at']; values[stage + '_lag_seconds'] = lag
            if lag > TIMING_POLICY['maximum_boundary_lag_seconds']:
                failures.append(stage + ' boundary observation delayed')
        if boundary['measure_stop']['worker_at'] < boundary['measure_start']['worker_at'] + worker:
            raise ValueError('worker elapsed exceeds lifecycle window')
        external = finish - baseline; excess = external - worker
        values.update(worker_seconds=worker, observer_seconds=external, window_excess_seconds=excess)
        if excess < 0 or excess > TIMING_POLICY['maximum_window_excess_seconds'] or excess > worker * TIMING_POLICY['maximum_window_excess_fraction']:
            failures.append('observer/worker window mismatch')
        queues = [m['controller_queue_delay_seconds'] for m in markers]
        if not queues or not all(finite(x) and x >= 0 for x in queues):
            raise ValueError('missing marker queue timing')
        for m in markers:
            if not math.isclose(m['controller_handled_at'] - m['controller_received_at'], m['controller_queue_delay_seconds'], abs_tol=1e-8):
                raise ValueError('inconsistent marker queue timing')
        values['maximum_marker_queue_delay_seconds'] = max(queues)
        if max(queues) > TIMING_POLICY['maximum_marker_queue_delay_seconds']:
            failures.append('marker queue delayed')
        durations = [c['seconds'] for c in costs]
        if not durations or not all(finite(x) and x >= 0 for x in durations):
            raise ValueError('missing snapshot costs')
        if any(sum(c['role'] == role for c in costs) != 1 for role in ('client_start', 'server_start', 'client_stop', 'server_stop', 'client_closed')):
            raise ValueError('missing or duplicate boundary snapshot')
        values['maximum_snapshot_cost_seconds'] = max(durations)
        if max(durations) > TIMING_POLICY['maximum_snapshot_cost_seconds']:
            failures.append('snapshot call delayed')
        points = [baseline] + [s['client']['at'] for s in resources['samples']] + [finish]
        if len(points) < 3 or not all(finite(x) for x in points) or any(b <= a for a, b in zip(points, points[1:])):
            raise ValueError('missing or unordered client samples')
        gap = max(b - a for a, b in zip(points, points[1:])); values['maximum_client_sample_gap_seconds'] = gap
        if gap > TIMING_POLICY['maximum_client_sample_gap_seconds']:
            failures.append('client sampling gap too large')
        cleanup = diagnostics['cleanup']
        if diagnostics['controller_errors'] or any(cleanup[k] for k in ('terminated_pids', 'still_alive_pids', 'errors')):
            failures.append('controller or cleanup failed')
    except (KeyError, TypeError, ValueError, IndexError) as error:
        failures.append('incomplete/inconsistent timing evidence: ' + str(error))
    return dict(eligible=not failures, failures=failures, values=values)


def evaluate(rows, selected, policy, integrity, plan, *, window_for=window, protocol=PROTOCOL):
    result = evaluate_v2(rows, selected, policy, integrity, window_for=window_for)
    expected = Counter((t['case']['name'], t['variant'], t['round'], t['run_id']) for t in plan)
    actual = Counter((r['case']['name'], r['variant'], r['round'], r['run_id']) for r in rows)
    sequence_ok = [(r['case'], r['variant'], r['round'], r['run_id']) for r in rows] == [(t['case'], t['variant'], t['round'], t['run_id']) for t in plan]
    plan_ok = expected == actual and sequence_ok and len({r['run_id'] for r in rows}) == len(rows)
    timed = [dict(run_id=r['run_id'], case=r['case']['name'], variant=r['variant'], round=r['round'], **timing_check(r)) for r in rows]
    for case in result['cases']:
        bad = [r for r in timed if r['case'] == case['case'] and not r['eligible']]
        reasons = case['eligibility_failures']
        reasons.extend(f"{r['variant']} round {r['round']}: {why}" for r in bad for why in r['failures'])
        if not plan_ok: reasons.append('frozen run plan mismatch')
        case['timing_eligible'] = not bad
        case['eligible'] = not reasons
        if reasons:
            candidate = case['candidate']
            candidate.setdefault('diagnostic_status', candidate['status'])
            candidate['status'] = 'ineligible'
    result.update(protocol=protocol, timing_policy=TIMING_POLICY, plan_matches=plan_ok, timing=timed)
    result['status'] = 'ineligible' if any(not c['eligible'] for c in result['cases']) else 'passed' if all(c['candidate']['status'] == 'passed' for c in result['cases']) else 'failed'
    result['success'] = result['status'] == 'passed'
    return result


def main(*, window_for=window, evaluate_plan=evaluate, protocol=PROTOCOL, observation_basis=None, description=None):
    p = argparse.ArgumentParser(description=description or __doc__)
    for name in ('candidate-root', 'baseline-dir', 'fixture', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args(); out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    baseline = args.baseline_dir.resolve(); policyfile = baseline / 'regression-policy-v1.json'; policy = load(policyfile)
    historyfiles = [baseline / 'full-transfer/results.json', baseline / 'full-small/results.json']
    names = {r['case']['name'] for f in historyfiles for r in load(f)['runs'] if r['client'] == 'fastls'}
    selected = [c for c in cases('full') if c['name'] in names]
    assert len(selected) == 10
    inputs = {'control-a': fastls_input('installed'), 'control-b': fastls_input('installed'), 'candidate': candidate_input(args.candidate_root)}
    fixture = args.fixture.resolve(); before = fingerprints(inputs, fixture, policyfile, historyfiles)
    assert not before['harness']['dirty_files'], 'commit source before freezing plan'
    plan = [dict(task, run_id=uuid.uuid4().hex) for task in schedule(selected, 5)]
    record = dict(protocol=protocol, created_at=datetime.now(timezone.utc).isoformat(),
        design='150 predetermined interleaved fresh-process runs; no retry, replacement or early stopping',
        scope='Windows AMD64 CPython 3.11; portable H1/H2 only',
        windows={c['name']: window_for(c) for c in selected}, timing_policy=TIMING_POLICY,
        timing_basis=observation_basis or 'R1 independent control observations; qualification frozen before the first v3 worker',
        continuous_transfers=True, rounds=5, minimum_pooled_cpu_seconds=MINIMUM_POOLED_CPU_SECONDS,
        cpu_rule='unchanged external process-tree CPU seconds summed over all five rounds / verified successful bytes',
        baseline_rule='new bidirectional A/A calibration; old single-transfer absolute baselines not interchangeable',
        historical_status='v1 and v2 remain retained; no failed round replaced',
        policy=policy, policy_sha256=sha(policyfile), inputs=inputs, fingerprints_before=before, plan=plan,
        expected_body_sha256={c['name']: expected_digest(c['size']) for c in selected}, runs=[])
    save(out / 'plan.json', {k: v for k, v in record.items() if k != 'runs'})
    record['plan_sha256'] = sha(out / 'plan.json'); save(out / 'results.json', record)
    with (out / 'fixture.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([str(fixture)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            lines = queue.Queue(); reader = threading.Thread(target=lambda: lines.put(server.stdout.readline()), daemon=True); reader.start()
            fixture_info = json.loads(lines.get(timeout=20)); reader.join(2)
            for index, task in enumerate(plan):
                case = task['case']; seconds = window_for(case)
                spec = dict(case=case, client='fastls', round=task['round'], fixture=fixture_info, run_id=task['run_id'],
                    fastls_input=inputs[task['variant']], seconds=seconds, continuous_transfers=True,
                    warmup_seconds=10 if case['workload'] == 'small' else .05, hold_ms=5000, slow_bytes_per_second=16*1048576)
                save(out / 'progress.json', dict(completed=index, total=len(plan), current=task, running=True))
                try: row = execute(spec, server, out)
                except Exception as error:
                    row = dict(case=case, client='fastls', round=task['round'], valid=False, exit_code=None, error=repr(error), metrics={}, resources={})
                row.update(variant=task['variant'], run_id=task['run_id'], continuous_transfers=True, requested_seconds=seconds)
                record['runs'].append(row); save(out / 'results.json', record)
                print(json.dumps(dict(completed=index+1, total=len(plan), case=case['name'], variant=task['variant'], round=task['round'], valid=row['valid'], timing_eligible=timing_check(row)['eligible'])), flush=True)
        finally:
            server.stdin.close()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
            server.stdout.close()
    after = fingerprints(inputs, fixture, policyfile, historyfiles)
    record['fingerprints_after'] = after
    record['integrity'] = before == after and candidate_input(args.candidate_root) == inputs['candidate'] and record['plan_sha256'] == sha(out / 'plan.json')
    record['evaluation'] = evaluate_plan(record['runs'], selected, policy, record['integrity'], plan)
    save(out / 'results.json', record); save(out / 'evaluation.json', record['evaluation'])
    save(out / 'progress.json', dict(completed=len(plan), total=len(plan), running=False, verdict=record['evaluation']['status']))
    save(out / 'controls-baseline.json', dict(protocol=protocol,
        calibration_valid=record['integrity'] and record['evaluation']['plan_matches'] and all(c['timing_eligible'] and all(a['status']=='passed' for a in c['aa']) for c in record['evaluation']['cases']),
        cases=[dict(case=c['case'], control_a=c['summaries']['control-a'], control_b=c['summaries']['control-b'], aa=c['aa'], timing_eligible=c['timing_eligible']) for c in record['evaluation']['cases']],
        results_sha256=sha(out / 'results.json')))
    render([dict(environment_id=a, profile=protocol, rounds=5, fastls_input=inputs[a], source_changed_during_run=not record['integrity'], runs=[r for r in record['runs'] if r['variant']==a]) for a in ARMS], out / 'report.html')
    print(json.dumps(dict(protocol=protocol, status=record['evaluation']['status'], runs=len(plan))), flush=True)
    return 0 if record['evaluation']['success'] else 1


if __name__ == '__main__': raise SystemExit(main())
