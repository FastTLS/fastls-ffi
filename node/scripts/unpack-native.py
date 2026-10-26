"""Copy only the three expected native files; never extract archive paths."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

archive, destination = map(Path, sys.argv[1:])
with tarfile.open(archive) as tar:
    metadata = json.load(tar.extractfile('package/package.json'))
    manifest = json.load(tar.extractfile('package/native/manifest.json'))
    name = {'win32': 'fastls.dll', 'linux': 'libfastls.so', 'darwin': 'libfastls.dylib'}[manifest['platform']]
    if manifest['file'] != name or manifest['schema'] != 1 or manifest['abi'] != 1:
        raise ValueError('invalid native manifest')
    data = tar.extractfile('package/native/' + name).read()
    if hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError('native archive integrity mismatch')
    destination.mkdir(parents=True, exist_ok=True)
    for filename in [name, 'manifest.json', 'THIRD_PARTY_NOTICES.txt']:
        (destination/filename).write_bytes(tar.extractfile('package/native/' + filename).read())
print(json.dumps({'metadata': metadata, 'native': manifest}))
