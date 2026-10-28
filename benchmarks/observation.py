"""Preserve failed benchmark observations and reap observed worker descendants."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

import psutil


def wall_budget(spec):
    # Keep the existing guard. Diagnostics must not turn a timeout into a pass.
    return spec['warmup_seconds'] + spec['seconds'] + 90


def fixture_snapshot(spec,token):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url=spec['fixture']['http']+'/bench/stats?run='+token
    with opener.open(url,timeout=5) as response:return json.load(response)


def execute(spec, fixture_process, output, *, worker_script, snapshot):
    output=Path(output);token=spec['run_id']
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',token):
        raise ValueError('invalid benchmark run identifier')
    paths={key:output/(token+suffix) for key,suffix in (
        ('input','-input.json'),('worker','-worker.json'),('stdout','-stdout.log'),
        ('stderr','-stderr.log'),('journal','-observation.jsonl'),('result','-run.json'))}
    spec=dict(spec,result_file=str(paths['worker']))
    if any(path.exists() for path in paths.values()):raise FileExistsError('benchmark run evidence already exists')
    budget=wall_budget(spec)
    if not math.isfinite(budget) or budget<=0:raise ValueError('invalid worker wall budget')
    paths['input'].write_text(json.dumps(spec)+'\n',encoding='utf-8')
    started=time.perf_counter();started_utc=datetime.now(timezone.utc).isoformat()
    events=queue.Queue();markers=[];samples=[];errors=[];stderr_chunks=[]
    process=None;child=None;server=None;threads=[];known={};cleanup={}
    base=finish=recovery=serverbase=serverfinish=None
    measuring=False;startup=None;last_phase='spawn';result={};stdout_eof=False
    received_at=None;last_discovery=0;next_sample=0;snapshot_costs=[]

    def take_snapshot(member,label):
        before=time.perf_counter()
        value=snapshot(member)
        snapshot_costs.append(dict(role=label,started_at=before,seconds=time.perf_counter()-before))
        return value

    def remember(member):
        try:known.setdefault(member.pid,(member,member.create_time()))
        except psutil.NoSuchProcess:pass

    def discover():
        nonlocal last_discovery
        last_discovery=time.perf_counter()
        if child is None:return
        remember(child)
        try:
            for member in child.children(recursive=True):remember(member)
        except psutil.NoSuchProcess:pass

    def stop_tree():
        killed=[];problems=[];waiting=[]
        for pid,(member,created) in reversed(list(known.items())):
            try:
                # A recorded PID is never permission to kill a reused PID.
                if member.is_running() and member.create_time()==created:
                    member.kill();killed.append(pid);waiting.append(member)
            except psutil.NoSuchProcess:pass
            except psutil.Error as error:problems.append(repr(error))
        _,alive=psutil.wait_procs(waiting,timeout=3)
        alive=[p for p in alive if p.is_running() and p.status()!=psutil.STATUS_ZOMBIE]
        if process is not None:
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:problems.append('worker did not exit after tree cleanup')
        return dict(terminated_pids=killed,still_alive_pids=[p.pid for p in alive],errors=problems,
                    observed_processes=[dict(pid=pid,created_at=created) for pid,(_,created) in known.items()])

    with ExitStack() as stack:
        stdout_log=stack.enter_context(paths['stdout'].open('xb',buffering=0))
        stderr_log=stack.enter_context(paths['stderr'].open('xb',buffering=0))
        journal=stack.enter_context(paths['journal'].open('x',encoding='utf-8',buffering=1))

        def note(kind,**value):
            journal.write(json.dumps(dict(kind=kind,at=time.perf_counter(),**value))+'\n')

        def stdout_reader():
            try:
                for raw in process.stdout:
                    observed=time.perf_counter();stdout_log.write(raw)
                    events.put(('line',observed,raw))
            except Exception as error:events.put(('reader_error',time.perf_counter(),repr(error)))
            finally:events.put(('eof',time.perf_counter(),None))

        def stderr_reader():
            try:
                while True:
                    raw=process.stderr.read1(65536)
                    if not raw:break
                    stderr_log.write(raw);stderr_chunks.append(raw)
            except Exception as error:events.put(('reader_error',time.perf_counter(),repr(error)))

        note('spawn',utc=started_utc,wall_budget_seconds=budget)
        try:
            env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
            for key in list(env):
                if key.lower() in ('http_proxy','https_proxy','all_proxy','no_proxy','pythonpath','fastls_library'):env.pop(key)
            env['FASTLS_LIBRARY']=spec['fastls_input']['library']
            process=subprocess.Popen([sys.executable,str(worker_script),str(paths['input'])],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
            threads=[threading.Thread(target=stdout_reader,daemon=True),threading.Thread(target=stderr_reader,daemon=True)]
            for thread in threads:thread.start()
            try:child=psutil.Process(process.pid);discover()
            except psutil.NoSuchProcess:pass
            server=psutil.Process(fixture_process.pid)
            while process.poll() is None or not stdout_eof or not events.empty():
                if time.perf_counter()-started>budget:
                    raise TimeoutError('benchmark worker exceeded wall budget')
                if time.perf_counter()-last_discovery>=.25:discover()
                try:
                    batch=[events.get(timeout=.05)]
                except queue.Empty:batch=[]
                while not events.empty() and len(batch)<1024:batch.append(events.get_nowait())
                latest_progress=None
                for kind,received_at,raw in batch:
                    if kind=='reader_error':raise RuntimeError('worker pipe reader failed: '+raw)
                    if kind=='eof':stdout_eof=True;continue
                    event=json.loads(raw)
                    if not isinstance(event,dict) or not isinstance(event.get('stage'),str):
                        raise ValueError('worker marker must be an object with a stage')
                    handled=time.perf_counter()
                    event=dict(event,controller_received_at=received_at,controller_handled_at=handled,
                               controller_queue_delay_seconds=handled-received_at)
                    markers.append(event);last_phase=event.get('phase',event['stage'])
                    note('marker',event=event)
                    if event['stage']=='initialized':startup=time.perf_counter()-started
                    elif event['stage']=='measure_start':
                        if base is not None:raise ValueError('duplicate measurement start')
                        base=take_snapshot(child,'client_start');serverbase=take_snapshot(server,'server_start');measuring=True
                        discover()
                    elif event['stage']=='measure_stop':
                        if base is None or finish is not None:raise ValueError('invalid measurement stop')
                        finish=take_snapshot(child,'client_stop');serverfinish=take_snapshot(server,'server_stop');measuring=False
                    elif event['stage']=='closed':recovery=take_snapshot(child,'client_closed')
                    elif event['stage']=='progress':latest_progress=event
                    if event.get('ack_required',event['stage'] not in ('progress','phase')):
                        process.stdin.write(b'\n');process.stdin.flush()
                        note('ack',stage=event['stage'],worker_at=event.get('worker_at'),received_at=received_at)
                # Record every progress message, but do not perform a process
                # enumeration and HTTP request for every queued message. Drain
                # lifecycle boundaries before the next periodic sample.
                if latest_progress is not None and measuring:
                    latest_progress['server']=fixture_snapshot(spec,token)
                    note('fixture_progress',event=latest_progress)
                if measuring and time.perf_counter()>=next_sample:
                    observation=dict(client=take_snapshot(child,'client_sample'),server=take_snapshot(server,'server_sample'))
                    samples.append(observation);note('sample',observation=observation)
                    next_sample=time.perf_counter()+.05
            process.wait(timeout=5)
        except Exception as error:
            errors.append(dict(type=type(error).__name__,message=str(error),phase=last_phase,
                               elapsed_seconds=time.perf_counter()-started,traceback=traceback.format_exc()))
            note('failure',error=errors[-1])
        finally:
            try:discover()
            except psutil.Error as error:errors.append(dict(type=type(error).__name__,message=str(error),phase=last_phase))
            cleanup=stop_tree()
            if cleanup['still_alive_pids'] or cleanup['errors']:
                errors.append(dict(type='CleanupError',message='observed process tree did not close cleanly',phase=last_phase))
            elif cleanup['terminated_pids'] and not errors:
                errors.append(dict(type='CleanupError',message='worker left live descendants after exiting',phase=last_phase))
            for thread in threads:thread.join(3)
            if any(thread.is_alive() for thread in threads):
                errors.append(dict(type='CleanupError',message='worker pipes did not reach EOF',phase=last_phase))
            if process is not None:
                for stream in (process.stdin,process.stdout,process.stderr):
                    # Closing a pipe while its reader owns the lock can hang.
                    if not any(t.is_alive() for t in threads):
                        try:stream.close()
                        except OSError as error:errors.append(dict(type=type(error).__name__,message=str(error),phase=last_phase))
            # Collect bytes that arrived while the failing worker was reaped.
            while not events.empty():
                kind,received_at,raw=events.get_nowait()
                if kind=='line':
                    try:
                        event=json.loads(raw)
                        if isinstance(event,dict) and isinstance(event.get('stage'),str):
                            markers.append(dict(event,controller_received_at=received_at,after_failure=True))
                    except (ValueError,UnicodeError):pass
                elif kind=='reader_error':errors.append(dict(type='ReaderError',message=raw,phase=last_phase))
            try:
                result=json.loads(paths['worker'].read_text(encoding='utf-8'))
                if not isinstance(result,dict):raise ValueError('worker result must be an object')
            except Exception as error:
                errors.append(dict(type=type(error).__name__,message=str(error),phase=last_phase));result={}
            if not (base and finish and recovery):
                errors.append(dict(type='IncompleteObservation',message='measurement or closed boundary missing',phase=last_phase))
            note('cleanup',cleanup=cleanup,errors=errors)

    resources=dict(baseline=base,finished=finish,after_close=recovery,samples=samples,
                   server_baseline=serverbase,server_finished=serverfinish,measurement_complete=bool(base and finish))
    if base and finish:
        allsamples=[base,finish]+[s['client'] for s in samples];seconds=finish['at']-base['at']
        resources.update(cpu_seconds=max(0,finish['cpu_seconds']-base['cpu_seconds']),
            cpu_cores=max(0,finish['cpu_seconds']-base['cpu_seconds'])/max(seconds,1e-9),requested_sample_interval_seconds=.05,
            peak_rss_bytes=max(s['rss_bytes'] for s in allsamples),peak_threads=max(s['threads'] for s in allsamples),
            peak_descriptors=max(s['descriptors'] for s in allsamples))
    logs=b''.join(stderr_chunks).decode('utf-8',errors='replace')
    valid=not errors and process is not None and process.returncode==0 and bool(result.get('success')) and not result.get('failed')
    row=dict(case=spec['case'],client=spec['client'],round=spec['round'],valid=bool(valid),
        exit_code=process.returncode if process else None,metrics=result,resources=resources,
        startup_including_probe_seconds=startup,markers=markers,
        error='; '.join(e['type']+': '+e['message'] for e in errors) or (logs[-5000:] if not valid else None),
        diagnostics=dict(started_utc=started_utc,started_at=started,ended_at=time.perf_counter(),
            wall_budget_seconds=budget,last_phase=last_phase,controller_errors=errors,cleanup=cleanup,
            last_received_phase=(markers[-1].get('phase',markers[-1]['stage']) if markers else 'spawn'),
            snapshot_costs=snapshot_costs,
            marker_queue_delay_max_seconds=max((m.get('controller_queue_delay_seconds',0) for m in markers),default=0),
            evidence_files={k:p.name for k,p in paths.items()}))
    temporary=paths['result'].with_suffix('.tmp')
    temporary.write_text(json.dumps(row,indent=2)+'\n',encoding='utf-8');temporary.replace(paths['result'])
    return row
