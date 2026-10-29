"""Promote exact certified archives across strictly publication-only changes."""
import json
from pathlib import Path
import re
import subprocess
import sys

PUBLISHING_TOOLS = {
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
    if not set(changed) <= PUBLISHING_TOOLS:
        raise ValueError('Certified archive promotion cannot include SDK, dependency, version or native build changes')
    return dict(schema=1, producer_revision=producer, publisher_revision=publisher,
                changed_publishing_tools=changed, production_inputs_unchanged=True,
                native_bytes='Original immutable producer artifacts; no rebuilding or relabelling.')


if __name__ == '__main__':
    print(json.dumps(certify_source(*sys.argv[1:3])))
