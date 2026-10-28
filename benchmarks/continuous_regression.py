"""Independent v2 continuous-transfer CPU protocol; never rewrites v1 results."""
import argparse
from datetime import datetime,timezone
import json,math,queue,subprocess,threading,uuid
from pathlib import Path
from common import cases
from regression import (ARMS,aggregate,candidate_input,compare,fingerprints,invalid_reason,
                        load,save,schedule,sha)
from run import ROOT,execute,fastls_input
from report import render
from worker import expected_digest

PROTOCOL='g1-continuous-cpu-v2'
MINIMUM_POOLED_CPU_SECONDS=1.0

def window(case):
    return 30 if case['workload']=='small' else 64 if case['workload']=='slow' else 16

def evaluate(rows,selected,policy,integrity,*,window_for=window):
    outcomes=[]
    for case in selected:
        groups={a:[r for r in rows if r['variant']==a and r['case']['name']==case['name']] for a in ARMS}
        reasons=[]
        if not integrity:reasons.append('inputs changed during measurement')
        for arm,group in groups.items():
            if len(group)!=5 or {r['round'] for r in group}!=set(range(1,6)):
                reasons.append(arm+': missing or duplicate round')
            for r in group:
                why=invalid_reason(r)
                if why:reasons.append(arm+': '+why)
                elapsed=r.get('metrics',{}).get('elapsed_seconds')
                if elapsed is None or not math.isfinite(elapsed) or elapsed<window_for(case):
                    reasons.append(arm+': shortened measurement window')
                if not r.get('continuous_transfers') or r.get('requested_seconds')!=window_for(case):
                    reasons.append(arm+': incorrect measurement protocol')
            cpu=[r.get('resources',{}).get('cpu_seconds') for r in group]
            if any(x is None or not math.isfinite(x) or x<0 for x in cpu) or sum(x or 0 for x in cpu)<MINIMUM_POOLED_CPU_SECONDS:
                reasons.append(arm+': insufficient observed CPU seconds')
        summaries={a:aggregate(g) for a,g in groups.items()}
        if any(x is None for x in summaries.values()):reasons.append('missing or invalid metric')
        slow=case['workload']=='slow'
        aa=[compare(summaries['control-a'],summaries['control-b'],policy,slow),
            compare(summaries['control-b'],summaries['control-a'],policy,slow)]
        if any(x['status']!='passed' for x in aa):reasons.append('bidirectional A/A controls unstable')
        candidate=compare(summaries['control-a'],summaries['candidate'],policy,slow)
        if reasons:
            candidate['diagnostic_status']=candidate['status'];candidate['status']='ineligible'
        outcomes.append(dict(case=case['name'],eligible=not reasons,eligibility_failures=reasons,
                             summaries=summaries,aa=aa,candidate=candidate))
    status='ineligible' if any(not x['eligible'] for x in outcomes) else 'passed' if all(x['candidate']['status']=='passed' for x in outcomes) else 'failed'
    return dict(protocol=PROTOCOL,status=status,success=status=='passed',cases=outcomes,
                expected_runs=len(selected)*15,actual_runs=len(rows))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate-root',type=Path,required=True)
    p.add_argument('--baseline-dir',type=Path,required=True)
    p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    baseline=args.baseline_dir.resolve();policyfile=baseline/'regression-policy-v1.json';policy=load(policyfile)
    historyfiles=[baseline/'full-transfer/results.json',baseline/'full-small/results.json']
    names={r['case']['name'] for f in historyfiles for r in load(f)['runs'] if r['client']=='fastls'}
    selected=[c for c in cases('full') if c['name'] in names]
    assert len(selected)==10
    inputs={'control-a':fastls_input('installed'),'control-b':fastls_input('installed'),
            'candidate':candidate_input(args.candidate_root)}
    fixture=args.fixture.resolve();before=fingerprints(inputs,fixture,policyfile,historyfiles)
    assert not before['harness']['dirty_files'],'commit the measurement source before freezing the plan'
    plan=schedule(selected,5)
    record=dict(protocol=PROTOCOL,created_at=datetime.now(timezone.utc).isoformat(),
        design='150 predetermined interleaved runs; fresh process per arm/round; no retry or early stopping',
        windows={c['name']:window(c) for c in selected},continuous_transfers=True,
        minimum_pooled_cpu_seconds=MINIMUM_POOLED_CPU_SECONDS,rounds=5,
        cpu_rule='unchanged external process-tree CPU seconds summed across five rounds / verified successful bytes',
        baseline_rule='new continuous-workload A/A calibration; original single-transfer absolute baselines are not interchangeable',
        original_v1_status='retained as ineligible with one independently failed CPU case; never rewritten',
        policy=policy,policy_sha256=sha(policyfile),inputs=inputs,fingerprints_before=before,plan=plan,
        expected_body_sha256={c['name']:expected_digest(c['size']) for c in selected},runs=[])
    save(out/'plan.json',{k:v for k,v in record.items() if k!='runs'});save(out/'results.json',record)
    with (out/'fixture.log').open('w',encoding='utf-8') as log:
        server=subprocess.Popen([str(fixture)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            lines=queue.Queue();reader=threading.Thread(target=lambda:lines.put(server.stdout.readline()),daemon=True);reader.start()
            fixture_info=json.loads(lines.get(timeout=20));reader.join(2)
            for index,task in enumerate(plan):
                case=task['case'];seconds=window(case)
                spec=dict(case=case,client='fastls',round=task['round'],fixture=fixture_info,run_id=uuid.uuid4().hex,
                    fastls_input=inputs[task['variant']],seconds=seconds,continuous_transfers=True,
                    warmup_seconds=10 if case['workload']=='small' else .05,hold_ms=5000,slow_bytes_per_second=16*1048576)
                save(out/'progress.json',dict(completed=index,total=150,current=task,running=True))
                try:result=execute(spec,server,out)
                except Exception as error:
                    result=dict(case=case,client='fastls',round=task['round'],valid=False,exit_code=None,
                                error=repr(error),metrics={},resources={})
                result.update(variant=task['variant'],run_id=spec['run_id'],continuous_transfers=True,requested_seconds=seconds)
                record['runs'].append(result);save(out/'results.json',record)
                print(json.dumps(dict(completed=index+1,total=150,case=case['name'],variant=task['variant'],valid=result['valid'])),flush=True)
        finally:
            server.stdin.close()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
            server.stdout.close()
    after=fingerprints(inputs,fixture,policyfile,historyfiles)
    record['fingerprints_after']=after;record['integrity']=before==after and candidate_input(args.candidate_root)==inputs['candidate']
    record['evaluation']=evaluate(record['runs'],selected,policy,record['integrity'])
    save(out/'results.json',record);save(out/'evaluation.json',record['evaluation'])
    save(out/'progress.json',dict(completed=150,total=150,running=False,verdict=record['evaluation']['status']))
    save(out/'controls-baseline.json',dict(protocol=PROTOCOL,inputs={a:inputs[a] for a in ARMS[:2]},
        cases=[dict(case=x['case'],control_a=x['summaries']['control-a'],control_b=x['summaries']['control-b'],aa=x['aa']) for x in record['evaluation']['cases']],
        calibration_valid=all(all(a['status']=='passed' for a in x['aa']) for x in record['evaluation']['cases']),
        results_sha256=sha(out/'results.json')))
    render([dict(environment_id=a,profile='continuous-v2',rounds=5,fastls_input=inputs[a],
                 source_changed_during_run=not record['integrity'],runs=[r for r in record['runs'] if r['variant']==a]) for a in ARMS],out/'report.html')
    print(json.dumps(dict(protocol=PROTOCOL,status=record['evaluation']['status'],runs=150)))
    return 0 if record['evaluation']['success'] else 1

if __name__=='__main__':raise SystemExit(main())
