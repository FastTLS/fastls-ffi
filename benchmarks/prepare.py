"""Build the real library and fixture using the native CI compiler selection."""
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
env=dict(os.environ,CGO_ENABLED="1",PYTHONUTF8="1",PYTHONIOENCODING="utf-8")
os.chdir(ROOT)
runpy.run_path(str(ROOT/"scripts/ci.py"))["preflight"](env)
subprocess.run([sys.executable,"scripts/build.py","--local"],env=env,check=True)
subprocess.run(["go","build","-o","build/ffi-fixture.exe" if os.name=="nt" else "build/ffi-fixture","./cmd/ffi-fixture"],env=env,check=True)
subprocess.run(["go","test","./cmd/ffi-fixture"],env=env,check=True)
subprocess.run([sys.executable,"scripts/build_benchmark_fixture.py"],env=env,check=True)
subprocess.run([sys.executable,"tests/probe_h2_settings_ack.py","--fixture",str(ROOT/"build"/("ffi-fixture-benchmark.exe" if os.name=="nt" else "ffi-fixture-benchmark")),"--output",str(ROOT/"build/fixture-settings-ack.json")],env=env,check=True)
subprocess.run([sys.executable,"tests/test_benchmarks.py"],env=env,check=True)
