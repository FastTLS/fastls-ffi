"""Keep v2 windows and eligibility independent of the retained v1 results."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
import sys,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmarks'))
from test_regression import CASE,POLICY,row
from continuous_regression import ARMS,evaluate,window
import worker
from timed_regression import evaluate as timed_evaluate, timing_check


def observed(r):
    r=deepcopy(r)
    r['run_id']=r['variant']+'-'+str(r['round'])
    r['resources'].update(baseline={'at':100.5},finished={'at':131.0},
        samples=[{'client':{'at':100.5+n}} for n in range(1,31)])
    r['markers']=[dict(stage=stage,worker_at=at,controller_received_at=at+.1,
        controller_handled_at=at+.2,controller_queue_delay_seconds=.1)
        for stage,at in [('measure_start',100),('measure_stop',130.5)]]
    r['diagnostics']=dict(controller_errors=[],cleanup=dict(terminated_pids=[],still_alive_pids=[],errors=[]),
        snapshot_costs=[dict(role=role,seconds=.1) for role in
            ('client_start','server_start','client_stop','server_stop','client_closed')])
    return r


class TimedGate(unittest.TestCase):
    def setUp(self):
        self.rows=[observed(dict(row(a,n),continuous_transfers=True,requested_seconds=30))
            for n in range(1,6) for a in ARMS]
        for r in self.rows:r['metrics']['elapsed_seconds']=30
        self.plan=[{k:deepcopy(r[k]) for k in ('case','variant','round','run_id')} for r in self.rows]
    def verdict(self):return timed_evaluate(self.rows,[CASE],POLICY,True,self.plan)
    def test_complete_timed_plan_passes(self):
        self.assertTrue(self.verdict()['success'])
    def test_boundary_delay_is_ineligible_without_erasing_metric_verdict(self):
        self.rows[0]['resources']['finished']['at']=135
        result=self.verdict()
        self.assertEqual(result['status'],'ineligible')
        self.assertEqual(result['cases'][0]['candidate']['diagnostic_status'],'passed')
    def test_missing_nan_duplicate_and_reversed_boundary_fail_closed(self):
        for mutation in ('missing','nan','duplicate','reversed'):
            self.setUp();r=self.rows[0]
            if mutation=='missing':r.pop('diagnostics')
            elif mutation=='nan':r['resources']['baseline']['at']=float('nan')
            elif mutation=='duplicate':r['markers'].append(deepcopy(r['markers'][0]))
            else:r['markers'][0]['controller_received_at']=99
            self.assertFalse(timing_check(r)['eligible'],mutation)
    def test_sample_stall_and_marker_backlog_fail(self):
        self.rows[0]['resources']['samples']=self.rows[0]['resources']['samples'][5:]
        self.assertFalse(timing_check(self.rows[0])['eligible'])
        self.setUp();m=self.rows[0]['markers'][0]
        m['controller_handled_at']=m['controller_received_at']+2.1;m['controller_queue_delay_seconds']=2.1
        self.assertFalse(timing_check(self.rows[0])['eligible'])
    def test_frozen_order_and_identity_cannot_be_replaced(self):
        self.rows.reverse();self.assertEqual(self.verdict()['status'],'ineligible')
        self.setUp();self.rows[0]['run_id']='replacement'
        self.assertEqual(self.verdict()['status'],'ineligible')
    def test_timing_does_not_relax_performance_or_aa_rules(self):
        for r in self.rows:
            if r['variant']=='candidate':r['resources']['cpu_seconds']*=1.26
        self.assertEqual(self.verdict()['status'],'failed')
        self.setUp()
        for r in self.rows:
            if r['variant']=='control-b':r['resources']['cpu_seconds']*=1.26
        self.assertEqual(self.verdict()['status'],'ineligible')

class ContinuousGate(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(row(a,n),continuous_transfers=True,requested_seconds=30) for n in range(1,6) for a in ARMS]
        for r in self.rows:r['metrics']['elapsed_seconds']=30
    def verdict(self):return evaluate(self.rows,[CASE],POLICY,True)
    def test_identical_controls_and_complete_windows_pass(self):
        self.assertTrue(self.verdict()['success'])
        self.assertEqual([window(dict(CASE,workload=x)) for x in ('small','download','slow')],[30,16,64])
    def test_short_window_missing_round_and_legacy_single_transfer_are_ineligible(self):
        for field,value in [('continuous_transfers',False),('requested_seconds',1)]:
            self.setUp();self.rows[0][field]=value
            self.assertEqual(self.verdict()['status'],'ineligible')
        self.setUp();self.rows[0]['metrics']['elapsed_seconds']=29.9
        self.assertEqual(self.verdict()['status'],'ineligible')
        self.setUp();self.rows.pop();self.assertEqual(self.verdict()['status'],'ineligible')
    def test_tiny_cpu_counts_cannot_pass_even_with_matching_ratios(self):
        for r in self.rows:r['resources']['cpu_seconds']=.01
        self.assertEqual(self.verdict()['status'],'ineligible')
    def test_aa_instability_and_real_cpu_regression_remain_distinct(self):
        for r in self.rows:
            if r['variant']=='control-b':r['resources']['cpu_seconds']*=1.26
        self.assertEqual(self.verdict()['status'],'ineligible')
        self.setUp()
        for r in self.rows:
            if r['variant']=='candidate':r['resources']['cpu_seconds']*=1.26
        self.assertEqual(self.verdict()['status'],'failed')
    def test_integrity_and_protocol_failure_never_pass(self):
        self.assertEqual(evaluate(self.rows,[CASE],POLICY,False)['status'],'ineligible')
        self.rows[0]['metrics']['server']['connections']['1']['protocol']='HTTP/2.0'
        self.assertEqual(self.verdict()['status'],'ineligible')

class FakeClient:
    case={'cold':False,'concurrency':1,'workload':'download'}
    session_setup_seconds=[]
    @asynccontextmanager
    async def session(self,async_=False):yield self
    async def aprime(self,*args):pass
    async def aperform(self,session,run,target):
        await asyncio.sleep(.001);target.success+=1;target.bytes+=1
    def stats(self):return {}

class ContinuousWorker(unittest.TestCase):
    def test_async_transfer_repeats_only_when_explicitly_selected(self):
        counts=[]
        for continuous in (False,True):
            spec=dict(warmup_seconds=0,seconds=.025,run_id='unit',continuous_transfers=continuous)
            with patch.object(worker,'emit'):
                result,_=asyncio.run(worker.run_async(FakeClient(),spec))
            counts.append(result['success'])
        self.assertEqual(counts[0],1);self.assertGreater(counts[1],1)

    def test_sync_transfer_repeats_only_when_explicitly_selected(self):
        class SyncClient(FakeClient):
            def session(self):return self
            def close(self):pass
            def prime(self,*args):pass
            def perform(self,session,run,target):time.sleep(.001);target.success+=1;target.bytes+=1
        counts=[]
        for continuous in (False,True):
            with patch.object(worker,'emit'):
                result,_=worker.run_sync(SyncClient(),dict(warmup_seconds=0,seconds=.025,run_id='unit',continuous_transfers=continuous))
            counts.append(result['success'])
        self.assertEqual(counts[0],1);self.assertGreater(counts[1],1)

class ExtendedTimedGate(unittest.TestCase):
    """Longer observation keeps the original metrics and strict qualification."""
    def setup_rows(self,seconds):
        import timed_regression_v4 as v4
        self.case=dict(CASE,name='slow-h1',workload='slow')
        self.rows=[]
        for n in range(1,6):
            for arm in ARMS:
                r=observed(dict(row(arm,n),case=self.case,continuous_transfers=True,requested_seconds=seconds))
                r['metrics']['elapsed_seconds']=seconds
                r['resources']['finished']['at']=101+seconds
                r['resources']['samples']=[{'client':{'at':100.5+i}} for i in range(1,seconds+1)]
                stop=r['markers'][1]
                stop.update(worker_at=100.5+seconds,controller_received_at=100.6+seconds,
                    controller_handled_at=100.7+seconds)
                self.rows.append(r)
        self.plan=[{k:deepcopy(r[k]) for k in ('case','variant','round','run_id')} for r in self.rows]
        self.v4=v4

    def verdict(self):return self.v4.evaluate(self.rows,[self.case],POLICY,True,self.plan)

    def test_complete_extended_plan_passes(self):
        import timed_regression_v4 as v4
        self.setup_rows(v4.SLOW_SECONDS)
        self.assertTrue(self.verdict()['success'])
        self.assertEqual(self.verdict()['protocol'],v4.PROTOCOL)

    def test_original64_is_still_valid_v3_but_rejected_by_v4(self):
        self.setup_rows(64)
        self.assertTrue(timed_evaluate(self.rows,[self.case],POLICY,True,self.plan)['success'])
        self.assertFalse(self.verdict()['success'])
        self.assertTrue(any('shortened' in s for s in self.verdict()['cases'][0]['eligibility_failures']))

    def test_original_cpu_aa_and_candidate_limits_still_apply(self):
        import timed_regression_v4 as v4
        for arm,status in [('candidate','failed'),('control-b','ineligible')]:
            self.setup_rows(v4.SLOW_SECONDS)
            for r in self.rows:
                if r['variant']==arm:r['resources']['cpu_seconds']*=1.3
            self.assertEqual(self.verdict()['status'],status)

    def test_original_two_second_timing_ceiling_is_not_scaled_up(self):
        import timed_regression_v4 as v4
        self.setup_rows(v4.SLOW_SECONDS)
        self.rows[0]['resources']['finished']['at']+=3
        self.assertEqual(self.verdict()['status'],'ineligible')
        self.assertEqual(self.verdict()['timing_policy']['maximum_window_excess_seconds'],2)

    def test_other_windows_and_missing_round_guards_are_unchanged(self):
        import timed_regression_v4 as v4
        self.assertEqual(v4.window(CASE),30)
        self.assertEqual(v4.window(dict(CASE,workload='upload')),16)
        self.setup_rows(v4.SLOW_SECONDS);self.rows.pop()
        self.assertFalse(self.verdict()['success'])


class SyncPhaseBudget(unittest.TestCase):
    def test_requested_window_beyond120_finishes_before_outer_guard(self):
        # Exercise the actual two-thread barrier path at100x clock speed.
        # The old120-second barrier then expires at1.20 real seconds, before
        # a128-second logical measurement; a deadline-aware barrier completes.
        class SyncClient(FakeClient):
            def session(self):return self
            def close(self):pass
            def prime(self,*args):pass
            def perform(self,session,run,target):time.sleep(.001);target.success+=1;target.bytes+=1
        import threading
        real_barrier=threading.Barrier
        def scaled_barrier(parties,timeout):
            return real_barrier(parties,timeout=timeout/100 if timeout>=120 else timeout)
        origin=time.perf_counter()
        def accelerated_clock():return (time.perf_counter()-origin)*100
        with patch.object(worker,'CLOCK',side_effect=accelerated_clock),patch.object(worker.threading,'Barrier',side_effect=scaled_barrier),patch.object(worker,'emit') as emit:
            result,_=worker.run_sync(SyncClient(),dict(warmup_seconds=0,seconds=128,run_id='extended-sync',continuous_transfers=True))
        self.assertGreaterEqual(result['elapsed_seconds'],128)
        self.assertGreater(result['success'],0)
        self.assertTrue(any(c.args[0]=='measure_stop' for c in emit.call_args_list))


if __name__=='__main__':unittest.main(verbosity=2)
