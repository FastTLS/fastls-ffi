"""Exercise the generated header and wait ABI from an independent C process."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.parse import urlsplit

from test_stability import FixtureCase, EvidenceResult


class CWait(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "c-wait"

    def test_standalone_c_readiness_and_binary_response(self):
        root = Path(__file__).resolve().parents[1]
        binary = root / "dist" / ("c-wait.exe" if sys.platform == "win32" else "c-wait")
        port = urlsplit(self.fixture["http"]).port
        result = subprocess.run([str(binary), str(port)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence["bytes"], 65536)
        self.assertTrue(evidence["ok"])
        self.assertGreaterEqual(evidence["waits"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
