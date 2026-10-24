"""Build the host shared library. Uses local sibling repositories only with --local."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def source_digest(env):
    """Hash Go-selected inputs in workspace modules, including embedded assets."""
    output = subprocess.check_output(["go", "list", "-deps", "-json", "./cmd/fastls-shared"], cwd=ROOT, env=env, text=True, encoding="utf-8")
    decoder, offset, inputs = json.JSONDecoder(), 0, set()
    while offset < len(output):
        while offset < len(output) and output[offset].isspace():
            offset += 1
        if offset == len(output):
            break
        package, offset = decoder.raw_decode(output, offset)
        module = package.get("Module", {})
        if not module.get("Main"):
            continue
        directory = Path(package["Dir"])
        for key in ("GoFiles", "CgoFiles", "CFiles", "HFiles", "SFiles", "SysoFiles", "EmbedFiles"):
            inputs.update(directory / file for file in package.get(key, []))
        inputs.add(Path(module["GoMod"]))
    digest = hashlib.sha256()
    for path in sorted(inputs):
        digest.update(path.as_posix().encode()); digest.update(b"\0"); digest.update(path.read_bytes())
    return {"sha256": digest.hexdigest(), "input_count": len(inputs)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="use unpublished sibling modules through an ignored go.work")
    parser.add_argument("--cc", help="C compiler command; otherwise use CC or gcc")
    args = parser.parse_args()
    env = os.environ.copy()
    env["CGO_ENABLED"] = "1"
    if args.cc:
        env["CC"] = args.cc
    if args.local:
        fastls = Path(os.environ.get("FASTLS_LOCAL_FASTLS", ROOT.parent / "fastls")).resolve()
        ftransport = Path(os.environ.get("FASTLS_LOCAL_FTRANSPORT", ROOT.parent / "ftransport")).resolve()
        imitate = Path(os.environ.get("FASTLS_LOCAL_IMITATE", ROOT.parent / "imitate")).resolve()
        modules = [ROOT, fastls, fastls / "profile", ftransport, imitate]
        for path in modules:
            if not (path / "go.mod").is_file():
                raise SystemExit(f"missing local module: {path}")
        work = ROOT / "go.work"
        contents = "go 1.26.0\n\nuse (\n" + "".join("  " + json.dumps(Path(os.path.relpath(p, ROOT)).as_posix()) + "\n" for p in modules) + ")\n"
        # Bind unpublished versions even when graph loading needs their go.mod.
        versions = {
            "github.com/FastTLS/fastls": "v0.1.0-dev.20261023.8",
            "github.com/FastTLS/fastls/profile": "v0.1.0-dev.20261023.8",
            "github.com/FastTLS/ftransport": "v0.0.0-20261024124928-a1916e4d2523",
            "github.com/FastTLS/imitate": "v0.1.0-dev.20261023.8",
        }
        for path in modules[1:]:
            module = (path / "go.mod").read_text(encoding="utf-8").splitlines()[0].split()[1]
            contents += f"replace {module} {versions[module]} => " + json.dumps(Path(os.path.relpath(path, ROOT)).as_posix()) + "\n"
        work.write_text(contents, encoding="utf-8")
        env["GOWORK"] = str(work)
    else:
        env["GOWORK"] = "off"
    name = "fastls.dll" if sys.platform == "win32" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    before = source_digest(env)
    subprocess.run(["go", "build", "-buildmode=c-shared", "-o", str(out / name), "./cmd/fastls-shared"], cwd=ROOT, env=env, check=True)
    if source_digest(env) != before:
        raise SystemExit("source changed during compilation; rebuild before using this artifact")
    # cgo emits libfastls.h on Unix and fastls.h on Windows. Keep the public
    # include name stable so the same C consumer builds on every platform.
    generated_header = (out / name).with_suffix(".h")
    header = out / "fastls.h"
    if generated_header != header:
        shutil.copyfile(generated_header, header)
    (out / "build-info.json").write_text(json.dumps({
        "platform": platform.platform(), "machine": platform.machine(), "local_dependencies": args.local,
        "go": subprocess.check_output(["go", "version"], text=True).strip(),
        "cc": env.get("CC", "gcc"), "abi": 1,
        "workspace_source": before,
        "sources": {name: subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
                    for name, path in (("fastls-ffi", ROOT), ("fastls", fastls),
                                       ("ftransport", ftransport), ("imitate", imitate))} if args.local else {},
        "library_sha256": hashlib.sha256((out / name).read_bytes()).hexdigest(),
        "note": "Unpublished development build. A local workspace build does not validate remote dependency availability.",
    }, indent=2) + "\n", encoding="utf-8")
    print(out / name)


if __name__ == "__main__":
    main()
