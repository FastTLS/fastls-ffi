"""Reject any GitHub npm release asset outside the signed publication inventory."""
import hashlib
import json
from pathlib import Path
import sys


def verify(directory, assets):
    manifest = json.loads((directory / 'npm-release-manifest.json').read_text(encoding='utf-8'))
    names = [item['file'] for item in manifest['publish_order']]
    names += ['npm-release-manifest.json', 'RELEASE_NOTES.md', 'SHA256SUMS', 'provenance.sigstore.json']
    actual = [a.get('name') for a in assets]
    if len(names) != 10 or len(set(names)) != 10 or len(actual) != 10 or set(actual) != set(names):
        raise ValueError('GitHub release asset inventory differs from the signed publication set')
    for item in assets:
        name = item['name']
        if Path(name).name != name or '\\' in name or ':' in name:
            raise ValueError('Unsafe release asset name')
        if item.get('digest') != 'sha256:' + hashlib.sha256((directory / name).read_bytes()).hexdigest():
            raise ValueError('GitHub release asset digest differs: ' + name)


if __name__ == '__main__':
    verify(Path(sys.argv[1]), json.load(sys.stdin)['assets'])
