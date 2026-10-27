"""Raw TLS/H2 black box: each pipelined SETTINGS must receive its own ACK."""
import argparse
import json
from pathlib import Path
import queue
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ACK=b"\x00\x00\x00\x04\x01\x00\x00\x00\x00"


def probe(fixture,count):
    endpoint=urlsplit(fixture["https2"])
    context=ssl.create_default_context(cafile=fixture["ca_file"]);context.set_alpn_protocols(["h2"])
    frames=b"".join(b"\x00\x00\x06\x04\x00\x00\x00\x00\x00"+struct.pack("!HI",4,6291456 if n%2==0 else 1048576) for n in range(count))
    received=0
    with socket.create_connection((endpoint.hostname,endpoint.port),timeout=3) as raw:
        with context.wrap_socket(raw,server_hostname=endpoint.hostname) as sock:
            assert sock.selected_alpn_protocol()=="h2"
            sock.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"+frames)
            sock.settimeout(.4);pending=bytearray();deadline=time.monotonic()+1
            while received<count and time.monotonic()<deadline:
                try:data=sock.recv(65536)
                except socket.timeout:continue
                if not data:break
                pending.extend(data)
                while len(pending)>=9:
                    length=int.from_bytes(pending[:3],"big")
                    if len(pending)<9+length:break
                    frame=bytes(pending[:9+length]);del pending[:9+length]
                    if frame[3]==4:
                        if frame[4]&1:
                            assert length==0,"nonempty SETTINGS ACK";received+=1
                        else:sock.sendall(ACK)
                    if frame[3]==7:raise AssertionError("server GOAWAY before acknowledging settings")
    return dict(sent=count,acknowledged=received,passed=received==count)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",required=True,type=Path);parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--rounds",type=int,default=20)
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    if not 1<=args.rounds<=100:parser.error("rounds must be 1..100")
    with args.output.with_suffix(".log").open("w",encoding="utf-8") as log:
        server=subprocess.Popen([str(args.fixture.resolve())],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            lines=queue.Queue();reader=threading.Thread(target=lambda:lines.put(server.stdout.readline()),daemon=True);reader.start()
            fixture=json.loads(lines.get(timeout=20));reader.join(2)
            results=[probe(fixture,8) for _ in range(args.rounds)]
        finally:
            server.stdin.close()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
            server.stdout.close()
    report=dict(rounds=results,success=all(r["passed"] for r in results))
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["success"] else 1


if __name__=="__main__":raise SystemExit(main())
