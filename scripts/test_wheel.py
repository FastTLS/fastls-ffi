"""Install a platform wheel outside the checkout and use only its bundled library."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("python", "stability", "network", "pooling", "cookies", "decode-timeouts",
          "upload", "duplex", "sse", "notifications", "proxy", "request-options", "websocket", "websocket-cookies")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    for suite in SUITES[1:]:
        parser.add_argument("--" + suite, action="store_true")
    args = vars(parser.parse_args())
    suites = list(SUITES) if args["all"] else [s for s in SUITES[1:] if args[s.replace("-", "_")]] or ["python"]
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("expected exactly one platform wheel in dist/")
    with zipfile.ZipFile(wheels[0]) as wheel:
        descriptor = wheel.read(next(n for n in wheel.namelist() if n.endswith(".dist-info/WHEEL"))).decode()
        if "Root-Is-Purelib: false" not in descriptor or "Tag: py3-none-any" in descriptor:
            raise SystemExit("native candidate must have a platform tag and non-pure installation layout")
        library = "fastls.dll" if sys.platform == "win32" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"
        if "fastls/_lib/" + library not in wheel.namelist():
            raise SystemExit("native candidate is missing its platform library in platlib")
    env = os.environ.copy()
    for name in ("FASTLS_LIBRARY", "PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        env.pop(name, None)
    env["FASTLS_TEST_LABEL"] = "wheel"
    with tempfile.TemporaryDirectory(prefix="fastls-wheel-") as temporary:
        target = Path(temporary) / "site"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile",
                        "--target", str(target), str(wheels[0])], cwd=temporary, env=env, check=True)
        bootstrap = '''
import hashlib, json, os, pathlib, queue, runpy, subprocess, sys, threading
target, root, suite = map(pathlib.Path, sys.argv[1:])
target, root = target.resolve(), root.resolve()
sys.path.insert(0, str(target))
import fastls
from fastls._native import Native
assert pathlib.Path(fastls.__file__).is_relative_to(target), fastls.__file__
native = Native()
loaded = pathlib.Path(native.lib._name).resolve()
assert loaded.is_relative_to(target / 'fastls' / '_lib'), loaded
metadata = json.loads(loaded.with_name('build-info.json').read_text(encoding='utf-8'))
assert hashlib.sha256(loaded.read_bytes()).hexdigest() == metadata['library_sha256']
assert Native(library=str(loaded)).lib is native.lib
os.environ['FASTLS_LIBRARY'] = str(target / 'missing-library')
try:
    Native()
except fastls.FastlsError:
    pass
else:
    raise AssertionError('invalid explicit library silently fell back to bundle')
assert Native(library=str(loaded)).lib is native.lib
del os.environ['FASTLS_LIBRARY']
print('Installed SDK:', fastls.__file__, 'Bundled library:', loaded, flush=True)
if suite.name == 'probe':
    fixture = root / 'build' / ('ffi-fixture.exe' if os.name == 'nt' else 'ffi-fixture')
    process = subprocess.Popen([str(fixture)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        lab = json.loads(lines.get(timeout=15))
        with fastls.Session(ca_file=lab['ca_file'], profile='chrome/156/windows') as session:
            assert session.get(lab['https2'] + '/inspect').status_code == 200
            with session.websocket(lab['https1'].replace('http', 'ws', 1) + '/ws') as ws:
                ws.send('installed without compiler paths')
                assert ws.recv() == 'installed without compiler paths'
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()
    print('Clean installation probe passed with compiler search paths removed', flush=True)
    raise SystemExit(0)
sys.path.insert(0, str(root / 'tests'))
sys.argv = [str(suite)]
runpy.run_path(str(suite), run_name='__main__')'''
        probe_env = dict(env, PATH=str(target / "no-tools"))
        subprocess.run([sys.executable, "-I", "-c", bootstrap, str(target), str(ROOT), "probe"],
                       cwd=temporary, env=probe_env, check=True)
        for suite in suites:
            path = ROOT / "tests" / ("test_" + suite.replace("-", "_") + ".py")
            subprocess.run([sys.executable, "-I", "-c", bootstrap, str(target), str(ROOT), str(path)],
                           cwd=temporary, env=env, check=True)


if __name__ == "__main__":
    main()
