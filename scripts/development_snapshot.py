"""Create a non-publishing manifest for a fully checked development snapshot."""
import argparse
import hashlib
import json
from pathlib import Path

from package_version import require_development_version


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
    version = require_development_version(environ)
    if inputs.get("package_version") != version:
        raise RuntimeError("development snapshot version differs from the immutable CI inputs")

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
        "status": "development-only",
        "publishable": False,
        "package_version": version,
        "target": inputs["target"],
        "sources": inputs["sources"],
        "files": files,
    }
    destination = dist / "development-snapshot.json"
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create",))
    parser.parse_args()
    path = create_manifest()
    print(f"Development snapshot manifest created: {path}")
