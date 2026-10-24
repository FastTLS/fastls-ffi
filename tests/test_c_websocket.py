"""Verify message payload copying, empty messages and close from a C process."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.parse import urlsplit
from test_stability import FixtureCase, EvidenceResult


class CWebSocket(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "c-websocket"

    def test_standalone_c_messages_and_close(self):
        root = Path(__file__).resolve().parents[1]
        binary = root / "dist" / ("c-websocket.exe" if sys.platform == "win32" else "c-websocket")
        result = subprocess.run([str(binary), str(urlsplit(self.fixture["http"]).port)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence, dict(ok=True, binary_bytes=4, empty_text=True, close_handshake=True))
        self.metrics["cases"]["c_consumer"] = evidence


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
