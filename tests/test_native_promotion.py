import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from promote_native import verify_candidate
from native_identity import validate
from python_stages import STAGES


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'dist').mkdir()
        (self.root / 'build').mkdir()
        self.env = dict(GITHUB_REPOSITORY='FastTLS/fastls-ffi', GITHUB_REF_TYPE='tag', GITHUB_SHA='a' * 40,
                        FASTLS_SOURCE_RUN_ID='123', FASTLS_CI_TARGET='windows-amd64')
        self.info = dict(package_version='0.7.1.dev1', local_dependencies=False, abi=1,
            sources={'fastls-ffi': 'a' * 40}, go='go exact', cc='gcc', cc_version='gcc exact',
            module_graph=[{'Path': 'exact'}], module_files={'go.mod': 'exact'}, workspace_source={'sha256': 'exact'},
            build_configuration={'GOOS': 'windows', 'GOARCH': 'amd64'}, library_sha256=hashlib.sha256(b'native').hexdigest())
        self.receipt = dict(schema=1, repository='FastTLS/fastls-ffi', revision='a' * 40, run_id='123',
            target='windows-amd64', created_at=datetime.now(timezone.utc).isoformat(), passed_stages=list(STAGES),
            build_info=copy.deepcopy(self.info))
        self.expected = {k:v for k,v in self.info.items() if k not in {'package_version', 'library_sha256'}}
        for name in ['dist/fastls.dll', 'dist/fastls.h', 'dist/native-audit.json', 'dist/module-inputs.json', 'build/ffi-fixture.exe']:
            (self.root / name).write_bytes(b'native' if name.endswith('.dll') else b'fixture')
        (self.root / 'dist/build-info.json').write_text(json.dumps(self.info))
        self.inputs = dict(target='windows-amd64', sources=self.info['sources'],
            artifacts={p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in self.root.rglob('*') if p.is_file()})
        self.save()

    def save(self):
        (self.root / 'dist/ci-inputs.json').write_text(json.dumps(self.inputs))

    def test_original_bytes_and_producer_metadata_are_accepted_without_relabeling(self):
        before = (self.root / 'dist/build-info.json').read_bytes()
        result = verify_candidate(self.root, self.receipt, self.expected, self.env)
        self.assertEqual(result['package_version'], '0.7.1.dev1')
        self.assertEqual((self.root / 'dist/build-info.json').read_bytes(), before)

    def test_changed_dependency_toolchain_source_configuration_or_native_bytes_rejected(self):
        for key in self.expected:
            changed = copy.deepcopy(self.expected)
            changed[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_candidate(self.root, self.receipt, changed, self.env)
        (self.root / 'dist/fastls.dll').write_bytes(b'wrong')
        with self.assertRaises(ValueError): verify_candidate(self.root, self.receipt, self.expected, self.env)

    def test_wrong_run_platform_stale_or_missing_receipt_is_not_reuse(self):
        for key,value in [('run_id','999'), ('target','linux-arm64'), ('passed_stages',[]),
                          ('created_at',(datetime.now(timezone.utc)-timedelta(days=15)).isoformat())]:
            receipt=copy.deepcopy(self.receipt); receipt[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_candidate(self.root, receipt, self.expected, self.env)

    def test_missing_and_unsafe_inventory_rejected(self):
        del self.inputs['artifacts']['dist/fastls.h']; self.save()
        with self.assertRaises(ValueError): verify_candidate(self.root, self.receipt, self.expected, self.env)
        self.inputs['artifacts']['../escape']='a'*64; self.save()
        with self.assertRaises(ValueError): verify_candidate(self.root, self.receipt, self.expected, self.env)

    def test_outer_version_requires_explicit_bound_producer_identity(self):
        proof=dict(schema=1, package_version='0.7.1', producer_package_version=self.info['package_version'],
                   producer_revision='a'*40, producer_run_id='123', target='windows-amd64',
                   library_sha256=self.info['library_sha256'], receipt_sha256='d'*64)
        validate(self.info, '0.7.1', proof, 'windows-amd64')
        with self.assertRaises(ValueError): validate(self.info, '0.7.1')
        for key in proof:
            changed=copy.deepcopy(proof); changed[key]=None
            with self.subTest(key=key), self.assertRaises((ValueError,TypeError)):
                validate(self.info, '0.7.1', changed, 'windows-amd64')


if __name__ == '__main__': unittest.main()
