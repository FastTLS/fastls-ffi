"""Exercise the dependency gate through its public CLI, including output safety."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ci_runtime import EvidenceRunner
import ci

ROOT = Path(__file__).resolve().parents[1]


class DependencyGate(unittest.TestCase):
    def test_preflight_can_be_loaded_without_scripts_on_import_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            code = "import runpy; assert callable(runpy.run_path(" + repr(str(ROOT / "scripts/ci.py")) + ")[\"preflight\"])"
            process = subprocess.run([sys.executable, "-I", "-c", code], cwd=temporary, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def resolve(self, overrides):
        env = os.environ.copy()
        env.pop("GITHUB_STEP_SUMMARY", None)
        for key in ("FASTLS_REF", "FTRANSPORT_REF", "IMITATE_REF"):
            env[key] = ""
        env.update(overrides)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "outputs"
            env["GITHUB_OUTPUT"] = str(output)
            process = subprocess.run([sys.executable, "scripts/ci.py", "resolve"], cwd=ROOT, env=env, text=True, capture_output=True)
            return process, output.read_text() if output.is_file() else ""

    def test_reviewed_commit_outputs(self):
        process, output = self.resolve({key: "a" * 40 for key in ("FASTLS_REF", "FTRANSPORT_REF", "IMITATE_REF")})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(output.splitlines()[:3], [f"{name}_ref={'a' * 40}" for name in ("fastls", "ftransport", "imitate")])
        matrix = json.loads(output.splitlines()[3].removeprefix("matrix="))
        self.assertEqual(len(matrix), 6)
        self.assertEqual({c["target"] for c in matrix}, {"windows-amd64", "linux-amd64", "linux-arm64", "darwin-amd64", "darwin-arm64"})

    def test_branch_or_injected_output_is_rejected(self):
        for value in ("main", "a" * 40 + "\nother=value"):
            process, output = self.resolve({"FASTLS_REF": value})
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(output, "")


class ExecutionEvidence(unittest.TestCase):
    def test_failure_preserves_earlier_results_and_partial_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = EvidenceRunner(Path(temporary) / "evidence", {"stage": "test"})
            runner.execute("passed", [sys.executable, "-c", "print('first')"], ROOT, os.environ.copy())
            with self.assertRaises(RuntimeError):
                runner.execute("failed", [sys.executable, "-c", "print('partial'); raise SystemExit(7)"], ROOT, os.environ.copy())
            record = json.loads((runner.directory / "results.json").read_text())
            self.assertFalse(record["success"])
            self.assertEqual([c["exit_code"] for c in record["checks"]], [0, 7])
            self.assertIn("partial", (runner.directory / "failed.log").read_text())

    def test_stalled_process_is_terminated_and_marked_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = EvidenceRunner(Path(temporary) / "evidence", {})
            with self.assertRaises(RuntimeError):
                runner.execute("timeout", [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(30)"], ROOT, os.environ.copy(), timeout=.5)
            self.assertEqual(runner.results["checks"][0]["state"], "failed")
            self.assertLess(runner.results["checks"][0]["duration_seconds"], 10)

    def test_changed_sdk_artifact_or_commit_is_rejected(self):
        sources = {"fastls-ffi": "a" * 40}
        record = dict(target="windows-amd64", sources=sources, sdk={"sdk.py": "one"}, artifacts={"lib": "one"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "dist").mkdir()
            (root / "dist/ci-inputs.json").write_text(json.dumps(record))
            with patch.object(ci, "ROOT", root), patch.object(ci, "sdk_inputs", return_value=record["sdk"]) as sdk, patch.object(ci, "artifact_inputs", return_value=record["artifacts"]) as artifacts:
                ci.verify_inputs("windows-amd64", sources)
                for target, revisions in (("linux-amd64", sources), ("windows-amd64", {"fastls-ffi": "b" * 40})):
                    with self.assertRaises(RuntimeError): ci.verify_inputs(target, revisions)
                sdk.return_value = {"sdk.py": "changed"}
                with self.assertRaises(RuntimeError): ci.verify_inputs("windows-amd64", sources)
                sdk.return_value = record["sdk"]
                artifacts.return_value = {"lib": "changed"}
                with self.assertRaises(RuntimeError): ci.verify_inputs("windows-amd64", sources)


if __name__ == "__main__":
    unittest.main(verbosity=2)
