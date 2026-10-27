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

if __name__=='__main__':unittest.main(verbosity=2)
