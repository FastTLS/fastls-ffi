"""Mixed cancellation/slow-producer recovery for installed async clients.

No speed ranking. Memory guards reuse the existing stability suite's budgets;
no explicit GC is performed. Each client/protocol uses a fresh subprocess.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid

import psutil
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"benchmarks"))
from run import fastls_input, versions
from worker import Client, SLAB
from client_conformance import control, wait_state


def sample(client):
    process=psutil.Process()
    result=dict(rss_bytes=process.memory_info().rss,threads=process.num_threads(),
                handles=process.num_handles() if os.name=="nt" else process.num_fds())
    if client.name=="fastls":result["native"]=client.stats()
    return result


async def wait_uploaded(spec,id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        state=json.loads(control(spec,"/state",id))
        if state["bytes"]>=65536:return
        await asyncio.sleep(.01)
    raise AssertionError("server did not consume the slow upload")


async def round_trip(client,spec,extra_close=False):
    endpoint=spec["fixture"]["https1" if spec["case"]["protocol"]=="h1" else "https2"]
    async with client.session(async_=True) as session:
        async def normal():
            response=await session.get(endpoint+"/bytes?n=32768")
            assert response.content==SLAB[:32768]
            assert response.headers["X-Test-Protocol"]==client.expected_protocol
        async def cancel_pending():
            id=uuid.uuid4().hex
            task=asyncio.create_task(session.get(endpoint+"/wait?id="+id))
            try:
                await wait_state(spec,id,"started");task.cancel()
                try:await task
                except asyncio.CancelledError:pass
                else:raise AssertionError("cancelled request returned normally")
                await wait_state(spec,id,"cancelled")
            finally:
                control(spec,"/release",id)
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        async def upload(cancel=False):
            id=uuid.uuid4().hex;closed=asyncio.Event()
            async def source():
                try:
                    for _ in range(32 if not cancel else 10000):
                        yield SLAB[:16384]
                        await asyncio.sleep(.002)
                finally:closed.set()
            data=source()
            options={"data" if client.name=="fastls" else "content":data}
            task=asyncio.create_task(session.post(endpoint+"/upload-slow?id="+id,**options))
            try:
                if cancel:
                    await wait_uploaded(spec,id);task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
                    else:raise AssertionError("cancelled upload returned normally")
                    try:await wait_state(spec,id,"cancelled")
                    except AssertionError as failure:
                        raise AssertionError(f"upload cancellation observation: {control(spec,'/state',id).decode()}; source_closed={closed.is_set()}") from failure
                else:
                    response=await task;result=response.json()
                    assert result["bytes"]==32*16384 and result["sha256"]==hashlib.sha256(SLAB[:16384]*32).hexdigest()
                await asyncio.wait_for(closed.wait(),3)
            finally:
                control(spec,"/release",id)
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
                await data.aclose()
        async def close_body():
            id=uuid.uuid4().hex
            try:
                async with session.stream("GET",endpoint+"/hold?id="+id) as response:
                    iterator=response.aiter_bytes(5)
                    assert await anext(iterator)==b"first"
                await wait_state(spec,id,"cancelled")
            finally:control(spec,"/release",id)
        jobs=[normal() for _ in range(6)]+[cancel_pending() for _ in range(4)]+[upload(),upload(True)]
        if extra_close:jobs.extend(close_body() for _ in range(4))
        tasks=[asyncio.create_task(job) for job in jobs]
        try:await asyncio.wait_for(asyncio.gather(*tasks),30)
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        await normal()  # Same-session recovery after the mixed batch.
    if client.name=="fastls":
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=client.stats()
            if all(state[key]==0 for key in ("open_requests","open_sessions","workers","notifiers")):break
            await asyncio.sleep(.01)
        else:raise AssertionError(f"native handles not recovered: {state}")


def child(spec):
    client=Client(spec);started=time.monotonic();samples=[];error=None;baseline=final=None
    async def run():
        nonlocal baseline,final
        for _ in range(3):await round_trip(client,spec)
        baseline=sample(client)
        for index in range(spec["rounds"]):
            await round_trip(client,spec)
            samples.append(dict(round=index+1,**sample(client)))
            final=samples[-1]
        final=samples[-1]
        assert max(s["rss_bytes"] for s in samples)<=baseline["rss_bytes"]+128*1048576,"RSS peak growth"
        assert final["rss_bytes"]<=baseline["rss_bytes"]+96*1048576,"RSS final growth"
        assert final["handles"]<=baseline["handles"]+64,"descriptor/handle recovery"
        if client.name=="fastls":
            assert max(s["native"]["heap_inuse_bytes"] for s in samples)<=baseline["native"]["heap_inuse_bytes"]+96*1048576
            assert final["native"]["goroutines"]<=baseline["native"]["goroutines"]+16
            # Early stream closure is additional Fastls cancellation coverage.
            for _ in range(5):await round_trip(client,spec,extra_close=True)
        return baseline,final
    try:baseline,final=asyncio.run(run())
    except Exception:error=traceback.format_exc()
    print(json.dumps(dict(client=spec["client"],protocol=spec["case"]["protocol"],success=error is None,error=error,
                         completed_rounds=len(samples),seconds=time.monotonic()-started,baseline=baseline,final=final,samples=samples,
                         extra_early_close_rounds=5 if client.name=="fastls" and error is None else 0)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--rounds', type=int, default=50)
    parser.add_argument('--client', action='append', choices=('fastls',))
    parser.add_argument('--protocol', action='append', choices=('h1', 'h2'))
    args = parser.parse_args()
    if not 1 <= args.rounds <= 100:
        parser.error('rounds must be 1..100')
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = fastls_input('installed')
    packages = versions()
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for key in list(env):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'pythonpath', 'fastls_library'):
            env.pop(key)
    env['FASTLS_LIBRARY'] = inputs['library']
    groups = []
    with (args.output / 'fixture.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([str(args.fixture.resolve())], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(server.stdout.readline()), daemon=True)
            reader.start()
            fixture = json.loads(lines.get(timeout=20))
            reader.join(2)
            for protocol in args.protocol or ('h1', 'h2'):
                for name in args.client or ('fastls',):
                    spec = dict(client=name, fixture=fixture, fastls_input=inputs, rounds=args.rounds, case=dict(protocol=protocol, concurrency=16, browser=False, workload='download', size=32768, gzip=False, poll=False))
                    try:
                        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child', json.dumps(spec)], capture_output=True, text=True, encoding='utf-8', env=env, timeout=300)
                        result = json.loads(run.stdout) if run.returncode == 0 else dict(client=name, protocol=protocol, success=False, error=run.stderr)
                    except subprocess.TimeoutExpired as failure:
                        result = dict(client=name, protocol=protocol, success=False, error=str(failure))
                    groups.append(result)
                    print(json.dumps({k: v for k, v in result.items() if k != 'samples'}), flush=True)
        finally:
            server.stdin.close()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            server.stdout.close()
    report = dict(groups=groups, success=all((g['success'] for g in groups)), fastls_input=inputs, packages=packages, comparison_scope='shared mixed workload; Fastls-only early-close checks are separate; no speed ranking', driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), fixture_sha256=hashlib.sha256(args.fixture.read_bytes()).hexdigest())
    (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['success'] else 1


if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--child":child(json.loads(sys.argv[2]))
    else:raise SystemExit(main())
