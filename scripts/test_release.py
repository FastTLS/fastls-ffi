"""Exercise sdist install, version upgrade, bundled-wheel load and uninstall."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

from package_version import package_version

ROOT = Path(__file__).resolve().parents[1]


def one(pattern, label):
    matches = list((ROOT / "dist").glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label} in dist/")
    return matches[0]


def run(args, *, cwd, env):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def probe(python, cwd, env, code, *args):
    run([python, "-I", "-c", code, *args], cwd=cwd, env=env)


def main():
    wheel, sdist = one("*.whl", "wheel"), one("*.tar.gz", "sdist")
    library = ROOT / "dist" / ("fastls.dll" if os.name == "nt" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so")
    candidate = package_version()
    clean = os.environ.copy()
    for name in ("FASTLS_LIBRARY", "FASTLS_PACKAGE_VERSION", "PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        clean.pop(name, None)

    with tempfile.TemporaryDirectory(prefix="fastls-release-") as temporary:
        temporary = Path(temporary)
        environment = temporary / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        pip = [python, "-m", "pip"]

        run(pip + ["install", "--no-deps", sdist], cwd=temporary, env=clean)
        probe(python, temporary, clean, """
import importlib.metadata, pathlib, sys
from fastls._native import Native
assert importlib.metadata.version('fastls-ffi') == sys.argv[2]
loaded = pathlib.Path(Native(library=sys.argv[1]).lib._name).resolve()
assert loaded == pathlib.Path(sys.argv[1]).resolve()
""", library, candidate)
        run(pip + ["uninstall", "-y", "fastls-ffi"], cwd=temporary, env=clean)

        old_dir = temporary / "old"
        old_dir.mkdir()
        old_env = dict(clean, FASTLS_PACKAGE_VERSION="0.0.0")
        run(pip + ["wheel", "--no-deps", "-w", old_dir, ROOT / "python"], cwd=temporary, env=old_env)
        old_wheel = one_from(old_dir, "*.whl", "baseline wheel")
        run(pip + ["install", "--no-deps", old_wheel], cwd=temporary, env=clean)
        probe(python, temporary, clean, "import importlib.metadata; assert importlib.metadata.version('fastls-ffi') == '0.0.0'")
        run(pip + ["install", "--no-deps", "--upgrade", "--force-reinstall", wheel], cwd=temporary, env=clean)
        probe(python, temporary, clean, """
import hashlib, importlib.metadata, json, pathlib, sys
from fastls._native import Native
assert importlib.metadata.version('fastls-ffi') == sys.argv[1]
loaded = pathlib.Path(Native().lib._name).resolve()
metadata = json.loads(loaded.with_name('build-info.json').read_text(encoding='utf-8'))
assert hashlib.sha256(loaded.read_bytes()).hexdigest() == metadata['library_sha256']
assert '_lib' in loaded.parts
""", candidate)
        run(pip + ["uninstall", "-y", "fastls-ffi"], cwd=temporary, env=clean)
        probe(python, temporary, clean, """
import importlib.metadata, importlib.util, pathlib, sysconfig
site = pathlib.Path(sysconfig.get_paths()['purelib'])
assert not (site / 'fastls').exists()
assert not list(site.glob('fastls_ffi-*.dist-info'))
try:
    importlib.metadata.version('fastls-ffi')
except importlib.metadata.PackageNotFoundError:
    pass
else:
    raise AssertionError('distribution metadata remained after uninstall')
""")
    print("sdist install, 0.0.0 upgrade, bundled load and uninstall checks passed")


def one_from(directory, pattern, label):
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label}")
    return matches[0]


if __name__ == "__main__":
    main()
