"""Reject unreviewed source identities and stale notices before native compilation."""
import hashlib
import json
import os
from pathlib import Path
import re

from release_materials import materials

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('fastls', 'ftransport', 'imitate')


def validate(reviewed, sources, modules=None):
    if reviewed.get('schema_version') != 1 or set(reviewed.get('sources', {})) != set(NAMES):
        raise RuntimeError('Incomplete reviewed source license register')
    for name in NAMES:
        record = reviewed['sources'][name]
        if not re.fullmatch(r'[0-9a-f]{40}', record.get('commit', '')) or sources.get(name) != record['commit']:
            raise RuntimeError('Review source license materials before building: ' + name)
        files = record.get('files', {})
        if not any(n.upper().startswith(('LICENSE', 'COPYING')) for n in files):
            raise RuntimeError('Missing reviewed license: ' + name)
        for filename, value in files.items():
            if Path(filename).name != filename or hashlib.sha256(value['text'].encode('utf-8')).hexdigest() != value['normalized_sha256']:
                raise RuntimeError('Reviewed source license bytes changed: ' + name)
        if modules is not None:
            matches = [m for m in modules if m['Path'] == 'github.com/FastTLS/' + name]
            if len(matches) != 1 or matches[0].get('Origin', {}).get('Hash') != record['commit']:
                raise RuntimeError('Formal module source mismatch: ' + name)
            expected = {n: v['normalized_sha256'] for n, v in files.items()}
            if matches[0].get('license_files') != expected:
                raise RuntimeError('Formal module license differs from reviewed bytes: ' + name)


def check(sources, modules=None):
    reviewed = json.loads((ROOT / 'ci/reviewed-source-licenses.json').read_text(encoding='utf-8'))
    validate(reviewed, sources, modules)
    # Explicit inputs prevent an old dist/build-info.json influencing preflight.
    info = dict(sources=sources, local_dependencies=modules is None, module_graph=modules)
    materials(False, info)
    return dict(sources=sources, module_count=len(modules or []), notices_sha256=hashlib.sha256(
        (ROOT / 'python/THIRD_PARTY_LICENSES.txt').read_bytes()).hexdigest())


def main():
    from module_inputs import capture, source_commits
    env = dict(os.environ, GOWORK='off', CGO_ENABLED='1')
    modules = capture(ROOT, env)
    receipt = check(source_commits(modules), modules)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
