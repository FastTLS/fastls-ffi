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
import hashlib
import platform

ROOT = Path(__file__).resolve().parents[1]
FASTLS_ROOT = Path(os.environ.get("FASTLS_LOCAL_FASTLS", ROOT.parent / "fastls")).resolve()
FTRANSPORT_ROOT = Path(os.environ.get("FASTLS_LOCAL_FTRANSPORT", ROOT.parent / "ftransport")).resolve()
IMITATE_ROOT = Path(os.environ.get("FASTLS_LOCAL_IMITATE", ROOT.parent / "imitate")).resolve()
REFS = ("fastls_ref", "ftransport_ref", "imitate_ref")
TARGETS = [
    dict(runner="windows-2022", target="windows-amd64", go="1.26.10", python="3.11"),
    dict(runner="ubuntu-24.04", target="linux-amd64", go="1.26.10", python="3.11"),
    dict(runner="ubuntu-24.04-arm", target="linux-arm64", go="1.26.10", python="3.11"),
    dict(runner="macos-15-intel", target="darwin-amd64", go="1.26.10", python="3.11"),
    dict(runner="macos-15", target="darwin-arm64", go="1.26.10", python="3.11"),
    dict(runner="ubuntu-24.04", target="linux-amd64", go="1.27.3", python="3.10"),
]


def package_version():
    # benchmarks/prepare.py loads this file with runpy from outside scripts/.
    # Keep local runner imports lazy so that read-only preflight stays usable.
    from package_version import package_version as resolve_package_version
    return resolve_package_version()



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
            output.write("matrix=" + json.dumps(TARGETS, separators=(",", ":")) + "\n")
    summary("Resolved immutable dependency commits: " + json.dumps(resolved))
    return 0


def preflight(env):
    capabilities = {
        "fastls/index.go": ("DoContext(", "VerifyTLS", "TLSRootCAs", "ProxyTLSRootCAs", "RequestHasBody"),
        "fastls/profile/go.mod": ("github.com/FastTLS/fastls/profile",),
        "imitate/go.mod": ("github.com/FastTLS/imitate",),
        "ftransport/go.mod": ("github.com/FastTLS/ftransport",),
        "ftransport/config_test.go": ("func TestExplicitHTTP2SettingsPreserveEmptyAndZero",),
        "ftransport/f2_test.go": ("func TestHTTP2ConfiguredWireIdentity", "func TestHTTP2Streams64MiBWithSlowConsumer"),
        "fastls/session.go": ("func NewSession", "Duplex"),
        "fastls/proxy_transport.go": ("func ValidateProxy", "type tunnelProxyDialer"),
        "fastls/websocket_context.go": ("func DialWebSocketContext", "wsHandshakeConn"),
        "fastls/duplex_test.go": ("func TestDuplexEarlyHeadersUploadLifetime",),
        "fastls/cookie_store.go": ("func NewCookieStore",),
        "fastls/timeouts.go": ("type Timeouts struct",),
        "fastls/decode.go": ("func decodeResponse",),
        "ftransport/transport_test.go": ("func TestPooledDialWaiterCancellationIsolation", "func TestHTTP2FullDuplexRemainsActiveUntilBothDirectionsFinish"),
    }
    for relative, markers in capabilities.items():
        repository, _, child = relative.partition("/")
        path = {"fastls": FASTLS_ROOT, "ftransport": FTRANSPORT_ROOT, "imitate": IMITATE_ROOT}[repository] / child
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


def library_name():
    return "fastls.dll" if os.name == "nt" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"


def host_target():
    machine = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    system = {"win32": "windows", "linux": "linux", "darwin": "darwin"}.get(sys.platform)
    target = f"{system}-{machine}"
    if machine is None or system is None or os.environ.get("FASTLS_CI_TARGET", target) != target:
        raise RuntimeError("consumer runner architecture mismatch")
    return target


def sdk_inputs():
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    # Include new files during local development too. CI checkouts are committed.
    paths += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sorted(set(paths)) if p and (ROOT / p).is_file()}


def artifact_inputs():
    paths = [ROOT / "dist" / library_name(), ROOT / "dist" / "fastls.h", ROOT / "dist" / "build-info.json",
             ROOT / "dist" / "native-audit.json", ROOT / "go.work",
             ROOT / "build" / ("ffi-fixture.exe" if os.name == "nt" else "ffi-fixture")]
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one candidate wheel")
    sdists = list((ROOT / "dist").glob("*.tar.gz"))
    if len(sdists) != 1:
        raise RuntimeError("expected exactly one candidate sdist")
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths + wheels + sdists}


def save_inputs(target, sources):
    (ROOT / "dist" / "ci-inputs.json").write_text(json.dumps(dict(target=target, sources=sources,
        package_version=package_version(),
        sdk=sdk_inputs(), artifacts=artifact_inputs()), indent=2) + "\n", encoding="utf-8")


def verify_inputs(target, sources):
    record = json.loads((ROOT / "dist" / "ci-inputs.json").read_text(encoding="utf-8"))
    if record["target"] != target or record["sources"]["fastls-ffi"] != sources["fastls-ffi"]:
        raise RuntimeError("native artifacts belong to a different target or SDK commit")
    if record.get("package_version") != package_version():
        raise RuntimeError("package version changed after build")
    if record["sdk"] != sdk_inputs() or record["artifacts"] != artifact_inputs():
        raise RuntimeError("SDK or native artifacts changed after build")
    for name, revision in sources.items():
        if revision is not None and record["sources"][name] != revision:
            raise RuntimeError(f"upstream commit mismatch: {name}")


def run(stage="all"):
    # benchmarks/prepare.py loads preflight with runpy, without adding scripts
    # to sys.path. Keep runner-only imports out of that read-only entry point.
    from ci_runtime import EvidenceRunner

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Keep captured child logs and redirected Windows CI output in one encoding.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env["CGO_ENABLED"] = "1"
    try:
        target = preflight(env) if stage in ("all", "build", "contracts", "race", "release") else host_target()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        summary(str(error)); return 1
    evidence = ROOT / "build" / ("ci-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    evidence.mkdir(parents=True)
    success = False
    sources = {}
    for name, path in (("fastls-ffi", ROOT), ("fastls", FASTLS_ROOT), ("ftransport", FTRANSPORT_ROOT), ("imitate", IMITATE_ROOT)):
        if not path.is_dir():
            sources[name] = None
            continue
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
        sources[name] = revision.stdout.strip() if revision.returncode == 0 else None

    runner = EvidenceRunner(evidence / stage, {"target": target, "sources": sources, "stage": stage})

    def execute(name, args, cwd=ROOT, command_env=None):
        return runner.execute(name, args, cwd, command_env or env)

    try:
        if stage in ("all", "build"):
            sdk_before = sdk_inputs()
            for wheel in (ROOT / "dist").glob("*.whl"):
                wheel.unlink()
            for sdist in (ROOT / "dist").glob("*.tar.gz"):
                sdist.unlink()
            execute("build", [sys.executable, "scripts/build.py", "--local"])
            env["GOWORK"] = str(ROOT / "go.work")
            execute("release-materials", [sys.executable, "scripts/release_materials.py", "materials"], command_env=env)
            execute("fixture-build", ["go", "build", "-o", "build/ffi-fixture.exe" if os.name == "nt" else "build/ffi-fixture", "./cmd/ffi-fixture"])
            execute("native-audit", [sys.executable, "scripts/native_audit.py", str(ROOT / "dist" / library_name())])
            wheel_env = dict(env, FASTLS_BUNDLE_LIBRARY=str(ROOT / "dist" / library_name()))
            execute("wheel-build", [sys.executable, "-m", "pip", "wheel", "./python", "--no-deps", "-w", "dist"], command_env=wheel_env)
            execute("sdist-build", [sys.executable, "setup.py", "sdist", "--dist-dir", str(ROOT / "dist")], ROOT / "python")
            if sdk_inputs() != sdk_before:
                raise RuntimeError("SDK inputs changed during build; rebuild the candidate")
            save_inputs(target, sources)
        else:
            verify_inputs(target, sources)
            if os.name != "nt":
                (ROOT / "build" / "ffi-fixture").chmod(0o755)
        env["GOWORK"] = str(ROOT / "go.work")
        if stage in ("all", "contracts"):
            execute("dependency-gate-tests", [sys.executable, "tests/test_ci.py"])
            execute("core-bridge-python", [sys.executable, "scripts/check.py"])
            for name, path, pattern in (("profile", FASTLS_ROOT / "profile", "./..."), ("generator", IMITATE_ROOT, "./...")):
                execute(name, ["go", "test", pattern], path)
            execute("ftransport-contract", ["go", "test", "./...", "-v"], FTRANSPORT_ROOT)
            execute("ftransport-vet", ["go", "vet", "./..."], FTRANSPORT_ROOT)
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
            waiter = dist / ("c-wait.exe" if os.name == "nt" else "c-wait")
            execute("c-wait-build", compiler + ["examples/c/wait.c", "-I", str(dist), str(dist / library), "-o", str(waiter)])
            execute("c-wait-execute", [sys.executable, "tests/test_c_wait.py"], command_env=runtime_env)
            ws_consumer = dist / ("c-websocket.exe" if os.name == "nt" else "c-websocket")
            execute("c-websocket-build", compiler + ["examples/c/websocket.c", "-I", str(dist), str(dist / library), "-o", str(ws_consumer)])
            execute("c-websocket-execute", [sys.executable, "tests/test_c_websocket.py"], command_env=runtime_env)
        if stage in ("all", "source"):
            execute("python-source", [sys.executable, "tests/test_python.py"])
            execute("stability-source", [sys.executable, "tests/test_stability.py"])
            execute("network-source", [sys.executable, "tests/test_network.py"])
            execute("pooling-source", [sys.executable, "tests/test_pooling.py"])
            execute("cookies-source", [sys.executable, "tests/test_cookies.py"])
            execute("decode-timeouts-source", [sys.executable, "tests/test_decode_timeouts.py"])
            execute("upload-source", [sys.executable, "tests/test_upload.py"])
            execute("duplex-source", [sys.executable, "tests/test_duplex.py"])
            execute("sse-source", [sys.executable, "tests/test_sse.py"])
            execute("notifications-source", [sys.executable, "tests/test_notifications.py"])
            execute("proxy-source", [sys.executable, "tests/test_proxy.py"])
            execute("request-options-source", [sys.executable, "tests/test_request_options.py"])
            execute("websocket-source", [sys.executable, "tests/test_websocket.py"])
            execute("websocket-cookies-source", [sys.executable, "tests/test_websocket_cookies.py"])
        if stage in ("all", "wheel"):
            execute("wheel-tests", [sys.executable, "scripts/test_wheel.py", "--all"])
        if stage in ("all", "release"):
            execute("release-artifacts", [sys.executable, "scripts/release_materials.py", "artifacts"])
            execute("release-install", [sys.executable, "scripts/test_release.py"])
            execute("development-snapshot", [sys.executable, "scripts/development_snapshot.py", "create"])
        if stage == "race" or (stage == "all" and env.get("FASTLS_CI_RACE", "").lower() == "true"):
            execute("race-bridge", ["go", "test", "-race", "./internal/bridge"])
            execute("race-core", ["go", "test", "-race", "./..."], FASTLS_ROOT)
            execute("race-ftransport", ["go", "test", "-race", "./..."], FTRANSPORT_ROOT)
        if stage == "soak" or (stage == "all" and env.get("FASTLS_CI_RACE", "").lower() == "true"):
            soak_env = dict(env, FASTLS_STABILITY_SECONDS="300", FASTLS_TEST_LABEL="extended")
            execute("extended-soak", [sys.executable, "tests/test_stability.py", "Stability.test_soak_and_resource_recovery"], command_env=soak_env)
        verify_inputs(target, sources)
        success = True
    except (RuntimeError, ValueError, OSError, KeyError) as error:
        summary(str(error))
        return 1
    finally:
        runner.results["success"] = success
        runner.checkpoint()
    summary(f"Native checks passed on {target}; evidence: {evidence.name}. The aggregate workflow decides whether the certified candidate is retained or published on GitHub.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("resolve", "run"))
    parser.add_argument("--stage", choices=("all", "build", "contracts", "source", "wheel", "release", "race", "soak", "verify"), default="all")
    args = parser.parse_args()
    raise SystemExit(resolve() if args.command == "resolve" else run(args.stage))
