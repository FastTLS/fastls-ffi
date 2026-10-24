"""Record the candidate's actual binary requirements and available license texts.

This is build tooling only: the installed package needs neither Python nor Go.
Missing license grants are recorded as release blockers, never invented.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8", env={**os.environ, "CGO_ENABLED": "1"})


def json_stream(text):
    decoder = json.JSONDecoder()
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        yield value
        text = text.lstrip()[end:]


def binary_metadata(path, platform, arch):
    data = path.read_bytes()
    if platform == "win32":
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[:2] != b"MZ" or data[pe:pe+4] != b"PE\0\0" or struct.unpack_from("<H", data, pe+4)[0] != 0x8664 or arch != "x64":
            raise ValueError("expected Windows x64 PE library")
        # Read the import directory without depending on an installed compiler.
        count = struct.unpack_from("<H", data, pe+6)[0]
        optional_size = struct.unpack_from("<H", data, pe+20)[0]
        optional = pe+24
        if struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise ValueError("expected PE32+ library")
        sections = [struct.unpack_from("<IIII", data, optional+optional_size+i*40+8) for i in range(count)]

        def offset(rva):
            for virtual_size, address, size, raw in sections:
                if address <= rva < address+max(virtual_size, size):
                    return raw+rva-address
            raise ValueError("invalid PE import address")

        imports = []
        rva = struct.unpack_from("<I", data, optional+112+8)[0]
        position = offset(rva)
        while any(data[position:position+20]):
            name = offset(struct.unpack_from("<I", data, position+12)[0])
            imports.append(data[name:data.index(b"\0", name)].decode("ascii"))
            position += 20
        return {"system": "Windows 10 or later (candidate tested on Windows Server 2022)", "dependencies": sorted(imports)}
    if platform == "linux":
        if data[:4] != b"\x7fELF" or data[4:6] != b"\x02\x01" or struct.unpack_from("<H", data, 18)[0] != {"x64": 62, "arm64": 183}[arch]:
            raise ValueError("unexpected Linux ELF architecture")
        versions = run("readelf", "--version-info", str(path))
        glibc = re.findall(r"GLIBC_(\d+)\.(\d+)", versions)
        if not glibc:
            raise ValueError("candidate must declare a measured glibc requirement")
        minimum = max((int(a), int(b)) for a, b in glibc)
        dynamic = run("readelf", "-d", str(path))
        return {"libc": "glibc", "glibc": ".".join(map(str, minimum)),
                "dependencies": re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)}
    if platform == "darwin":
        if struct.unpack_from("<I", data)[0] != 0xFEEDFACF or struct.unpack_from("<I", data, 4)[0] != {"x64": 0x1000007, "arm64": 0x100000C}[arch]:
            raise ValueError("unexpected Mach-O architecture")
        commands = run("otool", "-l", str(path))
        versions = re.findall(r"\bminos\s+(\d[\d.]*)", commands) or re.findall(r"\bversion\s+(\d[\d.]*)", commands)
        if not versions:
            raise ValueError("Mach-O has no minimum operating system version")
        return {"macos": versions[0], "dependencies": [line.strip().split(" (", 1)[0] for line in run("otool", "-L", str(path)).splitlines()[2:]]}
    raise ValueError("unsupported candidate platform")


def main():
    destination, platform, arch, library = sys.argv[1:]
    destination, library = Path(destination), Path(library).resolve()
    build = json.loads((ROOT/"dist/build-info.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(library.read_bytes()).hexdigest()
    if build["library_sha256"] != digest or build["abi"] != 1:
        raise ValueError("library does not match native build provenance")
    source_digest = runpy.run_path(str(ROOT/"scripts/build.py"))["source_digest"]
    if source_digest({**os.environ, "CGO_ENABLED": "1"}) != build["workspace_source"]:
        raise ValueError("native sources changed since compilation; rebuild before packaging")
    modules = {}
    for package in json_stream(run("go", "list", "-deps", "-json", "./cmd/fastls-shared")):
        module = package.get("Module")
        if module:
            modules[module["Path"]] = module
    notices = ["Fastls native candidate: collected source license/notice texts.",
               "This file is an inventory, not a grant of rights. Missing grants block public release.\n"]
    missing, provenance = [], []
    modules["Go runtime"] = {"Path": "Go runtime", "Dir": run("go", "env", "GOROOT").strip(), "Version": build["go"]}
    for name, module in sorted(modules.items()):
        source = module.get("Replace", module)
        directory = Path(source["Dir"])
        files = sorted(p for p in directory.iterdir() if p.is_file() and re.match(r"^(LICENSE|COPYING|NOTICE|PATENTS)(?:[.-].*)?$", p.name, re.I))
        # Workspace submodules can inherit a repository license from their parent.
        if not files and module["Path"].startswith("github.com/FastTLS/") and (directory.parent/".git").exists():
            files = sorted(p for p in directory.parent.iterdir() if p.is_file() and re.match(r"^(LICENSE|COPYING|NOTICE|PATENTS)(?:[.-].*)?$", p.name, re.I))
        record = {"module": name, "version": module.get("Version"), "sum": module.get("Sum"), "notices": [p.name for p in files]}
        if module["Path"].startswith("github.com/FastTLS/"):
            record["revision"] = subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
        provenance.append(record)
        notices.append(f"\n===== {name} {record['version'] or record.get('revision', '')} =====\n")
        if not any(p.name.upper().startswith(("LICENSE", "COPYING")) for p in files):
            missing.append(name)
            notices.append("No root LICENSE/COPYING found; public release requires license review.\n")
        for path in files:
            notices.append(f"\n--- {path.name} ---\n{path.read_text(encoding='utf-8', errors='replace')}\n")
    manifest = {"schema": 1, "platform": platform, "arch": arch, "abi": 1, "file": library.name,
                "sha256": digest, "requirements": binary_metadata(library, platform, arch),
                "build": {key: value for key, value in build.items() if key != "cc"},
                "sources": provenance, "release": {"candidateOnly": True, "licenseReviewRequired": missing}}
    if source_digest({**os.environ, "CGO_ENABLED": "1"}) != build["workspace_source"]:
        raise ValueError("native sources changed during metadata collection")
    destination.mkdir(parents=True, exist_ok=True)
    (destination/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    (destination/"THIRD_PARTY_NOTICES.txt").write_text("\n".join(notices), encoding="utf-8")


if __name__ == "__main__":
    main()
