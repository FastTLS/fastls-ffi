"""Promote exact certified archives across strictly publication-only changes."""
import json
from pathlib import Path
import re
import subprocess
import sys

PUBLISHING_TOOLS = {
    'scripts/promote_tag.py',
    'node/scripts/npm-recovery.mjs', 'node/scripts/test-npm-recovery.mjs',
    'node/scripts/recover_npm_run.py', 'node/scripts/test_npm_recovery.py',
    '.github/workflows/npm-recovery.yml', 'node/NPM_RELEASE.md',
    'tests/publish_promotion_checks.py',
    '.github/workflows/npm-publish.yml', 'node/scripts/check-npm-oidc.mjs',
    'node/scripts/test-npm-oidc.mjs', 'node/scripts/promotion_source.py',
    'node/scripts/test-npm-promotion.py', 'node/scripts/assemble-npm.py',
    'node/scripts/publish-npm.mjs', 'scripts/test_plan.py', 'tests/test_ci_plan.py',
}


def certify_source(producer, publisher, root=None):
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value) for value in (producer, publisher)):
        raise ValueError('Expected immutable producer and publisher commits')
    if subprocess.run(['git', 'merge-base', '--is-ancestor', producer, publisher], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('Certified producer is not an ancestor of this publisher')
    changed = subprocess.check_output(['git', 'diff', '--name-only', '--no-renames', producer, publisher], cwd=root, text=True).splitlines()
    allowed = PUBLISHING_TOOLS
    if '.github/workflows/ci.yml' in changed or set(changed) - allowed:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
        from promote_tag import PYPI_TOOLS, workflow_inputs_unchanged
        allowed = allowed | PYPI_TOOLS | {'.github/workflows/ci.yml'}
        if '.github/workflows/ci.yml' in changed:
            try:
                before = subprocess.check_output(['git', 'show', producer + ':.github/workflows/ci.yml'], cwd=root, text=True, stderr=subprocess.DEVNULL)
                after = subprocess.check_output(['git', 'show', publisher + ':.github/workflows/ci.yml'], cwd=root, text=True, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError as error:
                raise ValueError('Cannot verify original native test configuration') from error
            if not workflow_inputs_unchanged(before, after):
                raise ValueError('Native test configuration changed outside publication scope')
    if not set(changed) <= allowed:
        raise ValueError('Certified archive promotion cannot include SDK, dependency, version or native build changes')
    return dict(schema=1, producer_revision=producer, publisher_revision=publisher,
                changed_publishing_tools=changed, production_inputs_unchanged=True,
                native_bytes='Original immutable producer artifacts; no rebuilding or relabelling.')


if __name__ == '__main__':
    print(json.dumps(certify_source(*sys.argv[1:3])))
