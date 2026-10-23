"""Native CI entry point; dependency resolution fails closed before matrix jobs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REFS = ("fastls_ref", "fhttp_ref", "imitate_ref")


def summary(message):
    print(message, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(message + "\n")


def resolve():
    refs = json.loads((ROOT / "ci/dependencies.json").read_text(encoding="utf-8"))
    resolved = {}
    for name in REFS:
        ref = os.environ.get(name.upper()) or refs.get(name)
        if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
            summary(f"Dependency not ready: {name}. Push the reviewed upstream implementation, then set its full commit SHA in ci/dependencies.json, a repository variable, or workflow_dispatch. No old-version fallback is allowed. See CI.md.")
            return 1
        resolved[name] = ref.lower()
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for name, ref in resolved.items():
                output.write(f"{name}={ref}\n")
    summary("Resolved immutable dependency commits: " + json.dumps(resolved))
    return 0


def preflight(env):
    capabilities = {
        "fastls/index.go": ("DoContext(", "VerifyTLS", "TLSRootCAs"),
        "fastls/profile/go.mod": ("github.com/FastTLS/fastls/profile",),
        "imitate/go.mod": ("github.com/FastTLS/imitate",),
        "fhttp/go.mod": ("github.com/FastTLS/fhttp",),
        "fhttp/http2/explicit_settings_test.go": ("func TestExplicitZeroDoesNotSelectNavigatorDefaults", "func TestExplicitEmptySettings"),
        "fhttp/http2/receive_window_test.go": ("func TestReceiveWindowMatchesWire",),
    }
    for relative, markers in capabilities.items():
        path = ROOT.parent / relative
        if not path.is_file() or not all(marker in path.read_text(encoding="utf-8") for marker in markers):
            raise RuntimeError(f"upstream checkout lacks required implementation: {relative}; see CI.md")
    host = subprocess.check_output(["go", "env", "GOHOSTOS", "GOHOSTARCH", "GOOS", "GOARCH"], env=env, text=True).split()
    if host[:2] != host[2:]:
        raise RuntimeError("CI tests must execute native binaries; GOOS/GOARCH must match the host")
    target = "-".join(host[:2])
    if os.environ.get("FASTLS_CI_TARGET", target) != target:
        raise RuntimeError(f"runner architecture mismatch: actual {target}")
    if "CC" not in env:
        env["CC"] = "clang" if sys.platform == "darwin" else "gcc"
    if os.name == "nt" and env["CC"] == "gcc" and not shutil.which("gcc"):
        for directory in (Path("C:/msys64/mingw64/bin"), Path("C:/mingw64/bin")):
            if (directory / "gcc.exe").is_file():
                env["PATH"] = str(directory) + os.pathsep + env["PATH"]
                break
    return target


def run():
    env = os.environ.copy()
    env["CGO_ENABLED"] = "1"
    try:
        target = preflight(env)
    except (RuntimeError, subprocess.SubprocessError) as error:
        summary(str(error)); return 1
    evidence = ROOT / "build" / ("ci-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    evidence.mkdir(parents=True)
    results = []
    success = False
    sources = {}
    for name, path in (("fastls-ffi", ROOT), ("fastls", ROOT.parent / "fastls"), ("fhttp", ROOT.parent / "fhttp"), ("imitate", ROOT.parent / "imitate")):
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
        sources[name] = revision.stdout.strip() if revision.returncode == 0 else None

    def execute(name, args, cwd=ROOT, command_env=None):
        try:
            process = subprocess.run(args, cwd=cwd, env=command_env or env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=600)
            code, log = process.returncode, process.stdout
        except (OSError, subprocess.TimeoutExpired) as error:
            code, log = 1, str(error)
        (evidence / (name + ".log")).write_text(log, encoding="utf-8")
        results.append({"name": name, "exit_code": code, "command": args})
        print(log, flush=True)
        if code:
            raise RuntimeError(f"{name} failed")
        return log

    try:
        execute("build", [sys.executable, "scripts/build.py", "--local"])
        env["GOWORK"] = str(ROOT / "go.work")
        execute("fixture-build", ["go", "build", "-o", "build/ffi-fixture.exe" if os.name == "nt" else "build/ffi-fixture", "./cmd/ffi-fixture"])
        execute("dependency-gate-tests", [sys.executable, "tests/test_ci.py"])
        execute("core-bridge-python", [sys.executable, "scripts/check.py"])
        execute("stability-source", [sys.executable, "tests/test_stability.py"])
        execute("network-source", [sys.executable, "tests/test_network.py"])
        for name, relative, pattern in (("profile", "fastls/profile", "./..."), ("generator", "imitate", "./...")):
            execute(name, ["go", "test", pattern], ROOT.parent / relative)
        execute("fhttp-contract", ["go", "test", "./http2", "-run", "^(TestExplicitZeroDoesNotSelectNavigatorDefaults|TestExplicitEmptySettings|TestConnectionSettings|TestReceiveWindow.*)$", "-v"], ROOT.parent / "fhttp")
        execute("fhttp-vet", ["go", "vet", "./http2"], ROOT.parent / "fhttp")
        if env.get("FASTLS_CI_RACE", "").lower() == "true":
            execute("race-bridge", ["go", "test", "-race", "./internal/bridge"])
            execute("race-core", ["go", "test", "-race", "./..."], ROOT.parent / "fastls")
            execute("race-fhttp-window", ["go", "test", "-race", "./http2", "-run", "^TestReceiveWindow"], ROOT.parent / "fhttp")
        execute("wheel-build", [sys.executable, "-m", "pip", "wheel", "./python", "--no-deps", "-w", "dist"])
        execute("wheel-tests", [sys.executable, "scripts/test_wheel.py"])
        execute("stability-wheel", [sys.executable, "scripts/test_wheel.py", "--stability"])
        execute("network-wheel", [sys.executable, "scripts/test_wheel.py", "--network"])
        dist = ROOT / "dist"
        library = "fastls.dll" if os.name == "nt" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"
        executable = dist / ("c-profiles.exe" if os.name == "nt" else "c-profiles")
        compiler = shlex.split(env["CC"], posix=True)
        execute("c-build", compiler + ["examples/c/profiles.c", "-I", str(dist), str(dist / library), "-o", str(executable)])
        runtime_env = env.copy()
        variable = "PATH" if os.name == "nt" else "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
        runtime_env[variable] = str(dist) + os.pathsep + runtime_env.get(variable, "")
        catalog = json.loads(execute("c-execute", [str(executable)], command_env=runtime_env))
        if not catalog.get("ok") or "chrome/156/windows" not in catalog["result"]:
            raise RuntimeError("C consumer did not receive the expected profile catalog")
        if env.get("FASTLS_CI_RACE", "").lower() == "true":
            soak_env = dict(env, FASTLS_STABILITY_SECONDS="300", FASTLS_TEST_LABEL="extended")
            execute("extended-soak", [sys.executable, "tests/test_stability.py", "Stability.test_soak_and_resource_recovery"], command_env=soak_env)
        if env.get("FASTLS_FULL_FHTTP_TESTS", "").lower() == "true":
            execute("fhttp-full-diagnostic", ["go", "test", "./http2", "-timeout", "5m"], ROOT.parent / "fhttp")
        success = True
    except (RuntimeError, ValueError) as error:
        summary(str(error))
        return 1
    finally:
        (evidence / "results.json").write_text(json.dumps({"success": success, "target": target, "sources": sources, "checks": results}, indent=2) + "\n", encoding="utf-8")
    summary(f"Native checks passed on {target}; evidence: {evidence.name}. This run does not publish a release.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("resolve", "run"))
    args = parser.parse_args()
    raise SystemExit(resolve() if args.command == "resolve" else run())
