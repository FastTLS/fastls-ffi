"""Publication boundary tests: mixed, incomplete and modified sets fail closed."""
from email.message import EmailMessage
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_pypi import PLATFORMS, prepare


class Publication(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.candidates = self.root / "candidates"
        self.candidates.mkdir()
        self.output = self.root / "output"
        self.sources = dict(zip(("fastls-ffi", "fastls", "ftransport", "imitate"), (c * 40 for c in "abcd")))

    def candidate(self, target, version="0.1.3", formal=True, sources=None):
        sources = self.sources if sources is None else sources
        directory = self.candidates / f"release-candidate-{target}-go1.26.10-py3.11"
        directory.mkdir()
        metadata = EmailMessage()
        metadata["Metadata-Version"] = "2.4"
        metadata["Name"] = "fastls"
        metadata["Version"] = version
        wheel_name = f"fastls-{version}-py3-none-{PLATFORMS[target]}.whl"
        library_name = "fastls.dll" if target.startswith("windows") else "libfastls.dylib" if target.startswith("darwin") else "libfastls.so"
        library = b"unique-native-library-" + target.encode()
        with zipfile.ZipFile(directory / wheel_name, "w") as wheel:
            wheel.writestr(f"fastls-{version}.dist-info/METADATA", metadata.as_bytes())
            wheel.writestr(f"fastls-{version}.dist-info/WHEEL", "Root-Is-Purelib: false\nTag: py3-none-" + PLATFORMS[target] + "\n")
            wheel.writestr("fastls/_lib/" + library_name, library)
        with tarfile.open(directory / f"fastls-{version}.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo(f"fastls-{version}/PKG-INFO")
            data = metadata.as_bytes()
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        (directory / "build-info.json").write_text(json.dumps({
            "package_version": version, "abi": 1, "sources": sources,
            "library_sha256": hashlib.sha256(library).hexdigest(),
        }))
        (directory / "native-audit.json").write_text("{}")
        files = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size": p.stat().st_size}
                 for p in directory.iterdir()}
        (directory / ("release-manifest.json" if formal else "development-snapshot.json")).write_text(json.dumps({
            "schema": 1, "target": target, "package_version": version, "sources": sources,
            "publishable": formal, "status": "official" if formal else "development-only", "files": files,
        }))
        return directory

    def complete(self, **kwargs):
        return [self.candidate(target, **kwargs) for target in PLATFORMS]

    def test_complete_set_contains_only_five_wheels_and_one_sdist(self):
        self.complete()
        prepare(self.candidates, self.output, "0.1.3", True)
        self.assertEqual(len(list(self.output.glob("*.whl"))), 5)
        self.assertEqual(len(list(self.output.glob("*.tar.gz"))), 1)
        self.assertEqual(len(list(self.output.iterdir())), 6)

    def test_missing_platform_rejects_entire_set(self):
        self.candidate("windows-amd64")
        with self.assertRaisesRegex(RuntimeError, "canonical candidate"):
            prepare(self.candidates, self.output, "0.1.3", True)
        self.assertFalse(self.output.exists())

    def test_modified_wheel_is_rejected(self):
        directory = self.complete()[0]
        next(directory.glob("*.whl")).write_bytes(b"modified")
        with self.assertRaisesRegex(RuntimeError, "artifact changed"):
            prepare(self.candidates, self.output, "0.1.3", True)
        self.assertFalse(self.output.exists())

    def test_mixed_source_commits_are_rejected(self):
        for index, target in enumerate(PLATFORMS):
            self.candidate(target, sources=dict(self.sources, fastls="e" * 40) if index == 1 else None)
        with self.assertRaisesRegex(RuntimeError, "different commits"):
            prepare(self.candidates, self.output, "0.1.3", True)

    def test_development_set_cannot_be_published_as_formal(self):
        self.complete(version="0.1.0.dev9", formal=False)
        with self.assertRaises(FileNotFoundError):
            prepare(self.candidates, self.output, "0.1.0.dev9", True)

    def test_development_set_can_be_validated_without_upload(self):
        self.complete(version="0.1.0.dev9", formal=False)
        prepare(self.candidates, self.output, "0.1.0.dev9", False)
        self.assertEqual(len(list(self.output.iterdir())), 6)

    def test_wrong_version_is_rejected(self):
        self.complete()
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            prepare(self.candidates, self.output, "0.1.4", True)


if __name__ == "__main__":
    unittest.main()
