"""Reuse resource checks only from a successful same-commit main CI run."""
import json
import urllib.request


def verify(run, jobs, repository, revision):
    if not (run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and run.get('head_sha') == revision and run.get('head_branch') == 'main'
            and run.get('event') == 'push' and run.get('path') == '.github/workflows/ci.yml'
            and run.get('repository', {}).get('full_name') == repository):
        raise ValueError('Resource reuse requires successful same-commit main CI')
    required = {'All native tests', 'race / linux-amd64', 'soak / linux-amd64'}
    required.update('Resource recovery / ' + name for name in ('generic', 'duplex', 'sse', 'proxy', 'ws'))
    from ci import TARGETS
    for target in TARGETS[:5]:
        label = f"{target['target']}-go{target['go']}-py{target['python']}"
        required.update(('Build / ' + label, 'Python checks / ' + label,
                         f"Node checks / {target['target']} / Go {target['go']} / Node 24",
                         'Candidate / ' + label + '-node24'))
    for name in required:
        matches = [job for job in jobs if job.get('name') == name]
        if len(matches) != 1 or matches[0].get('conclusion') != 'success' or matches[0].get('status') != 'completed':
            raise ValueError('Missing successful resource prerequisite: ' + name)
    return {'run_id': run['id'], 'revision': revision, 'url': run['html_url'],
            'scope': 'Source-level Linux race and resource scenarios; tag native bytes are built and installed separately.'}


def find(env):
    repository, revision = env['GITHUB_REPOSITORY'], env['GITHUB_SHA']
    # This optimization is deliberately restricted to this repository's CI identity.
    if repository != 'FastTLS/fastls-ffi':
        return None
    token = env.get('GH_TOKEN')
    if not token:
        return None

    def api(path):
        request = urllib.request.Request('https://api.github.com/repos/' + repository + path,
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                     'User-Agent': 'fastls-release-reuse'})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    runs = api('/actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=' + revision + '&per_page=20')
    for run in runs['workflow_runs']:
        if run['status'] != 'completed' or run['conclusion'] != 'success':
            continue
        jobs, page = [], 1
        while True:
            batch = api(f"/actions/runs/{run['id']}/jobs?filter=latest&per_page=100&page={page}")['jobs']
            jobs.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        try:
            return verify(run, jobs, repository, revision)
        except ValueError:
            continue
    return None
