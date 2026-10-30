"""Source and license drift must fail before a compiler can run."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import license_preflight as preflight


class LicensePreflightTests(unittest.TestCase):
    def setUp(self):
        self.sources = {n: 'a' * 40 for n in preflight.NAMES}
        value = dict(text='reviewed\n', normalized_sha256=hashlib.sha256(b'reviewed\n').hexdigest())
        self.register = dict(schema_version=1, sources={n: dict(commit='a' * 40, files={'LICENSE': copy.deepcopy(value)}) for n in preflight.NAMES})
        self.modules = [dict(Path='github.com/FastTLS/' + n, Origin=dict(Hash='a' * 40), license_files={'LICENSE': value['normalized_sha256']}) for n in preflight.NAMES]

    def test_valid_exact_sources_and_materials(self):
        preflight.validate(self.register, self.sources, self.modules)

    def test_missing_or_unreviewed_sources_and_bad_bytes_rejected(self):
        changes = [lambda r: r['sources'].pop('imitate'),
                   lambda r: r['sources']['imitate'].update(commit='b' * 40),
                   lambda r: r['sources']['imitate']['files']['LICENSE'].update(text='changed'),
                   lambda r: r['sources']['imitate'].update(files={})]
        for change in changes:
            value = copy.deepcopy(self.register)
            change(value)
            with self.assertRaises(RuntimeError):
                preflight.validate(value, self.sources, self.modules)

    def test_module_identity_extra_license_and_hash_drift_rejected(self):
        for field, value in [('Origin', dict(Hash='b' * 40)), ('license_files', {}),
                             ('license_files', {'LICENSE': 'a' * 64, 'NOTICE': 'b' * 64})]:
            modules = copy.deepcopy(self.modules)
            modules[0][field] = value
            with self.assertRaises(RuntimeError):
                preflight.validate(self.register, self.sources, modules)

    def test_build_and_matrix_cannot_start_before_preflight(self):
        root = Path(__file__).resolve().parents[1]
        build = (root / 'scripts/build.py').read_text(encoding='utf-8')
        self.assertLess(build.index('check_licenses(sources, formal)'), build.index('"-buildmode=c-shared"'))
        workflow = (root / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        self.assertIn('run: python scripts/license_preflight.py', workflow.split('\n  native:\n')[0])
        with patch.object(preflight, 'validate', side_effect=RuntimeError('unreviewed')), patch.object(preflight, 'materials') as render:
            with self.assertRaises(RuntimeError):
                preflight.check(self.sources)
            render.assert_not_called()


if __name__ == '__main__':
    unittest.main()
