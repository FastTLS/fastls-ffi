"""One library, case and round per process. stdout carries lifecycle markers."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, asynccontextmanager, AsyncExitStack
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request

from common import EVENT, SLAB, Histogram

ROOT = Path(__file__).resolve().parents[1]
CLOCK = time.perf_counter


def emit(stage, ack=True, **values):
    print(json.dumps(dict(stage=stage, **values)), flush=True)
    # Keep the process alive until the external sampler has captured this exact
    # boundary, including after cleanup. No per-request sampler thread is added.
    if ack and not sys.stdin.readline():
        raise RuntimeError("benchmark controller disconnected")


def expected_digest(size, event=False):
    h = hashlib.sha256()
    if event:
        h.update(EVENT * 2)
    else:
        while size:
            part = SLAB[:min(size, len(SLAB))]; h.update(part); size -= len(part)
    return h.hexdigest()


def body(size):
    while size:
        part = SLAB[:min(size, len(SLAB))]; size -= len(part); yield part


class Metrics:
    def __init__(self):
        self.success = self.failed = self.bytes = 0
        self.events = 0
        self.latency, self.ttfb = Histogram(), Histogram()
        self.blocks, self.errors = Counter(), Counter()
        self.error_examples = []
        self.failure_details = []
        self.lock = threading.Lock()

    def add(self, elapsed, first, size, blocks, error=None, events=0, partial_bytes=0):
        with self.lock:
            if error is not None:
                self.failed += 1; self.errors[type(error).__name__] += 1
                if len(self.error_examples) < 5:
                    self.error_examples.append(f"{type(error).__name__}: {error}"[:400])
                    self.failure_details.append(dict(type=type(error).__name__,elapsed_seconds=elapsed,partial_body_bytes=partial_bytes))
            else:
                self.success += 1; self.bytes += size; self.events += events
                self.latency.add(elapsed); self.ttfb.add(first)
                self.blocks.update(blocks)

    def result(self, elapsed):
        return dict(success=self.success, failed=self.failed, errors=dict(self.errors), error_examples=self.error_examples,failure_details=self.failure_details,
                    successful_bytes=self.bytes, events=self.events, elapsed_seconds=elapsed,
                    rps=self.success / elapsed, mib_per_second=self.bytes / elapsed / 1048576,
                    p50_seconds=self.latency.percentile(.5), p95_seconds=self.latency.percentile(.95) if self.success>=20 else None,
                    ttfb_p50_seconds=self.ttfb.percentile(.5), ttfb_p95_seconds=self.ttfb.percentile(.95) if self.success>=20 else None,
                    quantile_method="2% multiplicative histogram upper bounds; successful complete responses only",
                    actual_read_chunk_sizes=dict(self.blocks))


class Client:

    def __init__(self, spec):
        self.spec = spec
        self.name = 'fastls' if spec['client'] == 'fastls-poll' else spec['client']
        if self.name not in ('requests', 'fastls'):
            raise ValueError('unsupported benchmark client')
        self.case = dict(spec['case'])
        if spec['client'] == 'fastls-poll':
            self.case['poll'] = True
        self.ca = spec['fixture']['ca_file']
        self.native_reference = None
        self.match = None
        self.session_setup_seconds = []
        started = CLOCK()
        if self.name == 'requests':
            import requests
            self.module = requests
        else:
            sdk = Path(spec.get('fastls_input', {}).get('python_path', ROOT / 'python')).resolve()
            sys.path.insert(0, str(sdk))
            import fastls
            if not Path(fastls.__file__).resolve().is_relative_to(sdk / 'fastls'):
                raise RuntimeError('Fastls module came from outside the selected SDK')
            self.module = fastls
        self.import_seconds = CLOCK() - started
        self.profile = 'chrome/156/windows' if self.case['browser'] else 'library/fastls/2/any/portable-' + self.case['protocol']
        self.expected_protocol = 'HTTP/1.1' if self.case['protocol'] == 'h1' else 'HTTP/2.0'
        self.expected_size = 128 if self.case['workload'] in ('idle', 'parsed') else self.case['size']
        self.digest = expected_digest(self.expected_size, self.case['workload'] in ('idle', 'parsed'))
        self.headers = {'User-Agent': 'fastls-benchmark/1', 'Accept': '*/*', 'Accept-Encoding': 'gzip' if self.case['gzip'] else 'identity'}

    def session(self, async_=False, wrong=False):
        ca = self.spec['fixture']['wrong_ca_file'] if wrong else self.ca
        if self.name == 'requests':
            session = self.module.Session()
            session.trust_env = False
            session.verify = ca
            return session
        cls = self.module.AsyncSession if async_ else self.module.Session
        session = cls(ca_file=ca, timeout=20, profile=self.profile, notifications='poll' if self.case['poll'] else 'required')
        self.native_reference = session
        return session

    def url(self, run, preflight=False):
        case = self.case
        workload = 'small' if preflight else case['workload']
        route = 'idle' if workload == 'parsed' else workload
        endpoint = self.spec['fixture']['https1' if case['protocol'] == 'h1' else 'https2']
        return f"{endpoint}/bench/{route}?run={run}&n={(1024 if preflight else case['size'])}&gzip={int(case['gzip'])}&hold_ms={self.spec['hold_ms']}"

    def options(self, preflight=False):
        options = dict(headers=self.headers, allow_redirects=False)
        if self.name == 'requests':
            options['timeout'] = (5, 20)
        if self.case['workload'] == 'upload' and (not preflight):
            options['data'] = body(self.case['size'])
        return options

    @contextmanager
    def stream(self, session, run, preflight=False):
        method = 'POST' if self.case['workload'] == 'upload' and (not preflight) else 'GET'
        if self.name == 'requests':
            with session.request(method, self.url(run, preflight), stream=True, **self.options(preflight)) as response:
                yield response
        else:
            with session.stream(method, self.url(run, preflight), **self.options(preflight)) as response:
                yield response

    @asynccontextmanager
    async def astream(self, session, run, preflight=False):
        method = 'POST' if self.case['workload'] == 'upload' and (not preflight) else 'GET'
        options = self.options(preflight)
        if method == 'POST':

            async def produce():
                for part in body(self.case['size']):
                    yield part
                    await asyncio.sleep(0)
            options['data'] = produce()
        async with session.stream(method, self.url(run, preflight), **options) as response:
            yield response

    def check(self, response):
        if response.status_code != 200 or response.headers.get('X-Test-Protocol') != self.expected_protocol:
            raise AssertionError(f'status/protocol mismatch: {response.status_code} {dict(response.headers)}')
        if self.match is None:
            self.match = getattr(response, 'match', None)

    def prime(self, session, run):
        with self.stream(session, run, True) as response:
            self.check(response)
            iterator = response.iter_bytes if self.name == 'fastls' else response.iter_content
            digest = hashlib.sha256()
            size = 0
            for chunk in iterator(chunk_size=65536):
                digest.update(chunk)
                size += len(chunk)
            if size != 1024 or digest.hexdigest() != expected_digest(1024):
                raise AssertionError('priming body mismatch')

    async def aprime(self, session, run):
        async with self.astream(session, run, True) as response:
            self.check(response)
            iterator = response.aiter_bytes if self.name == 'fastls' else response.aiter_content
            digest = hashlib.sha256()
            size = 0
            async for chunk in iterator(chunk_size=65536):
                digest.update(chunk)
                size += len(chunk)
            if size != 1024 or digest.hexdigest() != expected_digest(1024):
                raise AssertionError('priming body mismatch')

    def finish(self, count, digest, upload):
        if self.case['workload'] == 'upload':
            result = json.loads(upload)
            if result != {'bytes': self.case['size'], 'sha256': self.digest}:
                raise AssertionError('server upload hash/length mismatch')
            return self.case['size']
        if count != self.expected_size or digest.hexdigest() != self.digest:
            raise AssertionError(f'response hash/length mismatch: {count} != {self.expected_size}')
        return count

    def progress(self, response, count, started, previous):
        now = CLOCK()
        if self.case['workload'] != 'slow' or now - previous < 0.2:
            return previous
        buffered = None
        if self.name == 'fastls':
            buffered = self.native_reference.stats()['buffered_body_bytes']
        emit('progress', ack=False, consumed_bytes=count, library_buffered_bytes=buffered, elapsed=now - started)
        return now

    def perform(self, session, run, metrics):
        started = CLOCK()
        previous = started
        first = None
        count = 0
        blocks = Counter()
        digest = hashlib.sha256()
        upload = bytearray()
        try:

            def consume(chunk, response=None):
                nonlocal first, count, previous
                if chunk:
                    first = first if first is not None else CLOCK() - started
                    count += len(chunk)
                    digest.update(chunk)
                    blocks[len(chunk)] += 1
                    if self.case['workload'] == 'upload':
                        upload.extend(chunk)
                    if self.case['workload'] == 'slow':
                        time.sleep(max(0, count / self.spec['slow_bytes_per_second'] - (CLOCK() - started)))
                        previous = self.progress(response, count, started, previous)
                return len(chunk)
            with self.stream(session, run) as response:
                self.check(response)
                iterator = response.iter_bytes if self.name == 'fastls' else response.iter_content
                for chunk in iterator(chunk_size=64 if self.case['workload'] == 'idle' else 65536):
                    consume(chunk, response)
            size = self.finish(count, digest, upload)
            metrics.add(CLOCK() - started, first or CLOCK() - started, size, blocks)
        except Exception as error:
            metrics.add(CLOCK() - started, 0, 0, {}, error, partial_bytes=count)

    async def aperform(self, session, run, metrics):
        started = CLOCK()
        previous = started
        first = None
        count = 0
        blocks = Counter()
        digest = hashlib.sha256()
        upload = bytearray()
        try:
            if self.case['workload'] == 'parsed':
                events = 0
                async with session.sse('GET', self.url(run), headers=self.headers, allow_redirects=False) as stream:
                    async for event in stream:
                        if event.data != 'x' * 56:
                            raise AssertionError('SSE parser data mismatch')
                        first = first if first is not None else CLOCK() - started
                        events += 1
                if events != 2:
                    raise AssertionError('SSE event count mismatch')
                metrics.add(CLOCK() - started, first, events * 56, {}, events=events)
                return
            async with self.astream(session, run) as response:
                self.check(response)
                iterator = response.aiter_bytes if self.name == 'fastls' else response.aiter_content
                async for chunk in iterator(chunk_size=64 if self.case['workload'] == 'idle' else 65536):
                    if not chunk:
                        continue
                    first = first if first is not None else CLOCK() - started
                    count += len(chunk)
                    digest.update(chunk)
                    blocks[len(chunk)] += 1
                    if self.case['workload'] == 'upload':
                        upload.extend(chunk)
                    if self.case['workload'] == 'slow':
                        await asyncio.sleep(max(0, count / self.spec['slow_bytes_per_second'] - (CLOCK() - started)))
                        previous = self.progress(response, count, started, previous)
            size = self.finish(count, digest, upload)
            metrics.add(CLOCK() - started, first or CLOCK() - started, size, blocks)
        except Exception as error:
            metrics.add(CLOCK() - started, 0, 0, {}, error, partial_bytes=count)

    def stats(self):
        if self.native_reference is not None:
            return self.native_reference.stats()


def require_certificate_error(error):
    # A timeout/import failure must not count as rejecting an untrusted CA.
    message=str(error).lower()
    if not any(part in message for part in ("certificate", "unknown authority", "unable to get local issuer")):
        raise AssertionError(f"wrong-CA probe failed for a different reason: {error}") from error


def tls_probe(client):
    with client.session(wrong=True) as session:
        try:
            with client.stream(session,"wrong-ca",True) as response:
                raise AssertionError("wrong CA was accepted")
        except AssertionError:
            raise
        except Exception as error:
            require_certificate_error(error)
    with client.session() as session:
        with client.stream(session,"preflight",True) as response:
            client.check(response)
            iterator=response.iter_bytes if client.name=="fastls" else response.iter_content
            h=hashlib.sha256(); size=0
            for chunk in iterator(chunk_size=65536): h.update(chunk); size+=len(chunk)
            if size != 1024 or h.hexdigest()!=expected_digest(1024): raise AssertionError("preflight hash")


def server_stats(spec):
    url=spec["fixture"]["http"]+"/bench/stats?delete=1&run="+spec["run_id"]
    # Explicitly bypass environment proxies for control traffic too.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        with opener.open(url,timeout=5) as response: stats=json.load(response)
        if stats["active"]==0:
            for suffix in ("-warm","-prime"):
                with opener.open(url+suffix,timeout=5) as response: response.read()
            return stats
        time.sleep(.01)
    raise AssertionError("server retained active benchmark requests")


def run_sync(client, spec):
    count=client.case["concurrency"]; metrics=Metrics(); warm=Metrics()
    ready=threading.Barrier(count+1,timeout=60); done=threading.Barrier(count+1,timeout=120)
    cleanup=threading.Event(); start=threading.Event(); shared={}
    warm_started=CLOCK()
    def worker():
        session=None
        try:
            if not client.case["cold"]:
                setup=CLOCK();session=client.session();client.prime(session,spec["run_id"]+"-prime")
                client.session_setup_seconds.append(CLOCK()-setup)
            warm_deadline=CLOCK()+spec["warmup_seconds"]
            while CLOCK()<warm_deadline:
                if session is None:
                    with client.session() as fresh: client.perform(fresh,spec["run_id"]+"-warm",warm)
                else: client.perform(session,spec["run_id"]+"-warm",warm)
            ready.wait(); start.wait()
            once=False
            while (not once if client.case["workload"]!="small" else CLOCK()<shared["deadline"]):
                once=True
                if session is None:
                    with client.session() as fresh: client.perform(fresh,spec["run_id"],metrics)
                else: client.perform(session,spec["run_id"],metrics)
            done.wait(); cleanup.wait()
        finally:
            if session is not None: session.close()
    with ThreadPoolExecutor(max_workers=count) as pool:
        jobs=[pool.submit(worker) for _ in range(count)]
        ready.wait()
        warm_elapsed=CLOCK()-warm_started
        emit("measure_start",native=client.stats()); began=CLOCK(); shared["deadline"]=began+spec["seconds"]; start.set()
        try:
            done.wait(); elapsed=CLOCK()-began; emit("measure_stop")
        finally:
            cleanup.set()
        for job in jobs: job.result()
    return metrics.result(elapsed),warm.result(max(warm_elapsed,1e-6))


async def run_async(client,spec):
    metrics=Metrics(); warm=Metrics(); count=client.case["concurrency"]
    async with AsyncExitStack() as stack:
        setup=CLOCK()
        session=None if client.case["cold"] else await stack.enter_async_context(client.session(async_=True))
        if session is not None:
            # Establish the shared H2 connection before concurrent warmup. Record
            # this setup explicitly; first-parallel startup has its own probe.
            for _ in range(count):
                await client.aprime(session,spec["run_id"]+"-prime")
            client.session_setup_seconds.append(CLOCK()-setup)
        async def operation(run,target):
            if client.case["cold"]:
                async with client.session(async_=True) as fresh: await client.aperform(fresh,run,target)
            else: await client.aperform(session,run,target)
        warm_started=CLOCK(); deadline=warm_started+spec["warmup_seconds"]
        async def prewarm():
            while CLOCK()<deadline: await operation(spec["run_id"]+"-warm",warm)
        await asyncio.gather(*(prewarm() for _ in range(count)))
        warm_elapsed=CLOCK()-warm_started
        emit("measure_start",native=client.stats()); began=CLOCK(); deadline=began+spec["seconds"]
        async def work():
            once=False
            while (not once if client.case["workload"]!="small" else CLOCK()<deadline):
                once=True; await operation(spec["run_id"],metrics)
        await asyncio.gather(*(work() for _ in range(count)))
        elapsed=CLOCK()-began; emit("measure_stop")
    return metrics.result(elapsed),warm.result(max(warm_elapsed,1e-6))


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    client = Client(spec)
    tls_probe(client)
    emit('initialized', import_seconds=client.import_seconds, module_path=client.module.__file__, profile=client.profile if client.name == 'fastls' else None)
    result, warm = asyncio.run(run_async(client, spec)) if client.case['mode'] == 'async' else run_sync(client, spec)
    stats = server_stats(spec)
    protocols = {c['protocol'] for c in stats['connections'].values()}
    contract_errors = []
    if protocols != {client.expected_protocol}:
        contract_errors.append(f'server protocols: {protocols}')
    connections = len(stats['connections'])
    if client.case['cold'] and connections != stats['requests']:
        contract_errors.append('cold requests unexpectedly reused a connection')
    if not client.case['cold'] and (not client.case.get('allow_nonreuse')) and (stats['requests'] > client.case['concurrency'] * 2) and (connections >= stats['requests']):
        contract_errors.append('warm requests did not reuse connections')
    if stats['requests'] != result['success'] + result['failed']:
        if stats['requests'] > result['success'] + result['failed']:
            contract_errors.append('unaccounted server requests/retries')
    time.sleep(0.15)
    native = client.stats()
    if native is not None and any((native[k] for k in ('open_requests', 'workers', 'notifiers', 'open_sessions'))):
        contract_errors.append(f'native handles not recovered: {native}')
    emit('closed', native=native)
    result.update(server=stats, warmup=warm, native_final=native, tls_wrong_ca_rejected=True, contract_errors=contract_errors, import_seconds=client.import_seconds, session_setup_including_tls_probe_seconds=client.session_setup_seconds, configuration=dict(profile=client.profile if client.name == 'fastls' else None, match=client.match))
    if client.name == 'fastls':
        result['configuration']['notification_mode'] = 'poll' if client.case['poll'] else 'native'
    Path(spec['result_file']).write_text(json.dumps(result, indent=2) + '\n')
    if not result['success'] or result['failed'] or warm['failed'] or contract_errors:
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
