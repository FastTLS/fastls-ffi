"""Collect npm archives from the exact successful main CI run, without rebuilding."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile

root = Path(__file__).resolve().parents[2]
run_file, candidates, destination = map(Path, sys.argv[1:4])
publishable = '--publishable' in sys.argv[4:]
run = json.loads(run_file.read_text())
sha = os.environ['GITHUB_SHA']
pkg = json.loads((root/'node/package.json').read_text())
if not (run['status'] == 'completed' and run['conclusion'] == 'success' and run['head_sha'] == sha
        and run['head_branch'] == 'main' and run['event'] == 'push' and run['path'] == '.github/workflows/ci.yml'):
    raise ValueError('Expected successful same-commit main CI run')
targets = {('win32', 'x64'), ('linux', 'x64'), ('linux', 'arm64'), ('darwin', 'x64'), ('darwin', 'arm64')}
names = {f"@fastls/fastls-{os_}-{cpu}" + ('-glibc' if os_ == 'linux' else '') for os_, cpu in targets}
dependencies = {name: pkg['version'] for name in names}
baseline = json.loads((root/'node/tests/python-baseline.json').read_text())
expected_cases = {filename+'::'+item.name for filename in baseline['files']
                  for item in ast.walk(ast.parse((root/'tests'/filename).read_text(encoding='utf-8')))
                  if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith('test_')}
destination.mkdir(parents=True, exist_ok=True)
records, seen, blockers = [], set(), set()
main = None
for directory in sorted(candidates.glob('fastls-*-go1.26.10-py3.11-node24')):
    info_path = directory/'dist/npm/npm-package-info.json'
    info = json.loads(info_path.read_text())
    target = info['platform'], info['arch']
    if target not in targets or target in seen or info['revision'] != sha or not info['checks'] or not all(info['checks'].values()):
        raise ValueError('Missing, duplicate or unverified npm platform candidate')
    seen.add(target)
    candidate = json.loads((directory/'dist/node-package-info.json').read_text())
    if candidate['revision'] != sha or info['nativeManifest'] != candidate['native']['manifest'] or not all(candidate['checks'].values()):
        raise ValueError('npm candidate is not linked to installed parity evidence')
    native = info['nativeManifest']
    if len(info.get('evidence', [])) != 2:
        raise ValueError('Missing npm installed suite evidence')
    suites = []
    for filename in info['evidence']:
        if Path(filename).name != filename:
            raise ValueError('Invalid evidence filename')
        suites.append(json.loads((info_path.parent/filename).read_text()))
    parity = [suite for suite in suites if 'cases' in suite]
    sdk = [suite for suite in suites if 'tests' in suite]
    if (len(parity) != 1 or len(sdk) != 1 or len(parity[0]['cases']) != len(expected_cases)
            or {case['python'] for case in parity[0]['cases']} != expected_cases
            or not all(case['passed'] for case in parity[0]['cases'])
            or len(sdk[0]['tests']) != 14 or not all(case['passed'] for case in sdk[0]['tests'])):
        raise ValueError('Incomplete npm installed parity or SDK suite')
    blockers.update(native['release']['licenseReviewRequired'])
    for kind in ['main', 'native']:
        record = info[kind]
        path = info_path.parent/record['file']
        if path.name != record['file'] or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('npm archive checksum mismatch')
        with tarfile.open(path) as archive:
            metadata = json.load(archive.extractfile('package/package.json'))
            if metadata != record['metadata'] or metadata['version'] != pkg['version'] or metadata.get('private') or metadata.get('scripts'):
                raise ValueError('Unexpected npm package metadata')
            if archive.extractfile('package/LICENSE').read().replace(b'\r\n', b'\n') != (root/'LICENSE').read_bytes().replace(b'\r\n', b'\n'):
                raise ValueError('Package license text mismatch')
            if kind == 'main':
                if metadata['name'] != pkg['name'] or metadata['optionalDependencies'] != dependencies:
                    raise ValueError('Incorrect npm optional dependencies')
            else:
                expected = f'@fastls/fastls-{target[0]}-{target[1]}' + ('-glibc' if target[0] == 'linux' else '')
                if metadata['name'] != expected or metadata['os'] != [target[0]] or metadata['cpu'] != [target[1]]:
                    raise ValueError('Incorrect native platform metadata')
                if target[0] == 'linux' and metadata.get('libc') != ['glibc']:
                    raise ValueError('Missing glibc gate')
                embedded = json.load(archive.extractfile('package/native/manifest.json'))
                if embedded != native or hashlib.sha256(archive.extractfile('package/native/'+native['file']).read()).hexdigest() != native['sha256']:
                    raise ValueError('Incorrect native contents')
        if kind == 'native' or target == ('linux', 'x64'):
            shutil.copyfile(path, destination/path.name)
            item = {k: record[k] for k in ['name', 'version', 'file', 'sha256']}
            if kind == 'native':
                records.append(item)
            else:
                main = item
if seen != targets or main is None:
    raise ValueError('All five verified npm platform candidates are required')
if publishable and blockers:
    raise ValueError('Resolve native dependency license review before npm publication: '+', '.join(sorted(blockers)))
report = {'schema': 1, 'revision': sha, 'run_id': run['id'], 'version': pkg['version'],
          'license_review_required': sorted(blockers), 'publish_order': records+[main]}
(destination/'npm-release-manifest.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
