"""Record host binary dependencies without claiming distribution certification."""
import json
from pathlib import Path
import re
import struct
import subprocess
import sys


def pe_imports(path):
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if data[pe:pe+4] != b"PE\0\0":
        raise RuntimeError("expected a PE library")
    count = struct.unpack_from("<H", data, pe+6)[0]
    optional_size = struct.unpack_from("<H", data, pe+20)[0]
    optional = pe+24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (112 if magic == 0x20b else 96)
    imports = struct.unpack_from("<I", data, directories+8)[0]
    sections = optional + optional_size

    def offset(rva):
        for i in range(count):
            base = sections+i*40
            size, address, raw_size, raw = struct.unpack_from("<IIII", data, base+8)
            if address <= rva < address+max(size, raw_size):
                return raw+rva-address
        raise RuntimeError("invalid PE import RVA")

    dependencies = []
    if imports:
        cursor = offset(imports)
        while any(data[cursor:cursor+20]):
            name = offset(struct.unpack_from("<I", data, cursor+12)[0])
            dependencies.append(data[name:data.index(b"\0", name)].decode("ascii"))
            cursor += 20
    return dict(format="PE", machine=hex(struct.unpack_from("<H", data, pe+4)[0]), imports=sorted(dependencies),
                note="Static import table only; does not enumerate runtime LoadLibrary calls.")


def audit(path):
    if sys.platform == "win32":
        return pe_imports(path)
    if sys.platform == "darwin":
        return dict(format="Mach-O", dependencies=subprocess.check_output(["otool", "-L", str(path)], text=True),
                    load_commands=subprocess.check_output(["otool", "-l", str(path)], text=True))
    dynamic = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    versions = subprocess.check_output(["readelf", "--version-info", str(path)], text=True)
    glibc = sorted(set(re.findall(r"GLIBC_(\d+\.\d+(?:\.\d+)?)", versions)), key=lambda s: tuple(map(int, s.split("."))))
    return dict(format="ELF", dynamic=dynamic, versions=versions, highest_glibc_symbol=glibc[-1] if glibc else None,
                note="Symbol requirements are evidence, not manylinux certification.")


if __name__ == "__main__":
    path = Path(sys.argv[1]).resolve()
    path.with_name("native-audit.json").write_text(json.dumps(audit(path), indent=2)+"\n", encoding="utf-8")
