"""Run the integration suite with the packaged SDK, never the source package."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
stability = "--stability" in sys.argv[1:]
network = "--network" in sys.argv[1:]
pooling = "--pooling" in sys.argv[1:]
cookies = "--cookies" in sys.argv[1:]
decode_timeouts = "--decode-timeouts" in sys.argv[1:]
upload = "--upload" in sys.argv[1:]
duplex = "--duplex" in sys.argv[1:]
sse = "--sse" in sys.argv[1:]
notifications = "--notifications" in sys.argv[1:]
wheels = list((ROOT / "dist").glob("*.whl"))
if len(wheels) != 1:
    raise SystemExit("expected exactly one SDK wheel in dist/")
library = "fastls.dll" if sys.platform == "win32" else "libfastls.dylib" if sys.platform == "darwin" else "libfastls.so"
os.environ["FASTLS_LIBRARY"] = str(ROOT / "dist" / library)
sys.path.insert(0, str(wheels[0]))
import fastls
if str(wheels[0]) not in fastls.__file__:
    raise SystemExit("SDK was not imported from the wheel")
print("Packaged SDK:", fastls.__file__, flush=True)
os.environ["FASTLS_TEST_LABEL"] = "wheel"
sys.path.insert(0, str(ROOT / "tests"))
sys.argv = [str(ROOT / ("tests/test_notifications.py" if notifications else "tests/test_sse.py" if sse else "tests/test_duplex.py" if duplex else "tests/test_upload.py" if upload else "tests/test_decode_timeouts.py" if decode_timeouts else "tests/test_cookies.py" if cookies else "tests/test_pooling.py" if pooling else "tests/test_network.py" if network else "tests/test_stability.py" if stability else "tests/test_python.py"))]
runpy.run_path(sys.argv[0], run_name="__main__")
