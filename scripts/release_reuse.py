"""Verify immutable resource inputs and the separately published main Node set."""
import json
from pathlib import Path
import re
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
INFRASTRUCTURE_ONLY = {'RELEASE_NOTES.md', 'node/scripts/check-parity.mjs', 'ci/resource-reuse.json',
    'scripts/release_reuse.py', 'scripts/test_plan.py', 'tests/test_release_reuse.py',
    'tests/test_ci.py', 'tests/proxy_lab.py', 'tests/proxy_lab_checks.py'}


def equivalent_resource_inputs(base, revision):
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (base, revision)):
        return None
    if subprocess.run(['git', 'merge-base', '--is-ancestor', base, revision], cwd=ROOT,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        return None
    changed = subprocess.check_output(['git', 'diff', '--name-only', '--no-renames', base, revision], cwd=ROOT, text=True).splitlines()
    return changed if set(changed) <= INFRASTRUCTURE_ONLY else None


def verify(run, jobs, repository, revision, *, resource_only=False, platform_only=False, resource_kinds=None):
    if resource_only and platform_only:
        raise ValueError('Independent evidence scopes cannot be combined')
    accepted_state = run.get('status') == 'completed' and run.get('conclusion') in (('success', 'failure', 'cancelled') if resource_only else ('success',))
    if resource_only and run.get('status') == 'in_progress' and run.get('conclusion') is None:
        accepted_state = True  # Every named prerequisite below must already be completed/successful.
    if not (accepted_state
            and run.get('head_sha') == revision and run.get('head_branch') == 'main'
            and run.get('event') == 'push' and run.get('path') == '.github/workflows/ci.yml'
            and run.get('repository', {}).get('full_name') == repository):
        raise ValueError('Resource reuse requires successful same-commit main CI')
    required = {'dependencies'}
    kinds = ('generic', 'duplex', 'sse', 'proxy', 'ws') if resource_kinds is None else resource_kinds
    if not kinds or len(set(kinds)) != len(kinds) or set(kinds) - {'generic', 'duplex', 'sse', 'proxy', 'ws'}:
        raise ValueError('Unknown or empty resource coverage cannot authorize reuse')
    if not platform_only:
        required.update(('race / linux-amd64', 'soak / linux-amd64'))
        required.update('Resource recovery / ' + name for name in kinds)
    if not resource_only:
        required.add('All native tests')
    from ci import TARGETS
    for target in TARGETS[:5]:
        label = f"{target['target']}-go{target['go']}-py{target['python']}"
        required.add('Build / ' + label)
        if not resource_only:
            required.add('Python checks / ' + label)
        if not resource_only:
            required.update((f"Node checks / {target['target']} / Go {target['go']} / Node 24",
                             'Candidate / ' + label + '-node24'))
    for name in required:
        matches = [job for job in jobs if job.get('name') == name]
        if len(matches) != 1 or matches[0].get('conclusion') != 'success' or matches[0].get('status') != 'completed':
            raise ValueError('Missing successful resource prerequisite: ' + name)
    return {'run_id': run['id'], 'revision': revision, 'url': run['html_url'],
            'individual_resource_checks_only': resource_only,
            'platform_delivery_only': platform_only,
            'scope': ('Same-commit main Node delivery; Python tag bytes are built and installed separately.' if platform_only else
                      'Source-level Linux race and resource scenarios; new native bytes are built and installed separately.')}


def find(env, *, platform_only=False, resource_kinds=None):
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

    def jobs_for(run):
        jobs, page = [], 1
        while True:
            batch = api(f"/actions/runs/{run['id']}/jobs?filter=latest&per_page=100&page={page}")['jobs']
            jobs.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return jobs

    record_path = ROOT / 'ci/resource-reuse.json'
    if not platform_only and record_path.exists():
        record = json.loads(record_path.read_text())
        if record.get('schema') == 1 and isinstance(record.get('run_id'), int):
            changed = equivalent_resource_inputs(record['source'], revision)
            if changed is not None:
                run = api(f"/actions/runs/{record['run_id']}")
                try:
                    reused = verify(run, jobs_for(run), repository, record['source'], resource_only=True)
                except ValueError:
                    # A stale optional ancestor cannot hide an exact successful
                    # main candidate below. Every requested prerequisite still
                    # needs its own completed/successful job evidence.
                    pass
                else:
                    reused.update(consumer_revision=revision, unchanged_except=changed)
                    return reused

    runs = api('/actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=' + revision + '&per_page=20')
    for run in runs['workflow_runs']:
        if run['status'] != 'completed' or run['conclusion'] != 'success':
            continue
        try:
            return verify(run, jobs_for(run), repository, revision, platform_only=platform_only, resource_kinds=resource_kinds)
        except ValueError:
            continue
    return None
