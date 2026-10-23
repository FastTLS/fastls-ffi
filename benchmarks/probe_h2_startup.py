"""Independent curl_cffi H2 startup diagnostic; no Fastls library is loaded.

Observed transfer errors are data, not silently retried or classified as a
Fastls failure. The normal benchmark separately establishes warm connections.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import warnings

from curl_cffi import requests, CurlHttpVersion, Curl
from common import SLAB

ROOT=Path(__file__).resolve().parents[1]


async def scenario(info,warm,size,concurrency):
    run="startup-"+warm
    expected=hashlib.sha256()
    for _ in range(size//len(SLAB)):expected.update(SLAB)
    expected.update(SLAB[:size%len(SLAB)])
    async with requests.AsyncSession(verify=info["ca_file"],http_version=CurlHttpVersion.V2_0,
                                    trust_env=False,max_clients=concurrency,retry=0) as session:
        async def small():
            response=await session.get(info["https2"]+"/bench/small?n=1024&run=probe-warm",timeout=(2,3))
            if response.headers.get("X-Test-Protocol")!="HTTP/2.0":raise AssertionError("probe protocol")
        if warm=="parallel":await asyncio.gather(*(small() for _ in range(concurrency)))
        elif warm=="sequential":
            for _ in range(concurrency):await small()
        async def transfer():
            total=0;digest=hashlib.sha256();started=time.perf_counter()
            try:
                async with session.stream("GET",info["https2"]+f"/bench/download?n={size}&run={run}",timeout=(2,3),accept_encoding="identity") as response:
                    if response.headers.get("X-Test-Protocol")!="HTTP/2.0":raise AssertionError("probe protocol")
                    async for block in response.aiter_content():total+=len(block);digest.update(block)
                if total!=size or digest.hexdigest()!=expected.hexdigest():raise AssertionError("probe body")
                return dict(bytes=total,seconds=time.perf_counter()-started,success=True)
            except Exception as error:
                return dict(bytes=total,seconds=time.perf_counter()-started,success=False,error=str(error))
        return dict(warmup=warm,results=await asyncio.gather(*(transfer() for _ in range(concurrency))))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--size-mib",type=int,default=64);p.add_argument("--concurrency",type=int,default=8);args=p.parse_args()
    if not 1<=args.size_mib<=128 or not 1<=args.concurrency<=32:p.error("invalid diagnostic load")
    warnings.simplefilter("ignore",category=Warning)
    executable=ROOT/"build"/("ffi-fixture.exe" if os.name=="nt" else "ffi-fixture")
    process=subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        lines=queue.Queue();reader=threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True);reader.start()
        info=json.loads(lines.get(timeout=20));reader.join(2)
        curl=Curl()
        try:native=curl.version().decode()
        finally:curl.close()
        report=dict(diagnostic_only=True,curl_cffi=importlib.metadata.version("curl_cffi"),native=native,
                    size_bytes=args.size_mib*1048576,concurrency=args.concurrency,cases=[])
        for warm in ("none","parallel","sequential"):
            case=asyncio.run(scenario(info,warm,args.size_mib*1048576,args.concurrency));report["cases"].append(case)
            print(json.dumps(case),flush=True)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    finally:
        process.stdin.close()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        process.stdout.close()


if __name__=="__main__":main()
