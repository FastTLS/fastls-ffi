"""Generate and verify release license and platform-boundary evidence."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "python"
NOTICE = PYTHON / "THIRD_PARTY_LICENSES.txt"
LINUX_GLIBC_FLOOR = (2, 39)


def json_stream(text):
    decoder, offset = json.JSONDecoder(), 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            return
        value, offset = decoder.raw_decode(text, offset)
        yield value


def module_licenses():
    env = dict(os.environ, CGO_ENABLED="1", GOWORK="off")
    output = subprocess.check_output(
        ["go", "list", "-deps", "-json", "./cmd/fastls-shared"],
        cwd=ROOT, env=env, text=True, encoding="utf-8",
    )
    modules = {}
    for package in json_stream(output):
        module = package.get("Module")
        if not module or module["Path"] == "github.com/FastTLS/fastls-ffi":
            continue
        resolved = module.get("Replace") or module
        directory = Path(resolved.get("Dir", ""))
        if not directory.is_dir():
            raise RuntimeError(f"module source is unavailable: {module['Path']}")
        modules[module["Path"]] = (module.get("Version") or "workspace", directory)

    result = []
    for name in sorted(modules, key=str.lower):
        version, directory = modules[name]
        candidates = []
        search = directory
        for _ in range(2):
            candidates = sorted(
                path for path in search.iterdir()
                if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "PATENTS"))
            )
            if candidates:
                break
            search = search.parent
        if not candidates:
            raise RuntimeError(f"module has no root license material: {name}")
        vendored = sorted(set(directory.glob("third_party/*.LICENSE")) | set(directory.glob("third_party/*/LICENSE")))
        candidates.extend(path for path in vendored if path not in candidates)
        result.append((name, version, candidates))
    return result


def render_notices():
    parts = [
        "Fastls FFI native-library third-party license materials",
        "Generated from the exact Go production dependency graph. Do not edit by hand.",
    ]
    for name, version, files in module_licenses():
        parts.extend(["", "=" * 78, f"Module: {name}", f"Version: {version}"])
        for path in files:
            parts.extend([f"File: {path.name}", "-" * 78, path.read_text(encoding="utf-8", errors="replace").rstrip()])
    return "\n".join(parts) + "\n"


def materials(write):
    if (ROOT / "LICENSE").read_bytes() != (PYTHON / "LICENSE").read_bytes():
        raise RuntimeError("repository and Python package LICENSE files differ")
    expected = render_notices()
    if write:
        NOTICE.write_text(expected, encoding="utf-8", newline="\n")
    elif not NOTICE.is_file() or NOTICE.read_text(encoding="utf-8") != expected:
        raise RuntimeError("THIRD_PARTY_LICENSES.txt is stale; run release_materials.py materials --write")


def one(pattern, label):
    matches = list((ROOT / "dist").glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label} in dist/")
    return matches[0]


def version_tuple(value):
    return tuple(int(part) for part in value.split("."))


def artifacts():
    wheel = one("*.whl", "wheel")
    sdist = one("*.tar.gz", "sdist")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for leaf in ("LICENSE", "THIRD_PARTY_LICENSES.txt"):
            if not any(".dist-info/licenses/" in name and name.endswith("/" + leaf) for name in names):
                raise RuntimeError(f"wheel is missing {leaf}")
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        for leaf in ("LICENSE", "README.md", "THIRD_PARTY_LICENSES.txt", "VERSION", "fastls/__init__.py"):
            if not any(name.endswith("/" + leaf) for name in names):
                raise RuntimeError(f"sdist is missing {leaf}")
    audit = json.loads((ROOT / "dist" / "native-audit.json").read_text(encoding="utf-8"))
    if sys.platform == "linux":
        value = audit.get("highest_glibc_symbol")
        if audit.get("format") != "ELF" or not value or version_tuple(value) > LINUX_GLIBC_FLOOR:
            raise RuntimeError(f"Linux library exceeds the certified glibc 2.39 boundary: {value!r}")
    print(f"Release materials verified: {wheel.name}, {sdist.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("materials", "artifacts"))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.command == "materials":
        materials(args.write)
    else:
        if args.write:
            parser.error("--write is only valid for materials")
        materials(False)
        artifacts()
