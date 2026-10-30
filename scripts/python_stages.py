"""Reuse proved same-source checks; always check new wheel and installation bytes."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('contracts', 'source', 'wheel', 'release')
METADATA = {'package_version', 'library_sha256'}


def verify(receipt, current, env):
    if not isinstance(receipt, dict) or not isinstance(current, dict):
        raise ValueError('Malformed source receipt')
    if not (receipt.get('schema') == 1 and receipt.get('repository') == 'FastTLS/fastls-ffi'
            and env.get('GITHUB_REPOSITORY') == receipt['repository']
            and env.get('GITHUB_REF_TYPE') == 'tag'
            and receipt.get('revision') == env.get('GITHUB_SHA')
            and receipt.get('run_id') == env.get('FASTLS_SOURCE_RUN_ID')
            and receipt.get('target') == env.get('FASTLS_CI_TARGET')
            and receipt.get('passed_stages') == list(STAGES)):
        raise ValueError('Source receipt identity/success does not match the selected main workflow')
    when = datetime.fromisoformat(receipt['created_at'])
    now = datetime.now(timezone.utc)
    if when.tzinfo is None or not now - timedelta(days=14) <= when <= now + timedelta(minutes=5):
        raise ValueError('Source receipt is expired or from the future')
    previous = receipt['build_info']
    if not isinstance(previous, dict):
        raise ValueError('Malformed previous native inputs')
    required = {'build_configuration', 'cc_version', 'module_files', 'module_graph', 'workspace_source', 'sources', 'go', 'cc', 'abi'}
    if not required.issubset(current) or not required.issubset(previous):
        raise ValueError('Source receipt lacks compiler/dependency evidence')
    if current.get('local_dependencies') is not False or previous.get('local_dependencies') is not False:
        raise ValueError('Only formal native inputs can reuse source checks')
    if not isinstance(current['sources'], dict) or current['sources'].get('fastls-ffi') != env.get('GITHUB_SHA'):
        raise ValueError('Build source differs from workflow revision')
    for info in (previous, current):
        if not re.fullmatch(r'[a-f0-9]{64}', info.get('library_sha256', '')):
            raise ValueError('Missing native artifact identity')
    if {k:v for k,v in previous.items() if k not in METADATA} != {k:v for k,v in current.items() if k not in METADATA}:
        raise ValueError('Inputs changed beyond version/artifact metadata; full source checks required')
    return dict(run_id=receipt['run_id'], revision=receipt['revision'], target=receipt['target'],
                previous_sha256=previous['library_sha256'], current_sha256=current['library_sha256'],
                reused_stages=['contracts', 'source'], required_stages=['wheel', 'release'])


def main():
    current = json.loads((ROOT / 'dist/build-info.json').read_text(encoding='utf-8'))
    decision, stages = {'reused': False, 'reason': 'No selected same-source receipt'}, STAGES
    receipt_path = ROOT / 'build/source-receipt/receipt.json'
    if os.environ.get('FASTLS_SOURCE_RUN_ID'):
        try:
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            decision = dict(reused=True, **verify(receipt, current, os.environ))
            stages = ('wheel', 'release')
        except (OSError, ValueError, KeyError, TypeError) as error:
            decision = dict(reused=False, reason=str(error))
    output = ROOT / 'build/source-reuse-decision.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(decision, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(decision, indent=2), flush=True)
    for stage in stages:
        subprocess.run([sys.executable, 'scripts/ci.py', 'run', '--stage', stage], cwd=ROOT, check=True)
    # Only fully executed source checks can create a new reusable receipt.
    if stages == STAGES:
        receipt = dict(schema=1, repository=os.environ.get('GITHUB_REPOSITORY'), revision=os.environ.get('GITHUB_SHA'),
            run_id=os.environ.get('GITHUB_RUN_ID'), target=os.environ.get('FASTLS_CI_TARGET'),
            created_at=datetime.now(timezone.utc).isoformat(), passed_stages=list(STAGES), build_info=current)
        directory = ROOT / 'build/source-validation'
        directory.mkdir(exist_ok=True)
        (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
