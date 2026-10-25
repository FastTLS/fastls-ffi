"""Create a manifest for a fully checked development or GitHub release candidate."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from package_version import package_version


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def one(directory, pattern, label):
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {label}")
    return matches[0]


def create_manifest(root=ROOT, environ=None):
    dist = root / "dist"
    inputs = json.loads((dist / "ci-inputs.json").read_text(encoding="utf-8"))
    environ = os.environ if environ is None else environ
    version = package_version(environ)
    formal = environ.get("FASTLS_RELEASE_MODE", "").lower() == "true"
    if not formal and ".dev" not in version:
        raise RuntimeError(f"development snapshots require a .devN version, got {version!r}")
    if inputs.get("package_version") != version:
        raise RuntimeError("candidate version differs from the immutable CI inputs")

    wheel = one(dist, "*.whl", "wheel")
    sdist = one(dist, "*.tar.gz", "sdist")
    build_info = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))
    if build_info.get("package_version") != version:
        raise RuntimeError("native build metadata belongs to a different package version")
    files = {}
    for path in (wheel, sdist, dist / "build-info.json", dist / "native-audit.json"):
        files[path.name] = {"sha256": sha256(path), "size": path.stat().st_size}

    manifest = {
        "schema": 1,
        "status": "official" if formal else "development-only",
        "publishable": formal,
        "package_version": version,
        "target": inputs["target"],
        "sources": inputs["sources"],
        "files": files,
    }
    destination = dist / ("release-manifest.json" if formal else "development-snapshot.json")
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create",))
    parser.parse_args()
    path = create_manifest()
    print(f"Candidate manifest created: {path}")
