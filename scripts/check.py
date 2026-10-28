"""Run host checks against a previously built library, retaining local evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def main():
    checks = [
        ("go-bridge", ["go", "test", "./internal/bridge"], ROOT),
        ("go-core", ["go", "test", "./..."], ROOT.parent / "fastls"),
        ("python-dll", [sys.executable, "tests/test_python.py"], ROOT),
    ]
    evidence = ROOT / "build" / ("checks-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    evidence.mkdir(parents=True, exist_ok=False)
    results = []
    for name, command, cwd in checks:
        env = os.environ.copy()
        if env.get('GOWORK') != 'off' and (ROOT / "go.work").is_file():
            env["GOWORK"] = str(ROOT / "go.work")
        result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=180)
        (evidence / (name + ".log")).write_text(result.stdout, encoding="utf-8")
        results.append({"name": name, "command": command, "exit_code": result.returncode})
        print(name, "PASS" if result.returncode == 0 else "FAIL")
    (evidence / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(evidence)
    return int(any(result["exit_code"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
