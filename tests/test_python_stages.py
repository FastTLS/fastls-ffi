import copy
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from python_stages import verify, STAGES
import python_stages


class SourceReuseTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_REPOSITORY='FastTLS/fastls-ffi', GITHUB_REF_TYPE='tag', GITHUB_SHA='a' * 40,
                        FASTLS_SOURCE_RUN_ID='12', FASTLS_CI_TARGET='windows-amd64')
        self.info = dict(build_configuration={'GOOS': 'windows', 'GOARCH': 'amd64', 'GOFLAGS': ''},
                         cc_version='gcc exact version', module_files={'go.mod': 'digest'}, module_graph=[{'Path': 'dependency', 'Sum': 'h1:example'}],
                         workspace_source={'sha256': 'source'}, sources={'fastls-ffi': 'a' * 40, 'fastls': 'b' * 40},
                         go='go version', cc='gcc', abi=1, local_dependencies=False, library_sha256='c' * 64,
                         package_version='0.4.0.dev1', platform='same-platform')
        self.receipt = dict(schema=1, repository='FastTLS/fastls-ffi', revision='a' * 40, run_id='12', target='windows-amd64',
                            created_at=datetime.now(timezone.utc).isoformat(), passed_stages=list(STAGES), build_info=copy.deepcopy(self.info))

    def test_metadata_change_reuses_source_but_requires_actual_new_install(self):
        self.info.update(library_sha256='d' * 64, package_version='0.4.1')
        result = verify(self.receipt, self.info, self.env)
        self.assertEqual(result['required_stages'], ['wheel', 'release'])
        self.assertNotEqual(result['previous_sha256'], result['current_sha256'])

    def test_source_module_toolchain_config_abi_and_platform_drift_rejected(self):
        for key in set(self.info) - {'library_sha256', 'package_version'}:
            changed = copy.deepcopy(self.info)
            changed[key] = None
            with self.subTest(key=key), self.assertRaises((ValueError, KeyError, AttributeError)):
                verify(self.receipt, changed, self.env)

    def test_missing_compiler_evidence_is_not_legacy_reuse(self):
        del self.info['cc_version']
        with self.assertRaises(ValueError):
            verify(self.receipt, self.info, self.env)

    def test_failed_or_wrong_run_revision_platform_repository_rejected(self):
        for key, value in [('passed_stages', ['source']), ('run_id', '13'), ('target', 'linux-amd64'),
                           ('revision', 'b' * 40), ('repository', 'other/repo')]:
            receipt = copy.deepcopy(self.receipt)
            receipt[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify(receipt, self.info, self.env)

    def test_expired_future_or_unzoned_receipt_rejected(self):
        for when in [datetime.now(timezone.utc) - timedelta(days=15), datetime.now(timezone.utc) + timedelta(hours=1), datetime.now()]:
            receipt = copy.deepcopy(self.receipt)
            receipt['created_at'] = when.isoformat()
            with self.assertRaises(ValueError):
                verify(receipt, self.info, self.env)

    def test_missing_receipt_falls_back_and_failed_stage_cannot_create_success(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'dist').mkdir()
            (root / 'dist/build-info.json').write_text(json.dumps(self.info), encoding='utf-8')
            with patch.object(python_stages, 'ROOT', root), patch.dict(python_stages.os.environ, self.env), patch.object(python_stages.subprocess, 'run') as run:
                python_stages.main()
                self.assertEqual([call.args[0][-1] for call in run.call_args_list], list(STAGES))
                self.assertFalse(json.loads((root / 'build/source-reuse-decision.json').read_text())['reused'])
            (root / 'build/source-validation/receipt.json').unlink()
            with patch.object(python_stages, 'ROOT', root), patch.dict(python_stages.os.environ, self.env), patch.object(python_stages.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'source')):
                with self.assertRaises(subprocess.CalledProcessError):
                    python_stages.main()
                self.assertFalse((root / 'build/source-validation/receipt.json').exists())


if __name__ == '__main__':
    unittest.main()
