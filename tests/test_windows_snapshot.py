"""Whole-tree counters, Windows enumeration failures and real launcher ownership."""
from contextlib import nullcontext
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmarks'))
import windows_snapshot as ws


class FakeProcess:
    def __init__(self,pid,born=10,alive=True):self.pid=pid;self.born=born;self.alive=alive
    def create_time(self):return self.born
    def is_running(self):return self.alive
    def oneshot(self):return nullcontext()
    def cpu_times(self):return SimpleNamespace(user=self.pid*.25,system=self.pid*.5)
    def memory_info(self):return SimpleNamespace(rss=self.pid*4096)
    def num_handles(self):return self.pid*2


class TreeContract(unittest.TestCase):
    def test_recurses_all_descendants_ignoring_recycled_parent_and_cycles(self):
        entries={1:dict(parent=3,threads=2),2:dict(parent=1,threads=3),
                 3:dict(parent=2,threads=4),4:dict(parent=1,threads=8),
                 5:dict(parent=4,threads=9),6:dict(parent=2,threads=1)}
        processes={n:FakeProcess(n,born=5 if n==4 else 10+n,alive=n!=6) for n in entries}
        found=ws.process_tree(processes[1],entries,processes.__getitem__)
        self.assertEqual([(p.pid,t) for p,t in found],[(1,2),(2,3),(3,4)])

    def test_missing_dead_root_never_becomes_an_empty_zero_sample(self):
        for root,entries in [(FakeProcess(1),{}),(FakeProcess(1,alive=False),{1:dict(parent=0,threads=1)})]:
            with self.assertRaises(psutil.NoSuchProcess):ws.process_tree(root,entries)

    def test_disappearing_child_is_skipped_but_access_denied_is_reported(self):
        entries={1:dict(parent=0,threads=1),2:dict(parent=1,threads=2)}
        def missing(pid):raise psutil.NoSuchProcess(pid)
        self.assertEqual(len(ws.process_tree(FakeProcess(1),entries,missing)),1)
        def denied(pid):raise psutil.AccessDenied(pid)
        with self.assertRaises(psutil.AccessDenied):ws.process_tree(FakeProcess(1),entries,denied)

    def test_all_counters_sum_every_member_with_original_units(self):
        root=FakeProcess(1);child=FakeProcess(2)
        with patch.object(ws,'process_tree',return_value=[(root,3),(child,7)]):row=ws.snapshot(root)
        self.assertEqual([p['pid'] for p in row['processes']],[1,2])
        self.assertEqual(row['cpu_seconds'],2.25);self.assertEqual(row['rss_bytes'],12288)
        self.assertEqual(row['threads'],10);self.assertEqual(row['descriptors'],6)
        with patch.object(ws,'process_tree',return_value=[(root,3)]),patch.object(root,'num_handles',side_effect=psutil.AccessDenied(1)):
            with self.assertRaises(psutil.AccessDenied):ws.snapshot(root)

    def test_deceased_descendant_does_not_mask_live_root(self):
        root=FakeProcess(1);child=FakeProcess(2)
        with patch.object(ws,'process_tree',return_value=[(root,3),(child,7)]),patch.object(child,'cpu_times',side_effect=psutil.NoSuchProcess(2)):
            self.assertEqual([p['pid'] for p in ws.snapshot(root)['processes']],[1])


@unittest.skipUnless(os.name=='nt','Windows Tool Help live API')
class WindowsAPI(unittest.TestCase):
    def test_layout_thread_counts_and_handles_are_stable(self):
        self.assertEqual(ctypes.sizeof(ws.ProcessEntry),568 if ctypes.sizeof(ctypes.c_void_p)==8 else 556)
        process=psutil.Process();before=process.num_handles()
        # No helper threads are created in this process during the comparison.
        for _ in range(100):
            entries=ws.process_entries()
            self.assertEqual(entries[process.pid]['threads'],process.num_threads())
        self.assertEqual(process.num_handles(),before)

    def test_enumeration_failure_closes_snapshot_and_fails_closed(self):
        class API:
            def __init__(self):self.closed=[]
            def CreateToolhelp32Snapshot(self,*a):return 123
            def Process32FirstW(self,*a):ctypes.set_last_error(5);return 0
            def Process32NextW(self,*a):raise AssertionError('not reached')
            def CloseHandle(self,handle):self.closed.append(handle);return 1
        api=API()
        with patch.object(ws,'_api',return_value=api),self.assertRaises(OSError):ws.process_entries()
        self.assertEqual(api.closed,[123])

    def test_actual_venv_launcher_and_nested_child_are_counted(self):
        code="""import subprocess,sys,json
p=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE)
print(json.dumps(dict(parent=__import__('os').getpid(),child=p.pid)),flush=True)
sys.stdin.read();p.stdin.close();p.wait(timeout=5)
"""
        launcher=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            ready=json.loads(launcher.stdout.readline());root=psutil.Process(launcher.pid)
            expected={root.pid,*[p.pid for p in root.children(recursive=True)]}
            actual={p.pid for p,_ in ws.process_tree(root)}
            self.assertEqual(actual,expected)
            self.assertIn(ready['parent'],actual);self.assertIn(ready['child'],actual)
            row=ws.snapshot(root)
            self.assertEqual({p['pid'] for p in row['processes']},expected)
            for p in row['processes']:
                member=psutil.Process(p['pid'])
                self.assertEqual(p['threads'],member.num_threads())
                self.assertEqual(p['descriptors'],member.num_handles())
                self.assertLessEqual(p['cpu_seconds'],sum(member.cpu_times()[:2]))
        finally:
            launcher.stdin.close();launcher.wait(timeout=10);launcher.stdout.close()


if __name__=='__main__':unittest.main(verbosity=2)
