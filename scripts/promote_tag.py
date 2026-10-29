"""Publish original, fully tested tag packages after skipped delivery jobs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'node/scripts'))
from promotion_source import PUBLISHING_TOOLS

PYPI_TOOLS = {'scripts/promote_tag.py', 'tests/publish_promotion_checks.py'}
INPUT = ("      promote_tag_packages:\n"
         "        description: Promote original checked tag packages when delivery jobs were skipped\n"
         "        type: boolean\n"
         "        default: false\n")
PUSH = "  push:\n    branches: ['**']\n    tags: ['v*']\n"
PRODUCTION_JOBS = {'dependencies', 'native', 'tests', 'extended', 'required', 'node', 'node-soak', 'node-candidates'}


def workflow_inputs_unchanged(before, after):
    def split(text):
        prefix, rest = text.split('\njobs:\n', 1)
        jobs, events = rest.split("\n'on':\n", 1)
        chunks = re.split(r'(?m)^  ([a-z][a-z0-9-]*):\n', jobs)
        return prefix, dict(zip(chunks[1::2], chunks[2::2])), events
    try:
        prefix_a, jobs_a, events_a = split(before)
        prefix_b, jobs_b, events_b = split(after)
    except ValueError:
        return False
    return (prefix_a == prefix_b and jobs_a.keys() == jobs_b.keys()
        and PRODUCTION_JOBS <= jobs_a.keys()
        and all(jobs_a[name] == jobs_b[name] for name in PRODUCTION_JOBS)
        and events_b.replace(INPUT, '').replace(PUSH, '  push: null\n') == events_a.replace(INPUT, '').replace(PUSH, '  push: null\n'))


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def validate_source(producer, publisher):
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (producer, publisher)):
        raise ValueError('Immutable source commits required')
    subprocess.run(['git', 'merge-base', '--is-ancestor', producer, publisher], cwd=ROOT, check=True)
    changed = git('diff', '--name-only', '--no-renames', producer, publisher).splitlines()
    allowed = PUBLISHING_TOOLS | PYPI_TOOLS | {'.github/workflows/ci.yml'}
    if not set(changed) <= allowed:
        raise ValueError('Production, dependency, version or build inputs changed')
    workflow = '.github/workflows/ci.yml'
    if workflow in changed and not workflow_inputs_unchanged(git('show', producer + ':' + workflow), git('show', publisher + ':' + workflow)):
        raise ValueError('Native test configuration or workflow triggers changed outside publication scope')
    return changed


def validate():
    from resume_pypi import github_api as api, REPOSITORY
    from release_reuse import verify, equivalent_resource_inputs
    from ci import TARGETS
    run_id, version = os.environ['PYPI_SOURCE_RUN'], os.environ['PYPI_VERSION']
    if not re.fullmatch(r'[1-9][0-9]*', run_id) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Numeric run ID and final version required')
    run = api('actions/runs/' + run_id)
    tag = 'v' + version
    if not (run['status'] == 'completed' and run['conclusion'] == 'success' and run['head_branch'] == tag
            and run['event'] == 'push' and run['path'] == '.github/workflows/ci.yml'
            and run['repository']['full_name'] == REPOSITORY and run['head_repository']['full_name'] == REPOSITORY):
        raise ValueError('Expected successful original tag-push source run')
    source = run['head_sha']
    ref = api('git/ref/tags/' + tag)['object']
    for _ in range(3):
        if ref['type'] != 'tag': break
        ref = api('git/tags/' + ref['sha'])['object']
    if ref['type'] != 'commit' or ref['sha'] != source:
        raise ValueError('Tag differs from original tested source')
    inventory = api(f'actions/runs/{run_id}/jobs?per_page=100&filter=latest')
    if inventory['total_count'] != len(inventory['jobs']): raise ValueError('Incomplete source job inventory')
    jobs = {job['name']: job for job in inventory['jobs']}
    required = {'dependencies', 'All native tests'}
    for target in TARGETS[:5]:
        label = f"{target['target']}-go{target['go']}-py{target['python']}"
        required.update(('Build / ' + label, 'Python checks / ' + label))
    if any(jobs.get(name, {}).get('conclusion') != 'success' for name in required):
        raise ValueError('Every original platform and aggregate gate must pass')
    if any(jobs.get(name, {}).get('conclusion') != 'skipped' for name in
            ('Validate PyPI package set', 'Publish GitHub release', 'Publish fastls to PyPI')):
        raise ValueError('This promotion mode only repairs skipped delivery jobs')
    mains = api('actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=' + source + '&per_page=20')['workflow_runs']
    main = next(r for r in mains if r['status'] == 'completed' and r['conclusion'] == 'success')
    verify(main, api(f"actions/runs/{main['id']}/jobs?per_page=100")['jobs'], REPOSITORY, source, platform_only=True)
    resource = json.loads(git('show', source + ':ci/resource-reuse.json'))
    if equivalent_resource_inputs(resource['source'], source) is None: raise ValueError('Resource inputs changed')
    verify(api(f"actions/runs/{resource['run_id']}"), api(f"actions/runs/{resource['run_id']}/jobs?per_page=100")['jobs'],
           REPOSITORY, resource['source'], resource_only=True)
    publisher = os.environ['GITHUB_SHA']
    changed = validate_source(source, publisher)
    record = dict(schema=1, producer_revision=source, publisher_revision=publisher, tag=tag, version=version,
        tag_run_id=int(run_id), node_delivery_run_id=main['id'], resource_run_id=resource['run_id'],
        publication_run_id=os.environ['GITHUB_RUN_ID'], changed_publishing_tools=changed,
        production_and_native_test_inputs_unchanged=True, native_bytes='Original tag artifacts; no rebuilding.')
    Path('promotion.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record))


def assemble():
    from prepare_pypi import prepare, PLATFORMS
    from resume_pypi import verify_files
    record = json.loads(Path('promotion.json').read_text())
    candidates, dist, assets = Path('candidates'), Path('dist'), Path('release-assets')
    prepare(candidates, dist, record['version'], True)
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dist.iterdir()}
    verify_files(dist, record['version'], record['producer_revision'], checksums)
    assets.mkdir()
    for path in dist.iterdir(): shutil.copy2(path, assets / path.name)
    for target in PLATFORMS:
        matches = list(candidates.glob(f'*-{target}-go1.26.10-py3.11'))
        if len(matches) != 1: raise ValueError('Expected canonical original platform candidate')
        for stem in ('release-manifest', 'build-info', 'native-audit'):
            shutil.copy2(matches[0] / (stem + '.json'), assets / (stem + '-' + target + '.json'))
    record['package_sha256'] = checksums
    Path('promotion.json').write_text(json.dumps(record, indent=2)+'\n')
    shutil.copy2('promotion.json', assets / 'promotion-manifest.json')
    (assets / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
        for p in sorted(assets.iterdir()) if p.is_file()))


def stage_upload():
    record = json.loads(Path('promotion.json').read_text())
    expected = record['package_sha256']
    try:
        with urllib.request.urlopen('https://pypi.org/pypi/fastls/' + record['version'] + '/json', timeout=30) as response:
            existing = {item['filename']: item for item in json.load(response)['urls']}
    except urllib.error.HTTPError as error:
        if error.code != 404: raise
        existing = {}
    if not set(existing) <= expected.keys(): raise ValueError('Unexpected existing registry files')
    upload = Path('upload-dist'); upload.mkdir()
    for name, digest in expected.items():
        if name in existing:
            if existing[name]['digests']['sha256'] != digest or existing[name]['yanked']:
                raise ValueError('Registry already contains different or yanked bytes')
        else:
            shutil.copy2(Path('dist') / name, upload / name)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('upload=' + ('true' if any(upload.iterdir()) else 'false') + '\n')


if __name__ == '__main__':
    {'validate': validate, 'assemble': assemble, 'stage-upload': stage_upload}[sys.argv[1]]()
