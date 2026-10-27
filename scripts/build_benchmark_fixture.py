"""Build an isolated benchmark fixture with one SETTINGS ACK per received frame.

Go 1.26.10's bundled H2 server can coalesce ACKs. The overlay changes only the
benchmark server build; it never writes GOROOT or rebuilds the shipped SDK.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BASE_SHA256="a2525871d4dff10f9cb1205aa1f4af94845729643bc1aa411a1ccf31ea161e2e"


def patch_source(source):
    for pattern,replacement in (
        (r"(needToSendSettingsAck\s+)bool",r"\g<1>int"),
        (r"if sc\.needToSendSettingsAck \{","if sc.needToSendSettingsAck > 0 {"),
        (r"sc\.needToSendSettingsAck = false","sc.needToSendSettingsAck--"),
        (r"sc\.needToSendSettingsAck = true","sc.needToSendSettingsAck++"),
    ):
        source,count=re.subn(pattern,replacement,source)
        if count!=1:raise RuntimeError("unexpected Go H2 source; review the overlay before using it")
    return source


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"build"/("ffi-fixture-benchmark.exe" if os.name=="nt" else "ffi-fixture-benchmark"))
    args=parser.parse_args();env=dict(os.environ,GOWORK="off",CGO_ENABLED="0")
    info=json.loads(subprocess.check_output(["go","env","-json","GOROOT","GOVERSION","GOOS","GOARCH"],env=env,text=True))
    if info["GOVERSION"]!="go1.26.10":raise SystemExit("benchmark overlay is audited only for Go 1.26.10")
    original=Path(info["GOROOT"])/"src/net/http/h2_bundle.go"
    raw=original.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=BASE_SHA256:raise SystemExit("Go H2 source checksum differs; no fallback to an unverified fixture")
    directory=ROOT/"build/settings-ack-overlay";directory.mkdir(parents=True,exist_ok=True)
    patched=directory/"h2_bundle.go";patched.write_text(patch_source(raw.decode()),encoding="utf-8",newline="\n")
    subprocess.run(["gofmt","-w",str(patched)],check=True,env=env)
    overlay=directory/"overlay.json"
    overlay.write_text(json.dumps({"Replace":{str(original.resolve()):str(patched.resolve())}},indent=2)+"\n",encoding="utf-8")
    args.output=args.output.resolve();args.output.parent.mkdir(parents=True,exist_ok=True)
    command=["go","build","-overlay",str(overlay.resolve()),"-o",str(args.output),"./cmd/ffi-fixture"]
    subprocess.run(command,cwd=ROOT,env=env,check=True)
    if original.read_bytes()!=raw:raise RuntimeError("GOROOT changed during build")
    metadata=dict(scope="benchmark fixture only; SDK native library unchanged",toolchain=info,
                  builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  upstream_file="src/net/http/h2_bundle.go",upstream_sha256=BASE_SHA256,
                  overlay_sha256=hashlib.sha256(patched.read_bytes()).hexdigest(),
                  fixture_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),command=command,
                  source_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
                  source_status=subprocess.check_output(["git","status","--short"],cwd=ROOT,text=True).splitlines(),
                  fixture_inputs={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [ROOT/"go.mod",ROOT/"go.sum",*(ROOT/"cmd/ffi-fixture").glob("*.go")]})
    args.output.with_suffix(".build.json").write_text(json.dumps(metadata,indent=2)+"\n",encoding="utf-8")
    print(args.output)


if __name__=="__main__":main()
