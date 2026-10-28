"""Run reproducible loopback benchmarks. Never publishes or skips failed clients."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import queue
import random
import ssl
import subprocess
import sys
import threading
import time
import uuid
import urllib.request

import psutil
from common import cases
from report import render

ROOT=Path(__file__).resolve().parents[1]


def snapshot(process):
    # Windows venv python.exe can be a launcher for the actual interpreter.
    # Count the whole client process tree, including native runtimes and helpers.
    members=[]
    for member in [process,*process.children(recursive=True)]:
        try:
            with member.oneshot():
                cpu=member.cpu_times()
                members.append(dict(pid=member.pid,cpu_seconds=cpu.user+cpu.system,rss_bytes=member.memory_info().rss,
                    threads=member.num_threads(),descriptors=member.num_handles() if os.name=="nt" else member.num_fds()))
        except psutil.NoSuchProcess:
            continue
    if not members: raise psutil.NoSuchProcess(process.pid)
    return dict(at=time.perf_counter(),processes=members,
                **{key:sum(member[key] for member in members) for key in ("cpu_seconds","rss_bytes","threads","descriptors")})


def revision(path):
    def git(*args):
        return subprocess.check_output(["git",*args],cwd=path,text=True).strip()
    return dict(commit=git("rev-parse","HEAD"),dirty_files=git("status","--short").splitlines())


def versions():
    result={}
    for line in (ROOT/"benchmarks/requirements.lock").read_text().splitlines():
        if not line or line.startswith("#"): continue
        name,expected=line.split("==")
        actual=importlib.metadata.version(name)
        if actual!=expected: raise RuntimeError(f"benchmark lock mismatch: {name} {actual} != {expected}")
        result[name]=actual
    return result


def fastls_input(source):
    """Resolve an explicit SDK/native pair without importing its Go runtime."""
    filename="fastls.dll" if os.name=="nt" else "libfastls.dylib" if sys.platform=="darwin" else "libfastls.so"
    if source=="installed":
        distribution=importlib.metadata.distribution("fastls")
        package=Path(distribution.locate_file("fastls")).resolve()
        library=package/"_lib"/filename
        info=json.loads((library.parent/"build-info.json").read_text(encoding="utf-8"))
        if info.get("package_version")!=distribution.version:
            raise RuntimeError("installed SDK/native package version mismatch")
        if info.get("library_sha256")!=hashlib.sha256(library.read_bytes()).hexdigest():
            raise RuntimeError("installed native library does not match build-info SHA256")
        return dict(source=source,python_path=str(package.parent),library=str(library),
                    package_version=distribution.version,build_info=info,
                    sources=info.get("sources",{}))
    library=ROOT/"dist"/filename
    return dict(source=source,python_path=str(ROOT/"python"),library=str(library),
                build_info=json.loads((library.parent/"build-info.json").read_text(encoding="utf-8")),
                sources={name:revision(path) for name,path in (("fastls-ffi",ROOT),("fastls",ROOT.parent/"fastls"),("ftransport",ROOT.parent/"ftransport"),("imitate",ROOT.parent/"imitate"))})


def sdk_digests(inputs):
    package=Path(inputs["python_path"])/"fastls"
    return {str(path.relative_to(package)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(package.rglob("*.py"))}


def execute(spec,fixture_process,output):
    from observation import execute as observe
    return observe(spec,fixture_process,output,worker_script=ROOT/"benchmarks/worker.py",snapshot=snapshot)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', choices=('smoke', 'quick', 'full'), default='smoke')
    p.add_argument('--output', type=Path, default=ROOT / 'build' / 'benchmarks')
    p.add_argument('--case', action='append', help='exact case name; repeat to select several')
    p.add_argument('--rounds', type=int)
    p.add_argument('--fastls-source', choices=('checkout', 'installed'), default='checkout', help='installed measures the released wheel, never the checkout SDK')
    p.add_argument('--fixture', type=Path, help='explicit independently built loopback fixture')
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard-index', type=int, default=0)
    args = p.parse_args()
    if not args.case:
        args.case = [name.strip() for name in os.environ.get('BENCH_CASES', '').split(',') if name.strip()]
    if not 1 <= args.shards <= 32 or not 0 <= args.shard_index < args.shards:
        p.error('invalid shard range')
    rounds = dict(smoke=1, quick=2, full=5)[args.profile] if args.rounds is None else args.rounds
    if rounds < 1 or (args.profile == 'full' and rounds < 5):
        p.error('full requires at least five rounds')
    deps = versions()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixture = (args.fixture or ROOT / 'build' / ('ffi-fixture-benchmark.exe' if os.name == 'nt' else 'ffi-fixture-benchmark')).resolve()
    inputs = fastls_input(args.fastls_source)
    log = (output / 'fixture.log').open('w', encoding='utf-8')
    allcases = cases(args.profile)
    if args.case:
        missing = set(args.case) - {c['name'] for c in allcases}
        if missing:
            p.error('unknown cases: ' + str(missing))
        allcases = [c for c in allcases if c['name'] in args.case]
    selected = [c for i, c in enumerate(allcases) if i % args.shards == args.shard_index]
    if not selected:
        p.error('empty shard')
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + platform.system() + '-' + str(args.shard_index)
    library = Path(inputs['library'])
    report = dict(schema_version=2, resource_scope='recursive client process tree', environment_id=identifier, profile=args.profile, rounds=rounds, shard=args.shard_index, shards=args.shards, created_at=datetime.now(timezone.utc).isoformat(), python=sys.version, platform=platform.platform(), selected_case_names=[case['name'] for case in selected], machine=platform.machine(), cpu=platform.processor(), logical_cpus=psutil.cpu_count(), memory_bytes=psutil.virtual_memory().total, python_ssl=ssl.OPENSSL_VERSION, packages=deps, library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(), fastls_input=inputs, build_info=inputs['build_info'], sources=inputs['sources'], sdk_sources=sdk_digests(inputs), harness_source=revision(ROOT), fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(), fixture_build_info=json.loads(fixture.with_suffix('.build.json').read_text(encoding='utf-8')) if fixture.with_suffix('.build.json').exists() else None, requirements_sha256=hashlib.sha256((ROOT / 'benchmarks/requirements.lock').read_bytes()).hexdigest(), benchmark_sources={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT / 'benchmarks').glob('*.py')}, not_applicable=[dict(client='requests', protocol='h2', reason='standard Requests has no H2 client'), dict(clients=['requests'], workload='parsed', reason='native Fastls SSE parser is an independent measurement')], runs=[])
    config = {'smoke': (0.15, 0.03, 150, 1048576), 'quick': (1, 0.2, 1000, 16 * 1048576), 'full': (30, 10, 5000, 16 * 1048576)}[args.profile]
    server = subprocess.Popen([str(fixture)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        startup_lines = queue.Queue()
        reader = threading.Thread(target=lambda: startup_lines.put(server.stdout.readline()), daemon=True)
        reader.start()
        line = startup_lines.get(timeout=20)
        reader.join(2)
        fixture_info = json.loads(line)
        for round_number in range(rounds):
            ordered = list(selected)
            random.Random(1000 + round_number).shuffle(ordered)
            for case in ordered:
                clients = list(case['clients'])
                random.Random(2000 + round_number).shuffle(clients)
                for client in clients:
                    spec = dict(case=case, client=client, round=round_number + 1, fixture=fixture_info, run_id=uuid.uuid4().hex, fastls_input=inputs, seconds=config[0], warmup_seconds=config[1] if case['workload'] == 'small' else min(config[1], 0.05), hold_ms=config[2], slow_bytes_per_second=config[3])
                    try:
                        result = execute(spec, server, output)
                    except Exception as error:
                        result = dict(case=case, client=client, round=round_number + 1, valid=False, error=repr(error), metrics={})
                    report['runs'].append(result)
                    (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                    render([report], output / 'report.html')
                    print(json.dumps(dict(case=case['name'], client=client, round=round_number + 1, valid=result['valid'], success=result['metrics'].get('success'), failed=result['metrics'].get('failed'))), flush=True)
    finally:
        server.stdin.close()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        server.stdout.close()
        log.close()
    report['benchmark_sources_at_end'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT / 'benchmarks').glob('*.py')}
    report['library_sha256_at_end'] = hashlib.sha256(library.read_bytes()).hexdigest()
    report['sdk_sources_at_end'] = sdk_digests(inputs)
    report['requirements_sha256_at_end'] = hashlib.sha256((ROOT / 'benchmarks/requirements.lock').read_bytes()).hexdigest()
    report['fixture_sha256_at_end'] = hashlib.sha256(fixture.read_bytes()).hexdigest()
    report['source_changed_during_run'] = any((report[after] != report[before] for before, after in (('benchmark_sources', 'benchmark_sources_at_end'), ('library_sha256', 'library_sha256_at_end'), ('sdk_sources', 'sdk_sources_at_end'), ('requirements_sha256', 'requirements_sha256_at_end'), ('fixture_sha256', 'fixture_sha256_at_end'))))
    report['success'] = all((run['valid'] for run in report['runs'])) and (not report['source_changed_during_run'])
    (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    render([report], output / 'report.html')
    return 0 if report['success'] else 1


if __name__=="__main__":
    raise SystemExit(main())
