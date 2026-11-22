"""Reject any GitHub npm release asset outside the signed publication inventory."""
import hashlib
import json
from pathlib import Path
import sys
import re
import subprocess
import os


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


def finalize(directory, tag, source, api):
    directory = Path(directory)
    manifest = json.loads((directory / 'npm-release-manifest.json').read_text(encoding='utf-8'))
    if (not re.fullmatch(r'[0-9a-f]{40}', source) or manifest.get('revision') != source
            or not re.fullmatch(r'node-v[0-9]+\.[0-9]+\.[0-9]+', tag)
            or tag != 'node-v' + manifest.get('version', '')):
        raise ValueError('Release tag, version and certified producer differ')
    def check_tag():
        obj = api('git/ref/tags/' + tag)['object']
        for _ in range(4):
            if obj.get('type') == 'commit' and obj.get('sha') == source:
                return
            if obj.get('type') != 'tag':
                break
            obj = api('git/tags/' + obj['sha'])['object']
        raise ValueError('Immutable release tag differs from certified producer')
    check_tag()
    matches = []
    for page in range(1, 6):
        rows = api(f'releases?per_page=100&page={page}')
        if not isinstance(rows, list):
            raise ValueError('Invalid release inventory')
        matches.extend(row for row in rows if row.get('tag_name') == tag)
        if len(rows) < 100:
            break
    else:
        raise ValueError('Release inventory exceeds bounded lookup')
    if len(matches) != 1:
        raise ValueError('Expected exactly one release for the immutable tag')
    release_id = matches[0]['id']
    release = api(f'releases/{release_id}')
    if release.get('tag_name') != tag:
        raise ValueError('Release identity changed during lookup')
    verify(directory, release['assets'])
    check_tag()
    if release['draft']:
        api(f'releases/{release_id}', 'PATCH', {'draft': False})
    published = api(f'releases/{release_id}')
    if published.get('draft') is not False or published.get('tag_name') != tag:
        raise ValueError('Release publication did not complete')
    verify(directory, published['assets'])
    check_tag()
    return {'id': release_id, 'tag': tag, 'source': source, 'draft': False}


def github_api(route, method='GET', body=None):
    command = ['gh', 'api', 'repos/FastTLS/fastls-ffi/' + route, '--method', method]
    if body is not None:
        command += ['--input', '-']
    return json.loads(subprocess.check_output(command, input=json.dumps(body) if body is not None else None,
                                            text=True, encoding='utf-8'))


if __name__ == '__main__':
    if sys.argv[1:2] == ['finalize']:
        if (len(sys.argv) != 5 or os.environ.get('GITHUB_REPOSITORY') != 'FastTLS/fastls-ffi'
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
            raise SystemExit('Finalization requires the main publication workflow')
        print(json.dumps(finalize(Path(sys.argv[2]), sys.argv[3], sys.argv[4], github_api)))
    else:
        verify(Path(sys.argv[1]), json.load(sys.stdin)['assets'])
