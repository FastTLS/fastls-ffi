"""Black-box published/check-out SDK contracts against the independent Go fixture.

Each client/protocol runs in a new process. No performance comparison is made.
"""
import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import platform
import json
import os
from pathlib import Path
import queue
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"benchmarks"))
from run import fastls_input, versions
from worker import Client, Metrics, tls_probe


def control(spec,path,id):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(spec["fixture"]["http"]+path+"?id="+id,timeout=3) as response:
        return response.read()


async def wait_state(spec,id,key):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if json.loads(control(spec,"/state",id))[key]:
            return
        await asyncio.sleep(.02)
    raise AssertionError(f"server did not observe {key}")


@contextmanager
def connect_proxy(authority):
    """Independent CONNECT tunnel, restricted to this fixture's loopback port."""
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            line=self.rfile.readline(8193)
            words=line.split()
            if len(words)!=3 or words[:2]!=[b"CONNECT",authority.encode()] or words[2] not in (b"HTTP/1.0",b"HTTP/1.1"):
                self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            size=0
            while True:
                line=self.rfile.readline(8193);size+=len(line)
                if not line or size>16384:
                    return
                if line==b"\r\n":
                    break
            host,port=authority.rsplit(":",1)
            with socket.create_connection((host,int(port)),timeout=5) as upstream:
                self.server.connections+=1
                self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n");self.wfile.flush()
                while not self.server.stopping.is_set():
                    readable,_,_=select.select([self.request,upstream],[],[],.1)
                    for source in readable:
                        data=source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(data)
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address=True
        daemon_threads=True
        def handle_error(self,*args):
            # Session shutdown may reset a tunnel; tested by recovery below.
            pass
    with Server(("127.0.0.1",0),Handler) as server:
        server.connections=0;server.stopping=threading.Event()
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}",server
        finally:
            server.stopping.set();server.shutdown();thread.join(5)


def sync_contracts(client,spec):
    endpoint=spec["fixture"]["https1" if spec["case"]["protocol"]=="h1" else "https2"]
    results=[]
    def check(name,fn):
        try:
            evidence=fn()
            results.append(dict(name=name,status="passed",evidence=evidence))
        except Exception:
            results.append(dict(name=name,status="failed",error=traceback.format_exc()))

    def tls():
        tls_probe(client)
        return "private CA accepted; unrelated CA rejected; body and protocol verified"
    check("tls_body_protocol",tls)

    def transfer(workload):
        old=dict(client.case)
        try:
            client.case.update(workload=workload,gzip=workload=="gzip")
            # The client object's digest is unchanged: both transfer the same bytes.
            metrics=Metrics()
            with client.session() as session:
                client.perform(session,uuid.uuid4().hex,metrics)
            assert metrics.success==1 and metrics.failed==0,metrics.__dict__
            return dict(bytes=client.case["size"],sha256=client.digest)
        finally:
            client.case=old
    check("streaming_upload_sha256",lambda:transfer("upload"))
    check("gzip_decoded_sha256",lambda:transfer("gzip"))

    def cookies():
        with client.session() as session:
            response=session.get(endpoint+"/cookie-redirect",allow_redirects=True)
            data=response.json();cookie="; ".join(data["headers"].get("Cookie",[]))
            assert "first=one" in cookie and "second=two" in cookie,cookie
            assert str(response.url).endswith("/inspect"),response.url
            with client.session() as isolated:
                other=isolated.get(endpoint+"/inspect").json()
                assert "Cookie" not in other["headers"],other
        return "cookies from both redirect hops retained; new session isolated"
    check("redirect_cookie_session_isolation",cookies)

    def redirect():
        with client.session() as session:
            stopped=session.get(endpoint+"/redirect-code?code=302",allow_redirects=False)
            assert stopped.status_code==302,stopped.status_code
            response=session.post(endpoint+"/redirect-code?code=303",data=b"payload",allow_redirects=True)
            data=response.json()
            assert data["method"]=="GET" and not data["body"],data
        return "302 can be stopped; POST/303 becomes GET with empty body"
    check("redirect_method_and_disable",redirect)

    def truncated():
        with client.session() as session:
            try:
                session.get(endpoint+"/truncated")
            except Exception as error:
                message=(type(error).__name__+" "+str(error)).lower()
                assert any(word in message for word in ("eof","partial","incomplete","length","stream error","stream 1 reset","protocol_error")),repr(error)
                rejected=type(error).__name__
            else:
                raise AssertionError("truncated body treated as successful EOF")
            client.prime(session,uuid.uuid4().hex)
        return dict(error=rejected,recovery="same session successful")
    check("truncation_and_recovery",truncated)

    def timeout():
        id=uuid.uuid4().hex
        try:
            with client.session() as session:
                budget=.25 if client.name=="fastls" else (.25,.25)
                try:
                    session.get(endpoint+"/wait?id="+id,timeout=budget)
                except Exception as error:
                    assert any(word in str(error).lower() for word in ("timeout","timed out","deadline")),repr(error)
                    rejected=type(error).__name__
                else:
                    raise AssertionError("stalled request did not time out")
                asyncio.run(wait_state(spec,id,"cancelled"))
                client.prime(session,uuid.uuid4().hex)
            return dict(error=rejected,server_cancelled=True,recovery="same session successful")
        finally:
            control(spec,"/release",id)
    check("timeout_cancel_and_recovery",timeout)

    def proxy():
        with connect_proxy(urlsplit(endpoint).netloc) as (url,server):
            with client.session() as session:
                options=dict(proxy=url) if client.name=="fastls" else dict(proxies={"https":url})
                response=session.get(endpoint+"/inspect",**options)
                assert response.status_code==200
                assert response.json()["protocol"]==client.expected_protocol
                assert server.connections>=1,"proxy was silently bypassed"
            return dict(connects=server.connections,protocol=client.expected_protocol)
    check("https_connect_proxy",proxy)
    return results


async def async_contract(client, spec):
    endpoint = spec['fixture']['https1' if spec['case']['protocol'] == 'h1' else 'https2']
    a, b, c = (uuid.uuid4().hex for _ in range(3))
    previous = client.case['concurrency']
    client.case['concurrency'] = 2
    checks = []
    try:
        async with client.session(async_=True) as session:
            async with session.stream('GET', endpoint + '/hold?id=' + b) as sibling:
                iterator = sibling.aiter_bytes(5) if client.name == 'fastls' else sibling.aiter_content(chunk_size=5)
                assert await anext(iterator) == b'first'
                context = session.stream('GET', endpoint + '/hold?id=' + a)
                cancelled = await context.__aenter__()
                other = cancelled.aiter_bytes(5) if client.name == 'fastls' else cancelled.aiter_content(chunk_size=5)
                assert await anext(other) == b'first'
                closing = asyncio.create_task(context.__aexit__(None, None, None))
                try:
                    await asyncio.wait_for(asyncio.shield(closing), 2)
                except asyncio.TimeoutError:
                    control(spec, '/release', a)
                    await asyncio.wait_for(closing, 5)
                    checks.append(dict(name='async_early_close', status='failed', evidence='context exit waited >2s for remote body completion; completed after explicit server release'))
                else:
                    await wait_state(spec, a, 'cancelled')
                    checks.append(dict(name='async_early_close', status='passed', evidence='context exit cancelled held body on server within 2s'))
                control(spec, '/release', b)
                assert await anext(iterator) == b'last!'
            task = asyncio.create_task(session.get(endpoint + '/wait?id=' + c))
            try:
                await wait_state(spec, c, 'started')
                task.cancel()
                try:
                    await asyncio.wait_for(task, 5)
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError('cancelled task returned normally')
                await wait_state(spec, c, 'cancelled')
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await client.aprime(session, uuid.uuid4().hex)
        checks.append(dict(name='async_cancel_sibling_isolation', status='passed', evidence='sibling body survived; pending task cancelled on server; same session recovered'))
        return checks
    finally:
        client.case['concurrency'] = previous
        for id in (a, b, c):
            control(spec, '/release', id)


def child(spec):
    client=Client(spec)
    results=sync_contracts(client,spec)
    if client.name=="requests":
        results.append(dict(name="async_cancel_sibling_isolation",status="not_applicable",reason="standard Requests is synchronous"))
    else:
        try:
            results.extend(asyncio.run(asyncio.wait_for(async_contract(client,spec),20)))
        except Exception:
            results.append(dict(name="async_cancel_sibling_isolation",status="failed",error=traceback.format_exc()))
    if client.name=="fastls":
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            stats=client.stats()
            if all(stats[key]==0 for key in ("open_sessions","open_requests","workers","notifiers")):
                results.append(dict(name="native_handle_recovery",status="passed",evidence=stats))
                break
            time.sleep(.02)
        else:
            results.append(dict(name="native_handle_recovery",status="failed",evidence=stats))
    print(json.dumps(dict(client=spec["client"],protocol=spec["case"]["protocol"],module_path=client.module.__file__,checks=results)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--fastls-source', choices=('installed', 'checkout'), default='installed')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = fastls_input(args.fastls_source)
    packages = versions()
    driver_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
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
            for protocol, names in (('h1', ('requests', 'fastls')), ('h2', ('fastls',))):
                for name in names:
                    spec = dict(client=name, fixture=fixture, fastls_input=inputs, hold_ms=150, case=dict(protocol=protocol, browser=False, workload='download', size=262144, gzip=False, concurrency=1, poll=False))
                    (args.output / f'{name}-{protocol}-input.json').write_text(json.dumps(spec, indent=2) + '\n', encoding='utf-8')
                    try:
                        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child', json.dumps(spec)], capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
                        result = json.loads(run.stdout) if run.returncode == 0 else dict(client=name, protocol=protocol, checks=[dict(name='worker', status='failed', error=run.stderr)])
                    except subprocess.TimeoutExpired as error:
                        result = dict(client=name, protocol=protocol, checks=[dict(name='worker', status='failed', error=str(error))])
                    groups.append(result)
                    print(json.dumps(result), flush=True)
        finally:
            server.stdin.close()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            server.stdout.close()
    report = dict(fastls_input=inputs, packages=packages, python=sys.version, platform=platform.platform(), fixture_sha256=hashlib.sha256(args.fixture.read_bytes()).hexdigest(), driver_sha256=driver_digest, groups=groups, not_applicable=[dict(client='requests', protocol='h2', reason='standard Requests has no H2 support')], success=all((check['status'] != 'failed' for group in groups for check in group['checks'])) and driver_digest == hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if report['success'] else 1


if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--child":
        child(json.loads(sys.argv[2]))
    else:
        raise SystemExit(main())
