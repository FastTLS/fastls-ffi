"""Verify same-commit CI evidence before signing GitHub-only npm candidates."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile

root = Path(__file__).resolve().parents[2]
run_file, candidates, destination = map(Path, sys.argv[1:])
run = json.loads(run_file.read_text())
version = json.loads((root/'node/package.json').read_text())['version']
sha = os.environ['GITHUB_SHA']
assert re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version)
assert os.environ['NODE_RELEASE_VERSION'] == version
assert run['status'] == 'completed' and run['conclusion'] == 'success'
assert run['head_sha'] == sha and run['head_branch'] == 'main' and run['event'] == 'push'
assert run['path'] == '.github/workflows/ci.yml'
baseline = json.loads((root/'node/tests/python-baseline.json').read_text())
expected = set()
for name in baseline['files']:
    for item in ast.walk(ast.parse((root/'tests'/name).read_text(encoding='utf-8'))):
        if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)) and item.name.startswith('test_'):
            expected.add(name+'::'+item.name)
destination.mkdir(parents=True,exist_ok=True)
records = []
for directory in sorted(candidates.glob('fastls-*-go1.26.10-py3.11-node24')):
    manifest = json.loads((directory/'dist/node-package-info.json').read_text())
    assert manifest['revision'] == sha and manifest['package']['version'] == version
    assert all(manifest['checks'].values()) and manifest['checks']['installedEntryAndNativePath']
    native = manifest['native']['manifest']
    assert native['release']['candidateOnly'] is True
    for label in ['source','package']:
        evidence = [json.loads(p.read_text()) for p in (directory/'build').glob('node-parity-'+label+'-*.json')]
        evidence = [e for e in evidence if len(e['cases']) == len(expected)]
        assert len(evidence) == 1
        cases=evidence[0]['cases']
        assert {c['python'] for c in cases} == expected and all(c['passed'] for c in cases)
        sdk = [json.loads(p.read_text()) for p in (directory/'build').glob('node-'+label+'-*.json')]
        assert len(sdk)==1 and len(sdk[0]['tests'])==14 and all(c['passed'] for c in sdk[0]['tests'])
    package = directory/'dist'/manifest['package']['file']
    assert package.name == manifest['package']['file']
    assert hashlib.sha256(package.read_bytes()).hexdigest() == manifest['package']['sha256']
    with tarfile.open(package) as archive:
        embedded=json.load(archive.extractfile('package/native/manifest.json'))
        assert embedded == native and embedded['sha256'] == manifest['native']['sha256']
        assert hashlib.sha256(archive.extractfile('package/native/'+embedded['file']).read()).hexdigest()==embedded['sha256']
        assert archive.extractfile('package/LICENSE').read().replace(b'\r\n',b'\n')==(root/'LICENSE').read_bytes().replace(b'\r\n',b'\n')
    shutil.copyfile(package,destination/package.name)
    records.append({'platform':manifest['platform'],'arch':manifest['arch'],'package':manifest['package'],'native':native,'python_scenarios':len(expected)})
assert {(r['platform'],r['arch']) for r in records} == {('win32','x64'),('linux','x64'),('linux','arm64'),('darwin','x64'),('darwin','arm64')}
thin = destination/f'fasttls-fastls-{version}.tgz'
assert thin.is_file()
with tarfile.open(thin) as archive:
    assert not any(n.startswith('package/native/') for n in archive.getnames())
    assert json.load(archive.extractfile('package/package.json'))['version']==version
    assert archive.extractfile('package/LICENSE').read().replace(b'\r\n',b'\n')==(root/'LICENSE').read_bytes().replace(b'\r\n',b'\n')
pending=sorted({name for r in records for name in r['native']['release']['licenseReviewRequired']})
report={'version':version,'revision':sha,'run_id':run['id'],'ci_url':run['html_url'],'python_scenarios':len(expected),'platforms':records,'thin':{'file':thin.name,'sha256':hashlib.sha256(thin.read_bytes()).hexdigest()},'npm_registry_published':False,'candidate_only':True,'license_review_required':pending}
(destination/'node-release-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
notes=[f'@fasttls/fastls {version} — GitHub prerelease', '',f'Validated source: `{sha}`.',f'Full CI: {run["html_url"]}.','',f'Each platform candidate passed {len(expected)+16} source and isolated installed checks.','', 'Install the matching platform archive using npm install and its GitHub download URL. The unsuffixed thin archive requires an external Fastls library.','', 'Platform candidates:']
notes += [f'- `{r["package"]["file"]}`' for r in records]
notes += ['', 'Packages are distributed on GitHub only; nothing was published to the npm registry. Linux candidates require glibc; system requirements and native dependency licenses are recorded in each native manifest.', '', 'Verify SHA256SUMS and the GitHub artifact attestations. provenance.sigstore.json contains the signing bundle.']
if pending:
    notes += ['', 'License review remains required for: '+', '.join(pending)+'. The bundled THIRD_PARTY_NOTICES.txt retains the original provenance notices; this candidate does not add a license grant for these dependencies. npm registry publication is deferred.']
(destination/'RELEASE_NOTES.md').write_text('\n'.join(notes)+'\n')
shutil.copyfile(root/'LICENSE',destination/'LICENSE')
files=sorted(p for p in destination.iterdir() if p.is_file() and p.name!='SHA256SUMS')
(destination/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in files))
print(json.dumps({'verified_platforms':len(records),'python_scenarios':len(expected),'revision':sha}))
