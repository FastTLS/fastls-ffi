"""Check the live Windows cycle API against the actual venv child interpreter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import psutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"benchmarks"))
from windows_cpu_probe import snapshot_with_cycles


@unittest.skipUnless(os.name=="nt","Windows API")
class WindowsCycleProbe(unittest.TestCase):
    def test_actual_interpreter_cycles_increase_after_hash_work(self):
        code="import hashlib,json,os,sys;print(os.getpid(),flush=True);sys.stdin.readline();data=b'x'*(1024*1024);[hashlib.sha256(data).digest() for _ in range(128)];print('done',flush=True);sys.stdin.readline()"
        child=subprocess.Popen([sys.executable,"-c",code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            actual=int(child.stdout.readline())
            before=snapshot_with_cycles(psutil.Process(child.pid))
            child.stdin.write("\n");child.stdin.flush()
            self.assertEqual(child.stdout.readline().strip(),"done")
            after=snapshot_with_cycles(psutil.Process(child.pid))
            self.assertIn(actual,{p["pid"] for p in before["processes"]})
            self.assertGreater(after["cpu_cycles"],before["cpu_cycles"])
            self.assertEqual(after["cpu_cycles"],sum(p["cpu_cycles"] for p in after["processes"]))
        finally:
            child.stdin.close();child.wait(timeout=5);child.stdout.close()


if __name__=="__main__":unittest.main(verbosity=2)
