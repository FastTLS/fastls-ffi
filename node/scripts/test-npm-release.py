"""Exercise release rejection paths with synthetic CI archives, never publish."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SHA = 'a'*40
VERSION = json.loads((ROOT/'node/package.json').read_text())['version']
TARGETS = [('win32', 'x64'), ('linux', 'x64'), ('linux', 'arm64'), ('darwin', 'x64'), ('darwin', 'arm64')]


def name(target):
    return '@fastls/fastls-'+'-'.join(target)+('-glibc' if target[0] == 'linux' else '')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class NpmRelease(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fastls-npm-release-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write(self.root/'run.json', {'status': 'completed', 'conclusion': 'success', 'head_sha': SHA,
              'head_branch': 'main', 'event': 'push', 'path': '.github/workflows/ci.yml', 'id': 1})
        baseline = json.loads((ROOT/'node/tests/python-baseline.json').read_text())
        cases = [{'python': filename+'::'+item.name, 'passed': True} for filename in baseline['files']
                 for item in ast.walk(ast.parse((ROOT/'tests'/filename).read_text(encoding='utf-8')))
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith('test_')]
        self.infos = []
        for target in TARGETS:
            directory = self.root/'candidates'/('fastls-'+'-'.join(target)+'-go1.26.10-py3.11-node24')/'dist'
            native = {'file': 'test-library', 'sha256': hashlib.sha256(b'fixture').hexdigest(),
                      'release': {'licenseReviewRequired': ['fixture/unlicensed']}}
            main_pkg = {'name': '@fastls/fastls', 'version': VERSION,
                        'optionalDependencies': {name(t): VERSION for t in TARGETS}}
            native_pkg = {'name': name(target), 'version': VERSION, 'os': [target[0]], 'cpu': [target[1]]}
            if target[0] == 'linux':
                native_pkg['libc'] = ['glibc']
            record = {'platform': target[0], 'arch': target[1], 'revision': SHA,
                      'nativeManifest': native, 'checks': {'installedSuite': True},
                      'evidence': ['parity.json', 'sdk.json']}
            directory.joinpath('npm').mkdir(parents=True)
            for kind, pkg in [('main', main_pkg), ('native', native_pkg)]:
                path = directory/'npm'/(kind+'.tgz')
                contents = {'package/package.json': json.dumps(pkg).encode(), 'package/LICENSE': (ROOT/'LICENSE').read_bytes()}
                if kind == 'native':
                    contents.update({'package/native/manifest.json': json.dumps(native).encode(), 'package/native/test-library': b'fixture'})
                with tarfile.open(path, 'w:gz') as tar:
                    for filename, data in contents.items():
                        member = tarfile.TarInfo(filename); member.size = len(data)
                        tar.addfile(member, io.BytesIO(data))
                record[kind] = {'name': pkg['name'], 'version': VERSION, 'file': path.name,
                                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'metadata': pkg}
            # Production names are unique across targets; fixture names must be too.
            for kind in ['main', 'native']:
                old = directory/'npm'/record[kind]['file']
                new = old.with_name('-'.join(target)+'-'+old.name)
                old.rename(new); record[kind]['file'] = new.name
            info = directory/'npm/npm-package-info.json'
            write(info, record); self.infos.append(info)
            write(directory/'node-package-info.json', {'revision': SHA, 'native': {'manifest': native}, 'checks': {'installedSuite': True}})
            write(directory/'npm/parity.json', {'cases': cases})
            write(directory/'npm/sdk.json', {'tests': [{'passed': True}]*14})

    def run_assembly(self, *args):
        return subprocess.run([sys.executable, str(ROOT/'node/scripts/assemble-npm.py'), str(self.root/'run.json'),
              str(self.root/'candidates'), str(self.root/'output'), *args], env={**os.environ, 'GITHUB_SHA': SHA},
              text=True, capture_output=True)

    def test_prepare_collects_six_packages_and_pending_license(self):
        result = self.run_assembly()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root/'output/npm-release-manifest.json').read_text())
        self.assertEqual(len(report['publish_order']), 6)
        self.assertEqual(report['publish_order'][-1]['name'], '@fastls/fastls')
        self.assertEqual(report['license_review_required'], ['fixture/unlicensed'])

    def test_publish_rejects_pending_license(self):
        self.assertIn('license review', self.run_assembly('--publishable').stderr)

    def test_publishable_set_with_complete_licenses(self):
        for path in self.infos:
            info = json.loads(path.read_text())
            info['nativeManifest']['release']['licenseReviewRequired'] = []
            candidate_path = path.parent.parent/'node-package-info.json'
            candidate = json.loads(candidate_path.read_text())
            candidate['native']['manifest'] = info['nativeManifest']; write(candidate_path, candidate)
            archive_path = path.parent/info['native']['file']
            with tarfile.open(archive_path) as tar:
                contents = {member.name: tar.extractfile(member).read() for member in tar.getmembers()}
            contents['package/native/manifest.json'] = json.dumps(info['nativeManifest']).encode()
            with tarfile.open(archive_path, 'w:gz') as tar:
                for filename, data in contents.items():
                    member = tarfile.TarInfo(filename); member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
            info['native']['sha256'] = hashlib.sha256(archive_path.read_bytes()).hexdigest(); write(path, info)
        result = self.run_assembly('--publishable')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root/'output/npm-release-manifest.json').read_text())
        self.assertEqual(report['license_review_required'], [])

    def test_incomplete_platforms_rejected(self):
        self.infos[0].unlink()
        self.assertNotEqual(self.run_assembly().returncode, 0)

    def test_wrong_ci_revision_rejected(self):
        path = self.root/'run.json'; value = json.loads(path.read_text()); value['head_sha'] = 'b'*40; write(path, value)
        self.assertIn('same-commit', self.run_assembly().stderr)

    def test_failed_ci_rejected(self):
        path = self.root/'run.json'; value = json.loads(path.read_text()); value['conclusion'] = 'failure'; write(path, value)
        self.assertIn('same-commit', self.run_assembly().stderr)

    def test_archive_corruption_rejected(self):
        info = json.loads(self.infos[0].read_text())
        (self.infos[0].parent/info['native']['file']).write_bytes(b'corrupt')
        self.assertIn('checksum mismatch', self.run_assembly().stderr)

    def test_failed_parity_rejected(self):
        path = self.infos[0].parent/'parity.json'; value = json.loads(path.read_text()); value['cases'][0]['passed'] = False; write(path, value)
        self.assertIn('Incomplete npm installed', self.run_assembly().stderr)

    def test_package_metadata_mismatch_rejected(self):
        path = self.infos[0]; value = json.loads(path.read_text()); value['main']['metadata']['optionalDependencies'] = {}; write(path, value)
        self.assertIn('metadata', self.run_assembly().stderr)


if __name__ == '__main__':
    unittest.main()
