"""Retry only a classified transient signing failure, never a whole release."""
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request


def recovery_plan(run, jobs, log, head):
    if not (run.get('repository',{}).get('full_name')=='FastTLS/fastls-ffi'
            and run.get('event')=='workflow_dispatch' and run.get('head_branch')=='main'
            and run.get('head_sha')==head and re.fullmatch('[0-9a-f]{40}',head)
            and run.get('path')=='.github/workflows/npm-publish.yml'
            and run.get('status')=='completed' and run.get('conclusion')=='failure'
            and 1 <= run.get('run_attempt',0) < 3):
        raise ValueError('Untrusted, changed, completed or exhausted publication run')
    failed=[j for j in jobs if j.get('conclusion')=='failure']
    if len(failed)!=1 or failed[0].get('name')!='publish':
        raise ValueError('Only the failed publish job can be recovered')
    steps=failed[0].get('steps',[])
    errors=[s.get('name') for s in steps if s.get('conclusion')=='failure']
    if errors!=['Sign original archive promotion provenance'] or not any(
            s.get('name')=='Publish native packages first and main package last' and s.get('conclusion')=='success' for s in steps):
        raise ValueError('Upload/authentication/source failures require diagnosis')
    if not re.search(r'error fetching tlog entry - \((404|408|429|500|502|503|504)\)',log):
        raise ValueError('Failure is not a recognized transient transparency-log read')
    return dict(schema=1,run_id=run['id'],previous_attempt=run['run_attempt'],source=head,
                retry='failed-jobs-only',reason='transient transparency-log read',
                log_sha256=hashlib.sha256(log.encode()).hexdigest(),
                uploads='Rerun publisher independently verifies all six exact packages and trusted sources before skipping uploads.')


def main():
    repository='FastTLS/fastls-ffi'; run_id=os.environ['RECOVER_RUN_ID']
    if not re.fullmatch('[1-9][0-9]*',run_id):raise ValueError('Exact run id required')
    def api(path,method=None,raw=False):
        request=urllib.request.Request('https://api.github.com/repos/'+repository+path,
            headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json',
                     'User-Agent':'bounded-publication-recovery'},method=method)
        with urllib.request.urlopen(request,timeout=30) as response:
            body=response.read()
            return body.decode('utf-8') if raw else json.loads(body) if body else None
    run=api('/actions/runs/'+run_id)
    data=api('/actions/runs/'+run_id+'/jobs?per_page=100')
    if data['total_count']!=len(data['jobs']):raise ValueError('Incomplete job evidence')
    failed=[j for j in data['jobs'] if j.get('conclusion')=='failure']
    if len(failed)!=1:raise ValueError('Ambiguous failed jobs')
    log=api('/actions/jobs/'+str(failed[0]['id'])+'/logs',raw=True)
    plan=recovery_plan(run,data['jobs'],log,os.environ['GITHUB_SHA'])
    # Persist the classification before mutation. Raw logs and credentials stay private.
    Path('npm-recovery.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
    if os.environ.get('RECOVER_EXECUTE')=='true':
        api('/actions/runs/'+run_id+'/rerun-failed-jobs',method='POST')
    print(json.dumps(plan))


if __name__=='__main__':main()
