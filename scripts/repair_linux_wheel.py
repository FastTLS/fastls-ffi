"""Audit the Linux wheel before consumer tests; preserve the certified library."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def repair():
    if sys.platform != "linux":
        raise RuntimeError("manylinux certification requires a Linux host")
    arch = {"x86_64": "x86_64", "aarch64": "aarch64"}.get(platform.machine())
    if arch is None:
        raise RuntimeError("unsupported Linux wheel architecture")
    dist = ROOT / "dist"
    wheels = list(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one unrepaired wheel")
    original = wheels[0]
    expected = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))["library_sha256"]
    with tempfile.TemporaryDirectory(prefix="fastls-auditwheel-") as temporary:
        subprocess.run([sys.executable, "-m", "auditwheel", "repair", "--only-plat",
                        "--plat", f"manylinux_2_39_{arch}", "--wheel-dir", temporary,
                        str(original)], check=True)
        repaired = list(Path(temporary).glob("*.whl"))
        if len(repaired) != 1 or repaired[0].name == original.name:
            raise RuntimeError("auditwheel did not produce a manylinux wheel")
        with zipfile.ZipFile(repaired[0]) as wheel:
            digest = hashlib.sha256(wheel.read("fastls/_lib/libfastls.so")).hexdigest()
            if digest != expected:
                raise RuntimeError("auditwheel changed the certified library; rebuild and re-certify it")
        shutil.copy2(repaired[0], dist / repaired[0].name)
    original.unlink()
    print("Certified manylinux wheel:", repaired[0].name)


if __name__ == "__main__":
    repair()
