"""Validate separate outer-package and immutable native-producer identities."""
import re


def validate(build, version, proof=None, target=None):
    if build.get('package_version') == version and proof is None:
        return
    required = {'schema', 'package_version', 'producer_package_version', 'producer_revision',
                'producer_run_id', 'target', 'library_sha256', 'receipt_sha256'}
    if not isinstance(proof, dict) or set(proof) != required or not (
            proof['schema'] == 1 and proof['package_version'] == version
            and proof['producer_package_version'] == build.get('package_version')
            and proof['producer_revision'] == build.get('sources', {}).get('fastls-ffi')
            and proof['library_sha256'] == build.get('library_sha256')
            and re.fullmatch('[a-f0-9]{64}', proof['library_sha256'])
            and re.fullmatch('[a-f0-9]{40}', proof['producer_revision'])
            and re.fullmatch('[a-f0-9]{64}', proof['receipt_sha256'])
            and re.fullmatch('[1-9][0-9]*', str(proof['producer_run_id']))
            and proof['target'] in {'windows-amd64', 'linux-amd64', 'linux-arm64', 'darwin-amd64', 'darwin-arm64'}
            and (target is None or proof['target'] == target)):
        raise ValueError('Outer package and native producer identity differ')
