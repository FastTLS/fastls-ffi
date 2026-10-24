"""Candidate wheels bundle an explicitly selected, previously built host library.

Source installs do not compile or download native code. Set FASTLS_LIBRARY when
using an SDK-only installation. Platform candidates are built by scripts/ci.py.
"""
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import sys

from setuptools import setup
from setuptools.command.build_py import build_py
from wheel.bdist_wheel import bdist_wheel


class NativeBuild(build_py):
    def run(self):
        # An SDK-only rebuild must never inherit a previous bundled library.
        destination = Path(self.build_lib) / "fastls" / "_lib"
        if destination.exists():
            shutil.rmtree(destination)
        super().run()
        selected = os.environ.get("FASTLS_BUNDLE_LIBRARY")
        if selected:
            source = Path(selected).resolve()
            expected = "fastls.dll" if sys.platform == "win32" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"
            if source.name != expected:
                raise RuntimeError("bundled library must match the build host platform")
            metadata = json.loads(source.with_name("build-info.json").read_text(encoding="utf-8"))
            if hashlib.sha256(source.read_bytes()).hexdigest() != metadata["library_sha256"]:
                raise RuntimeError("bundled library differs from build-info.json")
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination / expected)
            (destination / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            shutil.copy2(source.with_name("native-audit.json"), destination / "native-audit.json")


class PlatformWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        if os.environ.get("FASTLS_BUNDLE_LIBRARY"):
            self.root_is_pure = False

    def get_tag(self):
        python, abi, tag = super().get_tag()
        if os.environ.get("FASTLS_BUNDLE_LIBRARY"):
            if sys.platform == "darwin":
                # Python's deployment target may predate the Go runtime.
                # Candidates conservatively require the tested host major.
                tag = f"macosx_{platform.mac_ver()[0].split('.')[0]}_0_{platform.machine()}"
            return "py3", "none", tag
        return python, abi, tag


setup(cmdclass={"build_py": NativeBuild, "bdist_wheel": PlatformWheel})
