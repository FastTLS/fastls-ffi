"""Restore bounded publication progress only from the exact trusted workflow."""
import io
import json
import os
from pathlib import Path
import re
import urllib.request
import zipfile


def validate_run(run, env):
    if not (run.get('repository', {}).get('full_name') == 'FastTLS/fastls-ffi'
            and run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main'
            and run.get('path') == '.github/workflows/npm-publish.yml'
            and run.get('head_sha') == env['GITHUB_SHA']):
        raise ValueError('Untrusted or changed publication resume run')


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
        if env.get('CONFIRM_ONLY') == 'true':
            raise ValueError('Read-only confirmation requires an exact previous run')
        return
    if not re.fullmatch('[1-9][0-9]*', source):
        raise ValueError('Exact numeric publication run required')
    opener = urllib.request.build_opener(Redirect())

    def read(path, raw=False):
        request = urllib.request.Request('https://api.github.com/repos/FastTLS/fastls-ffi/' + path,
            headers={'Authorization': 'Bearer ' + env['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                     'User-Agent': 'bounded-publication-state'})
        with opener.open(request, timeout=30) as response:
            body = response.read(2 << 20)
        return body if raw else json.loads(body)

    run = read('actions/runs/' + source)
    validate_run(run, env)
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
    # The publisher also checks manifest digest, sources, package set and all
    # budgets before using this auxiliary state. It never replaces registry proof.
    Path('npm-release/resume-state.json').write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
