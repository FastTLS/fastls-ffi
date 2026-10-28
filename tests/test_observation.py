"""Real subprocess faults must remain invalid, durable, and fully reaped."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import psutil

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmarks'))
import observation
from run import snapshot

STUB=r'''
from pathlib import Path
import faulthandler,json,os,subprocess,sys,time
s=json.loads(Path(sys.argv[1]).read_text());mode=s['fault']
def event(stage,**kw):
    ack=kw.pop('ack_required',True)
    print(json.dumps(dict(stage=stage,ack_required=ack,worker_at=time.perf_counter(),**kw)),flush=True)
    if ack and not sys.stdin.readline():raise RuntimeError('controller disconnected')
event('phase',ack_required=False,phase='probe')
sys.stderr.buffer.write(b'partial diagnostic without newline');sys.stderr.buffer.flush()
if mode=='bad_marker':
    sys.stdout.buffer.write(b'not JSON\n');sys.stdout.buffer.flush();time.sleep(30)
event('initialized')
event('measure_start')
if mode=='progress_flood':
    for i in range(100):event('progress',ack_required=False,consumed_bytes=i,elapsed=i/100)
if mode=='timeout':
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    event('phase',ack_required=False,phase='measurement_stall',descendant_pid=child.pid)
    faulthandler.dump_traceback_later(.15)
    time.sleep(30)
time.sleep(.08)
event('measure_stop')
event('phase',ack_required=False,phase='native_recovery')
event('closed')
p=Path(s['result_file'])
if mode=='bad_result':p.write_text('{broken')
elif mode=='list_result':p.write_text('[]')
elif mode!='missing_result':p.write_text(json.dumps(dict(success=1,failed=0)))
if mode=='nonzero':raise SystemExit(7)
'''


class DurableObservation(unittest.TestCase):
    def setUp(self):
        destination=os.environ.get('FASTLS_OBSERVATION_TEST_OUTPUT')
        self.temp=None if destination else tempfile.TemporaryDirectory()
        self.root=Path(destination)/self._testMethodName if destination else Path(self.temp.name)
        if destination:self.root.mkdir(parents=True,exist_ok=False)
        self.worker=self.root/'worker.py';self.worker.write_text(STUB,encoding='utf-8')
        self.fixture=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.buffer.read()'],stdin=subprocess.PIPE)

    def tearDown(self):
        # Closing stdin also releases the actual interpreter behind a Windows
        # venv launcher; killing only the launcher would leave an idle child.
        self.fixture.stdin.close();self.fixture.wait(timeout=5)
        if self.temp:self.temp.cleanup()

    def execute(self,mode):
        spec=dict(run_id=mode,case=dict(name='fault-injection',size=1),client='fastls',round=1,
            fastls_input=dict(library='not-loaded-by-stub'),warmup_seconds=.05,seconds=1,fault=mode)
        with patch.object(observation,'wall_budget',return_value=2 if mode=='timeout' else 8),patch.object(observation,'fixture_snapshot',return_value={'active':0}):
            row=observation.execute(spec,self.fixture,self.root,worker_script=self.worker,snapshot=snapshot)
        saved=json.loads((self.root/(mode+'-run.json')).read_text())
        self.assertEqual(row,saved)
        journal=[json.loads(line) for line in (self.root/(mode+'-observation.jsonl')).read_text().splitlines()]
        self.assertEqual(journal[0]['kind'],'spawn');self.assertEqual(journal[-1]['kind'],'cleanup')
        self.assertIn(b'partial diagnostic without newline',(self.root/(mode+'-stderr.log')).read_bytes())
        self.assertEqual(row['diagnostics']['cleanup']['still_alive_pids'],[])
        return row,journal

    def test_success_still_records_cpu_boundaries_and_ack_free_phases(self):
        row,_=self.execute('success')
        self.assertTrue(row['valid'],json.dumps(row['diagnostics']));self.assertEqual(row['exit_code'],0)
        self.assertTrue(row['resources']['measurement_complete'])
        self.assertIsNotNone(row['resources']['after_close'])
        self.assertEqual(row['diagnostics']['last_phase'],'closed')
        self.assertEqual(row['diagnostics']['cleanup']['terminated_pids'],[])

    def test_timeout_preserves_partial_samples_stack_and_kills_descendants(self):
        row,journal=self.execute('timeout')
        self.assertFalse(row['valid']);self.assertIn('TimeoutError',row['error'])
        self.assertIn(row['diagnostics']['last_phase'],('measure_start','measurement_stall'))
        self.assertEqual(row['diagnostics']['last_received_phase'],'measurement_stall')
        self.assertTrue(row['resources']['samples']);self.assertFalse(row['resources']['measurement_complete'])
        self.assertNotIn('cpu_seconds',row['resources'])
        self.assertTrue(any(x['kind']=='sample' for x in journal))
        self.assertIn(b'Timeout',(self.root/'timeout-stderr.log').read_bytes())
        descendant=next(m['descendant_pid'] for m in row['markers'] if 'descendant_pid' in m)
        self.assertIn(descendant,row['diagnostics']['cleanup']['terminated_pids'])
        if psutil.pid_exists(descendant):self.assertEqual(psutil.Process(descendant).status(),psutil.STATUS_ZOMBIE)

    def test_progress_flood_does_not_starve_lifecycle_acknowledgements(self):
        row,journal=self.execute('progress_flood')
        self.assertTrue(row['valid'],row['error'])
        self.assertEqual(sum(m['stage']=='progress' for m in row['markers']),100)
        self.assertEqual(sum(n['kind']=='marker' and n['event']['stage']=='progress' for n in journal),100)
        self.assertLess(row['diagnostics']['ended_at']-row['diagnostics']['started_at'],8)
        self.assertLess(len(row['resources']['samples']),20)

    def test_nonzero_exit_keeps_valid_partial_worker_payload_and_exit_status(self):
        row,_=self.execute('nonzero')
        self.assertFalse(row['valid']);self.assertEqual(row['exit_code'],7)
        self.assertEqual(row['metrics']['success'],1)
        self.assertTrue(row['resources']['measurement_complete'])

    def test_bad_stdout_keeps_original_bytes_and_stops_worker(self):
        row,_=self.execute('bad_marker')
        self.assertFalse(row['valid']);self.assertIn('JSONDecodeError',row['error'])
        self.assertIn(b'not JSON',(self.root/'bad_marker-stdout.log').read_bytes())

    def test_missing_malformed_or_nonobject_result_never_passes(self):
        for mode in ('missing_result','bad_result','list_result'):
            with self.subTest(mode=mode):
                row,_=self.execute(mode)
                self.assertFalse(row['valid']);self.assertEqual(row['metrics'],{})
                self.assertTrue(row['diagnostics']['controller_errors'])

    def test_spawn_failure_still_has_a_complete_failure_record(self):
        spec=dict(run_id='spawn',case=dict(name='spawn',size=1),client='fastls',round=1,
                  fastls_input=dict(library='unused'),warmup_seconds=0,seconds=1)
        with patch.object(observation.subprocess,'Popen',side_effect=OSError('injected spawn failure')):
            row=observation.execute(spec,self.fixture,self.root,worker_script=self.worker,snapshot=snapshot)
        self.assertFalse(row['valid']);self.assertIn('injected spawn failure',row['error'])
        self.assertEqual(json.loads((self.root/'spawn-run.json').read_text()),row)

    def test_reused_run_id_cannot_overwrite_any_evidence(self):
        self.execute('success')
        path=self.root/'success-input.json';before=path.read_bytes()
        with self.assertRaises(FileExistsError):self.execute('success')
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main(verbosity=2)
