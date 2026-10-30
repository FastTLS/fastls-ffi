"""Select CI coverage from the release level and the actual changed files."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TIERS = ('docs', 'patch', 'minor', 'major')
PUBLISHING_TOOLS = {
    '.github/workflows/npm-publish.yml', 'node/scripts/check-npm-oidc.mjs',
    'node/scripts/test-npm-oidc.mjs', 'node/scripts/promotion_source.py',
    'node/scripts/test-npm-promotion.py', 'node/scripts/assemble-npm.py',
    'node/scripts/publish-npm.mjs', 'scripts/test_plan.py', 'tests/test_ci_plan.py',
}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def version(text):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', text)
    if not match:
        raise ValueError('Expected an exact X.Y.Z release version')
    return tuple(map(int, match.groups()))


def release_tier(current, previous):
    current, previous = version(current), version(previous)
    if current <= previous:
        raise ValueError('Release version must advance')
    return 'major' if current[0] != previous[0] else 'minor' if current[1] != previous[1] else 'patch'


def choose(files, *, requested='auto', release=None, previous=None, uncertain=False, override=False):
    if requested not in ('auto', *TIERS[1:]):
        raise ValueError('Unknown test tier')
    docs = bool(files) and all(p.endswith('.md') or p.startswith('docs/') for p in files)
    tier = 'docs' if docs else 'patch'
    reasons = ['documentation only' if docs else 'ordinary change']
    fixture = any(p.startswith('cmd/ffi-fixture/') for p in files)
    sdk_state = {'python/fastls/client.py', 'python/fastls/auth.py', 'python/fastls/cookies.py',
                 'python/fastls/profiles.py', 'node/src/profiles.ts',
                 'python/fastls/redirects.py', 'python/fastls/retry.py', 'python/fastls/profile_store.py',
                 'node/src/session.ts', 'node/src/retry.ts', 'node/src/cookies.ts', 'node/src/profile-store.ts'}
    resource = any(p.endswith('.go') and not p.startswith('cmd/ffi-fixture/') or p in ('go.mod', 'go.sum', 'ci/dependencies.json') or
                   p in sdk_state or any(word in p.lower() for word in ('pool', 'duplex', 'timeout', 'proxy', 'decode', 'websocket')) for p in files)
    packaging = any(p.startswith(('scripts/build', 'scripts/package', 'scripts/repair', 'scripts/native', 'scripts/module', 'scripts/release', 'scripts/prepare_pypi')) or
                    p in ('pyproject.toml', 'setup.py', 'setup.cfg', 'MANIFEST.in', 'LICENSE') or p.startswith('ci/') for p in files)
    unknown = any(not (p.endswith(('.md', '.go')) or p.startswith(('docs/', 'python/', 'node/', 'tests/', 'scripts/', 'benchmarks/', '.github/', 'ci/')) or
                      p in ('go.mod', 'go.sum', 'pyproject.toml', 'setup.py', 'setup.cfg', 'MANIFEST.in', 'LICENSE', '.gitignore')) for p in files)
    if resource or packaging or uncertain or override or unknown:
        tier = 'minor'
        reasons.append('native/resource/packaging risk or unavailable change baseline')
    if release:
        level = release_tier(release, previous) if previous else 'major'
        tier = TIERS[max(TIERS.index(tier), TIERS.index(level))]
        reasons.append('release version floor: ' + level)
    if requested != 'auto':
        tier = TIERS[max(TIERS.index(tier), TIERS.index(requested))]
        reasons.append('manual floor: ' + requested)
    from ci import TARGETS
    pypi_tools = {'scripts/promote_tag.py', 'tests/publish_promotion_checks.py'}
    publishing_only = bool(files) and set(files) <= PUBLISHING_TOOLS | pypi_tools and any(
        p.startswith('node/scripts/') or p == 'scripts/promote_tag.py' for p in files)
    if publishing_only and not (release or uncertain or override or requested != 'auto'):
        return dict(tier='patch', reasons=['publication tools only; original certified archives are unchanged; publishing workflow runs rejection/source-identity tests'],
            files=files, native=False, node=False, matrix=TARGETS[:2],
            node_matrix=[dict(target, node='24', race=False) for target in TARGETS[:2]], extended=[],
            node_soak=False, candidates=False, pypi=False, soak_seconds=300, full_performance=False)
    python_adapter = 'python/fastls/_native.py' in files
    if python_adapter:
        reasons.append('Python native adapter requires all existing platform installations')
    targets = TARGETS if tier == 'major' else TARGETS[:5] if tier == 'minor' or release or python_adapter or fixture else TARGETS[:2]
    node = tier != 'docs' and (tier in ('minor', 'major') or bool(release) or any(p.startswith('node/') or p.endswith('.go') for p in files))
    candidates = node and (tier in ('minor', 'major') or bool(release))
    extended = ['race', 'soak'] if tier in ('minor', 'major') else ['race'] if release else []
    node_targets = [dict(t, node='22' if t['go'] == '1.27.3' else '24', race=t['target'] == 'linux-amd64' and t['go'] == '1.26.10') for t in targets]
    return dict(tier=tier, reasons=reasons, files=files, native=tier != 'docs', node=node,
                matrix=targets, node_matrix=node_targets, extended=extended,
                node_soak=tier in ('minor', 'major'), candidates=candidates,
                pypi=bool(release) or tier in ('minor', 'major'),
                soak_seconds=3600 if tier == 'major' else 300,
                full_performance=False)


def context(env):
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_text()) if env.get('GITHUB_EVENT_PATH') else {}
    release = env.get('GITHUB_REF_NAME') if env.get('GITHUB_REF_TYPE') == 'tag' else None
    previous = None
    uncertain = False
    try:
        if release:
            current = version(release)
            tags = [t for t in git('tag', '--merged', 'HEAD').splitlines() if re.fullmatch(r'v\d+\.\d+\.\d+', t) and version(t) < current]
            previous = max(tags, key=version) if tags else None
            base = previous
        elif event.get('pull_request'):
            base = git('merge-base', event['pull_request']['base']['sha'], 'HEAD')
        elif re.fullmatch(r'[0-9a-f]{40}', event.get('before', '')) and event['before'] != '0' * 40:
            base = event['before']
        else:
            base = git('merge-base', 'HEAD', 'refs/remotes/origin/main')
            if base == git('rev-parse', 'HEAD'):
                base = git('rev-parse', 'HEAD^')
        files = git('diff', '--name-only', '--no-renames', base, 'HEAD').splitlines() if base else []
        uncertain = not bool(files)
        if not release and 'node/package.json' in files:
            before_version = json.loads(git('show', base + ':node/package.json'))['version']
            after_version = json.loads((ROOT / 'node/package.json').read_text())['version']
            if before_version != after_version:
                release, previous = after_version, before_version
        # A follow-up test fix must not narrow an unreleased minor candidate to
        # two platforms merely because its version bump was an earlier commit.
        # Published versions retain ordinary patch scopes; docs stay docs-only.
        source_changes = any(not (p.endswith(('.md', '.rst', '.txt')) or p in ('LICENSE', 'NOTICE')) for p in files)
        if not release and env.get('GITHUB_REF') == 'refs/heads/main' and source_changes:
            current_node = json.loads((ROOT / 'node/package.json').read_text())['version']
            node_tags = [t[len('node-v'):] for t in git('tag', '--merged', 'HEAD').splitlines()
                         if re.fullmatch(r'node-v\d+\.\d+\.\d+', t)]
            if node_tags:
                published_node = max(node_tags, key=version)
                if version(current_node) > version(published_node):
                    release, previous = current_node, published_node
    except (subprocess.CalledProcessError, ValueError):
        if release:
            version(release)  # Invalid release tags must fail, never silently downgrade.
        files, uncertain = [], True
    selected_files = files
    if '.github/workflows/ci.yml' in files:
        from promote_tag import workflow_inputs_unchanged, PYPI_TOOLS
        workflow = '.github/workflows/ci.yml'
        if (not uncertain and not release and set(files) <= PUBLISHING_TOOLS | PYPI_TOOLS | {workflow}
                and workflow_inputs_unchanged(git('show', base + ':' + workflow), git('show', 'HEAD:' + workflow))):
            selected_files = [p for p in files if p != workflow]
    plan = choose(selected_files, requested=env.get('TEST_TIER') or 'auto', release=release, previous=previous,
                  uncertain=uncertain, override=any(env.get(n) for n in ('FASTLS_REF', 'FTRANSPORT_REF', 'IMITATE_REF', 'CERTIFICATION_SOAK_SECONDS')))
    if selected_files != files:
        plan['files'] = files
        plan['reasons'].append('verified unchanged production jobs, toolchains, environment and existing event inputs; publication guards/explicit promotion only')
    duration = env.get('CERTIFICATION_SOAK_SECONDS')
    if duration:
        if not re.fullmatch(r'[0-9]+', duration) or not 300 <= int(duration) <= 7200:
            raise ValueError('Resource observation must be an integer in 300..7200 seconds')
        plan['soak_seconds'] = max(plan['soak_seconds'], int(duration))
    if (env.get('GITHUB_ACTIONS') == 'true' and (env.get('GITHUB_REF_TYPE') == 'tag'
            or env.get('GITHUB_REF') == 'refs/heads/main' and (ROOT / 'ci/resource-reuse.json').is_file())
            and plan['tier'] == 'minor' and not any(env.get(n) for n in
                ('FASTLS_REF', 'FTRANSPORT_REF', 'IMITATE_REF', 'CERTIFICATION_SOAK_SECONDS'))):
        from release_reuse import find
        try:
            reused = find(env)
        except (OSError, ValueError, KeyError) as error:
            print('Resource evidence unavailable; retaining full checks: ' + type(error).__name__, file=sys.stderr)
            reused = None
        if reused:
            plan.update(extended=[], node_soak=False, resource_reuse=reused)
            plan['reasons'].append('unchanged race/resource inputs and successful source jobs verified; fresh platform identity and installs remain required')
            if env.get('GITHUB_REF_TYPE') == 'tag':
                try:
                    node_delivery = find(env, platform_only=True)
                except (OSError, ValueError, KeyError):
                    node_delivery = None
                if node_delivery:
                    plan.update(node=False, candidates=False, node_delivery_reuse=node_delivery)
                    plan['python_source_run_id'] = str(node_delivery['run_id'])
                    plan['reasons'].append('Node registry packages use the verified same-commit main artifacts; this tag publishes only fresh Python/C ABI artifacts')
    return plan


def required_results(plan, results):
    expected = {'dependencies': True, 'native': plan['native'], 'tests': plan['native'],
                'extended': bool(plan['extended']), 'node': plan['node'],
                'node-soak': plan['node_soak'], 'node-candidates': plan['candidates']}
    errors = []
    for name, enabled in expected.items():
        actual = results.get(name, {}).get('result')
        wanted = 'success' if enabled else 'skipped'
        if actual != wanted:
            errors.append(f'{name}: expected {wanted}, got {actual}')
    return errors


def main():
    if sys.argv[1:] == ['--check-results']:
        errors = required_results(json.loads(os.environ['TEST_PLAN']), json.loads(os.environ['TEST_RESULTS']))
        if errors:
            raise SystemExit('\n'.join(errors))
        print('All checks required by the selected plan passed; optional checks were explicitly skipped.')
        return
    plan = context(os.environ)
    output = {**plan, 'plan': plan, 'extended_enabled': bool(plan['extended']), 'extended_matrix': plan['extended'] or ['race']}
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            for key, value in output.items():
                stream.write(key + '=' + (value if isinstance(value, str) else json.dumps(value, separators=(',', ':'))) + '\n')
    summary = json.dumps(plan, indent=2)
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('Selected test scope\n```json\n' + summary + '\n```\n')


if __name__ == '__main__':
    main()
