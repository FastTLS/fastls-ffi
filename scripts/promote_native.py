"""Promote authenticated same-source native inputs without rewriting producer metadata."""
import hashlib
import json
import os
import platform
from pathlib import Path
import shlex
import shutil
import subprocess

from python_stages import verify


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_candidate(directory, receipt, expected, env):
    info = json.loads((directory / 'dist/build-info.json').read_text(encoding='utf-8'))
    verify(receipt, info, env)
    if {k: info.get(k) for k in expected} != expected:
        raise ValueError('Native producer inputs differ from current selected inputs')
    inputs = json.loads((directory / 'dist/ci-inputs.json').read_text(encoding='utf-8'))
    if inputs.get('target') != env['FASTLS_CI_TARGET'] or inputs.get('sources') != info['sources']:
        raise ValueError('Native input snapshot identity differs')
    target = env['FASTLS_CI_TARGET']
    library = 'fastls.dll' if target.startswith('windows') else 'libfastls.dylib' if target.startswith('darwin') else 'libfastls.so'
    fixture = 'build/ffi-fixture.exe' if target.startswith('windows') else 'build/ffi-fixture'
    required = {'dist/build-info.json', 'dist/native-audit.json', 'dist/module-inputs.json',
                'dist/fastls.h', 'dist/' + library, fixture}
    artifacts = inputs.get('artifacts', {})
    if not required <= artifacts.keys():
        raise ValueError('Native producer inventory incomplete')
    for name, checksum in artifacts.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or digest(directory / path) != checksum:
            raise ValueError('Native producer artifact changed or unsafe path')
    if digest(directory / 'dist' / library) != info['library_sha256']:
        raise ValueError('Native library differs from source receipt')
    return info


def promote(root, directory, receipt_path, sources, env):
    from build import source_digest
    from module_inputs import capture
    from ci import library_name
    from package_version import package_version
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    configuration = json.loads(subprocess.check_output(['go', 'env', '-json', 'GOOS', 'GOARCH', 'GOAMD64', 'GOARM64',
        'GOEXPERIMENT', 'CGO_ENABLED', 'CGO_CFLAGS', 'CGO_CPPFLAGS', 'CGO_CXXFLAGS', 'CGO_LDFLAGS', 'GOFLAGS'],
        env=env, text=True, encoding='utf-8'))
    expected = dict(sources=sources, workspace_source=source_digest(env), module_graph=capture(root, env),
        platform=platform.platform(), machine=platform.machine(),
        module_files={p: digest(root / p) for p in ('go.mod', 'go.sum')}, local_dependencies=False, abi=1,
        go=subprocess.check_output(['go', 'version'], env=env, text=True).strip(), build_configuration=configuration,
        cc=env.get('CC', 'gcc'), cc_version=subprocess.check_output(shlex.split(env.get('CC', 'gcc')) + ['--version'],
            env=env, text=True, encoding='utf-8'))
    info = verify_candidate(directory, receipt, expected, env)
    library = library_name()
    fixture = 'ffi-fixture.exe' if os.name == 'nt' else 'ffi-fixture'
    required = ['dist/' + library, 'dist/fastls.h', 'dist/build-info.json', 'dist/module-inputs.json',
                'dist/native-audit.json', 'build/' + fixture]
    inputs = json.loads((directory / 'dist/ci-inputs.json').read_text(encoding='utf-8'))
    if not set(required) <= inputs['artifacts'].keys():
        raise ValueError('Producer artifact inventory incomplete')
    for name in required:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(directory / name, target)
    proof = dict(schema=1, package_version=package_version(env), producer_package_version=info['package_version'],
        producer_revision=receipt['revision'], producer_run_id=receipt['run_id'], target=receipt['target'],
        library_sha256=info['library_sha256'], receipt_sha256=digest(receipt_path))
    (root / 'dist/package-info.json').write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    return proof
