"""Assemble five certified platform wheels, rejecting stale or mixed inputs."""
import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import zipfile

from package_version import package_version

PLATFORMS = {
    "windows-amd64": "win_amd64",
    "linux-amd64": "manylinux_2_39_x86_64",
    "linux-arm64": "manylinux_2_39_aarch64",
    "darwin-amd64": "macosx_15_0_x86_64",
    "darwin-arm64": "macosx_15_0_arm64",
}


def verify_candidate(directory, target, version, formal):
    manifest_name = "release-manifest.json" if formal else "development-snapshot.json"
    manifest = json.loads((directory / manifest_name).read_text(encoding="utf-8"))
    if (manifest.get("schema") != 1 or manifest.get("target") != target
            or manifest.get("package_version") != version
            or manifest.get("publishable") is not formal
            or manifest.get("status") != ("official" if formal else "development-only")):
        raise RuntimeError(f"candidate identity mismatch: {directory.name}")
    sources = manifest.get("sources", {})
    if set(sources) != {"fastls-ffi", "fastls", "ftransport", "imitate"} or any(
            not isinstance(value, str) or len(value) != 40
            or any(c not in "0123456789abcdef" for c in value) for value in sources.values()):
        raise RuntimeError("candidate must record all four immutable source commits")
    files = manifest.get("files", {})
    if len(files) != 4 or not {"build-info.json", "native-audit.json"}.issubset(files):
        raise RuntimeError("candidate manifest has an unexpected file inventory")
    for name, record in files.items():
        if Path(name).name != name or "/" in name or "\\" in name:
            raise RuntimeError("invalid manifest filename")
        path = directory / name
        if path.stat().st_size != record["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise RuntimeError(f"candidate artifact changed: {name}")
    expected_wheel = f"fastls-{version}-py3-none-{PLATFORMS[target]}.whl"
    expected_sdist = f"fastls-{version}.tar.gz"
    if set(files) != {expected_wheel, expected_sdist, "build-info.json", "native-audit.json"}:
        raise RuntimeError("unexpected package name, version or platform tag")
    build = json.loads((directory / "build-info.json").read_text(encoding="utf-8"))
    if build.get("package_version") != version or build.get("abi") != 1 or build.get("sources") != sources:
        raise RuntimeError("build information does not match the certified manifest")
    library = "fastls.dll" if target.startswith("windows") else "libfastls.dylib" if target.startswith("darwin") else "libfastls.so"
    with zipfile.ZipFile(directory / expected_wheel) as wheel:
        info = f"fastls-{version}.dist-info"
        metadata = Parser().parsestr(wheel.read(info + "/METADATA").decode())
        descriptor = wheel.read(info + "/WHEEL").decode()
        if metadata["Name"] != "fastls" or metadata["Version"] != version:
            raise RuntimeError("wheel metadata does not match the package identity")
        if "Root-Is-Purelib: false" not in descriptor.splitlines() or f"Tag: py3-none-{PLATFORMS[target]}" not in descriptor.splitlines():
            raise RuntimeError("wheel must have the certified non-pure platform tag")
        digest = hashlib.sha256(wheel.read("fastls/_lib/" + library)).hexdigest()
        if digest != build["library_sha256"]:
            raise RuntimeError("bundled library does not match native provenance")
    with tarfile.open(directory / expected_sdist, "r:gz") as archive:
        stream = archive.extractfile(f"fastls-{version}/PKG-INFO")
        if stream is None:
            raise RuntimeError("sdist is missing package metadata")
        metadata = Parser().parsestr(stream.read().decode())
        if metadata["Name"] != "fastls" or metadata["Version"] != version:
            raise RuntimeError("sdist metadata does not match the package identity")
    return sources, directory / expected_wheel, directory / expected_sdist


def prepare(candidates, output, version, formal):
    if output.exists():
        raise RuntimeError("output directory must not exist")
    validated, baseline = [], None
    for target in PLATFORMS:
        matches = list(candidates.glob(f"*-{target}-go1.26.10-py3.11"))
        if len(matches) != 1:
            raise RuntimeError(f"expected one canonical candidate for {target}")
        sources, wheel, sdist = verify_candidate(matches[0], target, version, formal)
        if baseline is not None and sources != baseline:
            raise RuntimeError("platform candidates were built from different commits")
        baseline = sources
        validated.append((target, wheel, sdist))
    output.mkdir(parents=True)
    for target, wheel, sdist in validated:
        shutil.copy2(wheel, output / wheel.name)
        if target == "linux-amd64":
            shutil.copy2(sdist, output / sdist.name)
    print(f"Prepared {len(validated)} platform wheels and one SDK-only sdist; formal={formal}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("candidates", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--formal", action="store_true")
    args = parser.parse_args()
    prepare(args.candidates, args.output, package_version(), args.formal)
