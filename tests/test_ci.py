"""Exercise the dependency gate through its public CLI, including output safety."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DependencyGate(unittest.TestCase):
    def resolve(self, overrides):
        env = os.environ.copy()
        env.pop("GITHUB_STEP_SUMMARY", None)
        for key in ("FASTLS_REF", "FHTTP_REF", "IMITATE_REF"):
            env[key] = ""
        env.update(overrides)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "outputs"
            env["GITHUB_OUTPUT"] = str(output)
            process = subprocess.run([sys.executable, "scripts/ci.py", "resolve"], cwd=ROOT, env=env, text=True, capture_output=True)
            return process, output.read_text() if output.is_file() else ""

    def test_reviewed_commit_outputs(self):
        process, output = self.resolve({key: "a" * 40 for key in ("FASTLS_REF", "FHTTP_REF", "IMITATE_REF")})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(output.splitlines(), [f"{name}_ref={'a' * 40}" for name in ("fastls", "fhttp", "imitate")])

    def test_branch_or_injected_output_is_rejected(self):
        for value in ("main", "a" * 40 + "\nother=value"):
            process, output = self.resolve({"FASTLS_REF": value})
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
