"""Resume an upload from certified artifacts without rebuilding or moving a tag."""
import argparse
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import urllib.request
import zipfile

from prepare_pypi import PLATFORMS

REPOSITORY = "FastTLS/fastls-ffi"
GATES = {"All native tests", "Validate PyPI package set", "Publish GitHub release"}


def github_api(path):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/{path}",
        headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def validate_run(run_id, version):
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise RuntimeError("a numeric run ID and final X.Y.Z version are required")
    tag = "v" + version
    run = github_api(f"actions/runs/{run_id}")
    if (run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("path") != ".github/workflows/ci.yml" or run.get("event") != "push"
            or run.get("head_branch") != tag or run.get("status") != "completed"):
        raise RuntimeError("source run must be this repository's completed tag-push CI")
    ref = github_api(f"git/ref/tags/{tag}")["object"]
    for _ in range(3):
        if ref["type"] != "tag":
            break
        ref = github_api("git/tags/" + ref["sha"])["object"]
    if ref["type"] != "commit" or ref["sha"] != run["head_sha"]:
        raise RuntimeError("the published tag no longer matches the certified source commit")
    response = github_api(f"actions/runs/{run_id}/jobs?per_page=100&filter=latest")
    jobs = response["jobs"]
    if response["total_count"] != len(jobs):
        raise RuntimeError("incomplete job inventory")
    passed = {job["name"] for job in jobs if job["conclusion"] == "success"}
    if not GATES.issubset(passed):
        raise RuntimeError("native, package-set and GitHub-release gates must all have passed")
    if any(job["status"] != "completed" or job["conclusion"] != "success"
           for job in jobs if job["name"] not in {"Publish fastls to PyPI", "Resume certified PyPI upload"}):
        raise RuntimeError("a source-run check other than the upload has not passed")
    release = github_api(f"releases/tags/{tag}")
    if release.get("tag_name") != tag or release.get("draft") or release.get("prerelease"):
        raise RuntimeError("an existing official GitHub release is required")
    print(f"Certified upload source: run={run_id} tag={tag} commit={run['head_sha']}")
    return run, release


def verify_files(directory, version, commit, checksums):
    expected = {f"fastls-{version}-py3-none-{platform}.whl" for platform in PLATFORMS.values()}
    expected.add(f"fastls-{version}.tar.gz")
    if {path.name for path in directory.iterdir()} != expected:
        raise RuntimeError("upload must contain exactly five certified wheels and one sdist")
    for path in directory.iterdir():
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksums.get(path.name):
            raise RuntimeError(f"artifact differs from the published GitHub release: {path.name}")
    baseline = None
    for target, platform in PLATFORMS.items():
        with zipfile.ZipFile(directory / f"fastls-{version}-py3-none-{platform}.whl") as wheel:
            metadata = Parser().parsestr(wheel.read(f"fastls-{version}.dist-info/METADATA").decode())
            descriptor = wheel.read(f"fastls-{version}.dist-info/WHEEL").decode().splitlines()
            if metadata["Name"] != "fastls" or metadata["Version"] != version:
                raise RuntimeError("wheel package identity mismatch")
            if "Root-Is-Purelib: false" not in descriptor or f"Tag: py3-none-{platform}" not in descriptor:
                raise RuntimeError("wheel platform tag mismatch")
            build = json.loads(wheel.read("fastls/_lib/build-info.json"))
            sources = build.get("sources", {})
            if build.get("abi") != 1 or build.get("package_version") != version or sources.get("fastls-ffi") != commit:
                raise RuntimeError("native build information differs from the certified source")
            if baseline is not None and baseline != sources:
                raise RuntimeError("native builds have different source inputs")
            baseline = sources
            library = "fastls.dll" if target.startswith("windows") else "libfastls.dylib" if target.startswith("darwin") else "libfastls.so"
            if hashlib.sha256(wheel.read("fastls/_lib/" + library)).hexdigest() != build["library_sha256"]:
                raise RuntimeError("bundled native library checksum mismatch")
    with tarfile.open(directory / f"fastls-{version}.tar.gz") as archive:
        stream = archive.extractfile(f"fastls-{version}/PKG-INFO")
        if stream is None:
            raise RuntimeError("sdist is missing metadata")
        metadata = Parser().parsestr(stream.read().decode())
        if metadata["Name"] != "fastls" or metadata["Version"] != version:
            raise RuntimeError("sdist package identity mismatch")
    print("All six files match the certified source and published GitHub checksums")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate-run", "verify-files"))
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args()
    version = os.environ["PYPI_VERSION"]
    run, release = validate_run(os.environ["PYPI_SOURCE_RUN"], version)
    if args.command == "verify-files":
        asset = next(item for item in release["assets"] if item["name"] == "SHA256SUMS")
        expected_url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/SHA256SUMS"
        if asset["browser_download_url"] != expected_url:
            raise RuntimeError("unexpected published checksum URL")
        # No GitHub authorization header is sent through asset redirects.
        with urllib.request.urlopen(expected_url, timeout=30) as response:
            checksums = {parts[1].lstrip("*"): parts[0] for line in response.read().decode().splitlines()
                         if len(parts := line.split()) == 2}
        verify_files(args.directory, version, run["head_sha"], checksums)
