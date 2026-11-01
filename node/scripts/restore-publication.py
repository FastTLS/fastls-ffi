"""Restore bounded publication progress only from the exact trusted workflow."""
import io
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request
import zipfile
from promotion_source import certify_source


def validate_run(run, env, certify=None):
    if not (run.get('repository', {}).get('full_name') == 'FastTLS/fastls-ffi'
            and run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main'
            and run.get('path') == '.github/workflows/npm-publish.yml'):
        raise ValueError('Untrusted or changed publication resume run')
    if run.get('head_sha') != env['GITHUB_SHA']:
        if certify is None:
            raise ValueError('Changed publisher requires verified publication-only ancestry')
        certify(run.get('head_sha'), env['GITHUB_SHA'])


def rebind_progress(state, previous_bytes, current_bytes, certify):
    previous, current = json.loads(previous_bytes), json.loads(current_bytes)
    producer = previous['revision']
    prior_publisher = previous.get('publisher_revision', producer)
    publisher = current.get('publisher_revision', current['revision'])
    identity = dict(producer=producer, publisher=prior_publisher,
                    manifest_sha256=hashlib.sha256(previous_bytes).hexdigest())
    def items(manifest):
        return [{k: item[k] for k in ('name', 'version', 'sha256')} for item in manifest['publish_order']]
    if (state.get('identity') != identity or current['revision'] != producer or
            current['version'] != previous['version'] or items(previous) != items(current) or
            [{k: item[k] for k in ('name', 'version', 'sha256')} for item in state['items']] != items(current)):
        raise ValueError('Resumed producer, manifest or immutable archive set differs')
    certify(prior_publisher, publisher)
    certify(producer, publisher)
    result = json.loads(json.dumps(state))
    if publisher != prior_publisher:
        result.setdefault('publisher_history', []).append(dict(identity=identity,
            run_id=state['run_id'], attempt=state['attempt']))
    result['identity'] = dict(producer=producer, publisher=publisher,
        manifest_sha256=hashlib.sha256(current_bytes).hexdigest())
    return result


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        if not url.startswith('https://'):
            raise ValueError('Artifact redirect requires HTTPS')
        result = super().redirect_request(request, fp, code, message, headers, url)
        for header in ('Authorization', 'Cookie', 'Proxy-Authorization'):
            result.remove_header(header)
        return result


def main():
    env = os.environ
    current, attempt = env['GITHUB_RUN_ID'], int(env['GITHUB_RUN_ATTEMPT'])
    source = env.get('RESUME_RUN_ID') or (current if attempt > 1 else '')
    if not source:
        if env.get('CONFIRM_ONLY') == 'true' or env.get('CONTINUE_AFTER_DEADLINE') == 'true':
            raise ValueError('Confirmation or explicit continuation requires an exact previous run')
        return
    if not re.fullmatch('[1-9][0-9]*', source):
        raise ValueError('Exact numeric publication run required')
    opener = urllib.request.build_opener(Redirect())

    def read(path, raw=False, limit=2 << 20):
        request = urllib.request.Request('https://api.github.com/repos/FastTLS/fastls-ffi/' + path,
            headers={'Authorization': 'Bearer ' + env['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                     'User-Agent': 'bounded-publication-state'})
        with opener.open(request, timeout=30) as response:
            body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Publication artifact exceeds bounded download size')
        return body if raw else json.loads(body)

    run = read('actions/runs/' + source)
    validate_run(run, env, certify_source)
    listing = read('actions/runs/' + source + '/artifacts?per_page=100')
    if listing['total_count'] != len(listing['artifacts']):
        raise ValueError('Incomplete publication artifact inventory')
    candidates = []
    for item in listing['artifacts']:
        match = re.fullmatch('npm-publication-' + source + '-([1-9][0-9]*)', item['name'])
        if match and not item['expired'] and (source != current or int(match[1]) < attempt):
            candidates.append((int(match[1]), item))
    if not candidates:
        # An absent receipt cannot safely grant a fresh upload/deadline.
        raise ValueError('Previous publication progress missing; diagnose before retry')
    prior_attempt, artifact = max(candidates, key=lambda p: p[0])
    with zipfile.ZipFile(io.BytesIO(read('actions/artifacts/' + str(artifact['id']) + '/zip', raw=True))) as archive:
        expected = f'publication-{source}-{prior_attempt}.json'
        if archive.namelist() != [expected] or archive.getinfo(expected).file_size > 1 << 20:
            raise ValueError('Unexpected publication progress inventory')
        state = json.loads(archive.read(expected))
    if str(state.get('run_id')) != source or str(state.get('attempt')) != str(prior_attempt):
        raise ValueError('Publication progress attempt differs')
    manifests = [item for item in listing['artifacts'] if item['name'] == 'npm-release' and not item['expired']]
    if len(manifests) != 1:
        raise ValueError('Exactly one authenticated prior package set required')
    with zipfile.ZipFile(io.BytesIO(read('actions/artifacts/' + str(manifests[0]['id']) + '/zip',
            raw=True, limit=64 << 20))) as archive:
        member = 'npm-release-manifest.json'
        if archive.namelist().count(member) != 1 or archive.getinfo(member).file_size > 1 << 20:
            raise ValueError('Invalid previous manifest inventory')
        previous_bytes = archive.read(member)
        previous_manifest = json.loads(previous_bytes)
        if previous_manifest.get('publisher_revision', previous_manifest['revision']) != run['head_sha']:
            raise ValueError('Previous manifest publisher differs from authenticated workflow')
        state = rebind_progress(state, previous_bytes,
            Path('npm-release/npm-release-manifest.json').read_bytes(), certify_source)
    # The publisher also checks manifest digest, sources, package set and all
    # budgets before using this auxiliary state. It never replaces registry proof.
    Path('npm-release/resume-state.json').write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
