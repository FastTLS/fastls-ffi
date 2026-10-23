"""Phase-two tests: real library, independently generated CA and loopback H1/H2.

Memory limits are regression guards after warmup, not proof of absence of leaks.
No system roots, global Go flags or network outside loopback are modified.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
import gc
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tempfile
import time
import unittest
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from fastls import Session, AsyncSession, FastlsError

SLAB = bytes([0, 1, 128, 255]) * 16384
MIB = 1024 * 1024


def resources():
    rss, descriptors = None, None
    if sys.platform == "win32":
        class Memory(C.Structure):
            _fields_ = [("cb", C.c_ulong), ("faults", C.c_ulong)] + [(name, C.c_size_t) for name in (
                "peak", "working", "paged_peak", "paged", "nonpaged_peak", "nonpaged", "pagefile", "pagefile_peak", "private")]
        memory = Memory(); memory.cb = C.sizeof(memory)
        if C.windll.psapi.GetProcessMemoryInfo(C.c_void_p(-1), C.byref(memory), memory.cb):
            rss = memory.working
        count = C.c_ulong()
        if C.windll.kernel32.GetProcessHandleCount(C.c_void_p(-1), C.byref(count)):
            descriptors = count.value
    elif sys.platform.startswith("linux"):
        rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        descriptors = len(os.listdir("/proc/self/fd"))
    elif sys.platform == "darwin":
        rss = int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())], text=True)) * 1024
        descriptors = len(os.listdir("/dev/fd"))
    return {"rss_bytes": rss, "descriptors_or_handles": descriptors}


class FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = ROOT / "build" / ("ffi-fixture.exe" if sys.platform == "win32" else "ffi-fixture")
        cls.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(cls.process.stdout.readline()), daemon=True)
        reader.start()
        try:
            cls.fixture = json.loads(lines.get(timeout=15))
        except BaseException:
            cls.process.kill(); cls.process.communicate(timeout=5)
            raise
        reader.join(2)
        cls.metrics = {"platform": sys.platform, "python": sys.version, "label": os.environ.get("FASTLS_TEST_LABEL", "source"), "cases": {}}
        # This object does not perform requests. The statistics are runtime-wide.
        cls.monitor = Session()

    @classmethod
    def tearDownClass(cls):
        cls.monitor.close()
        cls.process.stdin.close()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill(); cls.process.wait(timeout=5)
            raise AssertionError("fixture retained active connections at shutdown")
        finally:
            cls.process.stdout.close(); cls.process.stderr.close()
            output = ROOT / "build" / ("stability-" + cls.metrics["label"] + "-" + str(time.time_ns()) + ".json")
            output.write_text(json.dumps(cls.metrics, indent=2) + "\n", encoding="utf-8")
            print("Stability evidence:", output, flush=True)

    def session(self, async_=False, **kwargs):
        kwargs.setdefault("timeout", 15)
        return (AsyncSession if async_ else Session)(ca_file=self.fixture["ca_file"], **kwargs)

    def control(self, path, id):
        with urllib.request.urlopen(self.fixture["http"] + path + "?id=" + id, timeout=3) as response:
            return response.read()

    def state(self, id):
        return json.loads(self.control("/state", id))

    async def wait_state(self, id, key):
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            if self.state(id).get(key):
                return
            await asyncio.sleep(0.02)
        self.fail(f"fixture did not report {key} for {id}")

    def idle(self, timeout=10):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            stats = self.monitor.stats()
            if stats["open_requests"] == 0 and stats["workers"] == 0:
                return stats
            time.sleep(0.02)
        self.fail(f"native requests/workers leaked: {stats}")

    def tearDown(self):
        self.idle()


class Stability(FixtureCase):
    def test_verified_tls_h1_h2_and_ca_isolation(self):
        observed = []
        for name, expected in (("https1", "HTTP/1.1"), ("https2", "HTTP/2.0")):
            with self.session() as s:
                r = s.get(self.fixture[name] + "/redirect", headers={"User-Agent": "phase-two"})
                data = r.json()
                self.assertEqual(data["protocol"], expected)
                self.assertEqual(data["headers"]["User-Agent"], ["phase-two"])
                observed.append(data["protocol"])
            # Supplying a private CA must never modify the process/system roots.
            for ca in (None, self.fixture["wrong_ca_file"]):
                with Session(ca_file=ca, timeout=5) as s:
                    with self.assertRaises(FastlsError):
                        s.get(self.fixture[name])
        self.metrics["cases"]["verified_tls"] = observed

    def test_invalid_ca_fails_before_registering_request(self):
        with tempfile.TemporaryDirectory() as directory:
            ca = Path(directory) / "invalid.pem"
            ca.write_text("invalid certificate data", encoding="utf-8")
            with Session(ca_file=ca) as s:
                with self.assertRaisesRegex(FastlsError, "ca_pem"):
                    s.get(self.fixture["https2"])
                self.assertEqual(s.stats()["open_requests"], 0)
                self.assertEqual(s.stats()["workers"], 0)

    def test_cancel_discards_unread_final_bytes(self):
        with self.session() as s:
            with s.stream("GET", self.fixture["https2"] + "/bytes?n=128") as r:
                iterator = r.iter_bytes(1)
                self.assertEqual(next(iterator), SLAB[:1])
                r.cancel()
                with self.assertRaises(FastlsError):
                    next(iterator)

    def test_tls_streaming_close_isolation_and_cancellation(self):
        async def scenario():
            for name, expected in (("https1", "HTTP/1.1"), ("https2", "HTTP/2.0")):
                a, b, c = (uuid.uuid4().hex for _ in range(3))
                async with self.session(async_=True) as s:
                    async with s.stream("GET", self.fixture[name] + "/hold?id=" + a) as first:
                        self.assertEqual(first.headers["X-Test-Protocol"], expected)
                        iterator = first.aiter_bytes(5)
                        self.assertEqual(await anext(iterator), b"first")
                        async with s.stream("GET", self.fixture[name] + "/hold?id=" + b) as other:
                            second = other.aiter_bytes(5)
                            self.assertEqual(await anext(second), b"first")
                            await first.aclose()
                            self.control("/release", b)
                            self.assertEqual(await anext(second), b"last!")
                            with self.assertRaises(StopAsyncIteration):
                                await anext(second)
                        await self.wait_state(a, "cancelled")
                    task = asyncio.create_task(s.get(self.fixture[name] + "/wait?id=" + c))
                    await self.wait_state(c, "started")
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    await self.wait_state(c, "cancelled")
        asyncio.run(scenario())

    def test_truncated_tls_bodies_are_errors(self):
        for name in ("https1", "https2"):
            with self.session() as s:
                with self.assertRaises(FastlsError):
                    s.get(self.fixture[name] + "/truncated")

    def test_large_stream_backpressure_and_buffered_limit(self):
        id = uuid.uuid4().hex
        with self.session(timeout=90) as s:
            with s.stream("GET", self.fixture["https2"] + "/bytes?n=67108864&id=" + id) as r:
                time.sleep(0.15)
                stats = s.stats()
                self.assertLessEqual(stats["buffered_body_bytes"], 3 * 65536)
                observation = self.state(id)
                self.assertFalse(observation["done"], f"slow consumer did not throttle producer: {observation}; native={s._native.call('fastls_info', r._handle)}")
                digest, length = hashlib.sha256(), 0
                for chunk in r.iter_bytes():
                    digest.update(chunk); length += len(chunk)
                self.assertEqual(length, 64 * MIB)
                expected = hashlib.sha256()
                for _ in range(1024):
                    expected.update(SLAB)
                self.assertEqual(digest.digest(), expected.digest())
            with self.assertRaisesRegex(FastlsError, "32MiB"):
                s.get(self.fixture["http"] + "/bytes?n=34603008")
            self.assertEqual(s.stats()["open_requests"], 0)
        self.metrics["cases"]["large_stream"] = {"bytes": length, "bridge_buffered_bytes": stats["buffered_body_bytes"], "sha256": digest.hexdigest()}

    def test_registry_limit_and_recovery(self):
        with self.session() as s:
            for _ in range(256):
                s._begin("GET", self.fixture["http"] + "/bytes", timeout=5)
            with self.assertRaisesRegex(FastlsError, "too many open requests"):
                s._begin("GET", self.fixture["http"])
            s._close_handle(next(iter(s._handles)))
            s._begin("GET", self.fixture["http"])
            self.assertEqual(s.stats()["open_requests"], 256)
        self.idle()
        with self.session() as s:
            self.assertEqual(s.get(self.fixture["https2"]).status_code, 200)

    def test_fragmented_sse_and_upload_boundaries(self):
        with self.session() as s:
            with s.stream("GET", self.fixture["https2"] + "/sse") as r:
                self.assertEqual(b"".join(r.iter_bytes(3)), "data: 第一条\n\ndata: second\n\n".encode())
            data = bytes([0, 255]) * (256 * 1024)
            self.assertEqual(s.post(self.fixture["https2"] + "/echo", data=data).content, data)
            # Large Python bodies now use the upload channel automatically.
            streamed = data + b"x"
            result = s.post(self.fixture["https2"] + "/upload", data=streamed).json()
            self.assertEqual(result["bytes"], len(streamed))
            self.assertEqual(result["sha256"], hashlib.sha256(streamed).hexdigest())
            # The original JSON-body limit remains enforced for C callers.
            with self.assertRaisesRegex(FastlsError, "512KiB"):
                s._native.start(dict(url=self.fixture["https2"] + "/upload", body="x" * (512 * 1024 + 1)))

    def test_concurrent_cancel_and_session_close(self):
        async def scenario():
            async with self.session(async_=True) as s:
                tokens = [uuid.uuid4().hex for _ in range(128)]
                async def work(i):
                    if i % 2:
                        task = asyncio.create_task(s.get(self.fixture["https2"] + "/wait?id=" + tokens[i]))
                        await self.wait_state(tokens[i], "started")
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        self.assertEqual((await s.get(self.fixture["https2"] + "/bytes?n=32768")).content, SLAB[:32768])
                await asyncio.wait_for(asyncio.gather(*(work(i) for i in range(128))), 30)
                tokens = [uuid.uuid4().hex for _ in range(32)]
                tasks = [asyncio.create_task(s.get(self.fixture["https2"] + "/wait?id=" + id)) for id in tokens]
                for id in tokens:
                    await self.wait_state(id, "started")
                await s.aclose()
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
                self.assertTrue(all(isinstance(result, FastlsError) for result in results))
        asyncio.run(scenario())

    def test_multithreaded_sync_session(self):
        with self.session() as s, ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: s.get(self.fixture["https1"] + "/bytes?n=4096").content, range(96)))
            self.assertTrue(all(body == SLAB[:4096] for body in results))

    def test_soak_and_resource_recovery(self):
        seconds = int(os.environ.get("FASTLS_STABILITY_SECONDS", "30"))
        self.assertTrue(1 <= seconds <= 300)
        async def batch(s, count=32):
            results = await asyncio.gather(*(s.get(self.fixture["https2"] + "/bytes?n=4096") for _ in range(count)))
            self.assertTrue(all(r.content == SLAB[:4096] for r in results))

        async def run():
            async with self.session(async_=True) as s:
                for _ in range(3):
                    await batch(s)
                self.idle(); gc.collect()
                baseline = dict(self.monitor.stats(), **resources())
                samples, requests = [], 0
                started = time.perf_counter()
                while time.perf_counter() - started < seconds:
                    await batch(s); requests += 32
                    # Exercise C-allocated JSON results and their matching free.
                    for _ in range(32):
                        self.assertIn("chrome/156/windows", s.list_profiles())
                    self.idle()
                    samples.append(dict(self.monitor.stats(), **resources()))
                    # Bound request churn so resource snapshots remain comparable.
                    await asyncio.sleep(0.1)
                final = samples[-1]
                self.assertLessEqual(final["goroutines"], baseline["goroutines"] + 16)
                self.assertLessEqual(max(sample["heap_inuse_bytes"] for sample in samples), baseline["heap_inuse_bytes"] + 96 * MIB)
                if baseline["rss_bytes"] is not None:
                    self.assertLessEqual(max(sample["rss_bytes"] for sample in samples), baseline["rss_bytes"] + 128 * MIB)
                    self.assertLessEqual(final["rss_bytes"], baseline["rss_bytes"] + 96 * MIB)
                if baseline["descriptors_or_handles"] is not None:
                    self.assertLessEqual(final["descriptors_or_handles"], baseline["descriptors_or_handles"] + 64)
                self.metrics["cases"]["soak"] = {"seconds": time.perf_counter() - started, "completed_requests": requests, "concurrency": 32, "baseline": baseline, "samples": samples}
        asyncio.run(run())


class EvidenceResult(unittest.TextTestResult):
    def record(self, test, status):
        if hasattr(type(test), "metrics"):
            type(test).metrics.setdefault("results", []).append({"case": test.id(), "status": status})

    def addSuccess(self, test):
        super().addSuccess(test); self.record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err); self.record(test, "failed")

    def addError(self, test, err):
        super().addError(test, err); self.record(test, "error")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None and hasattr(type(test), "metrics"):
            type(test).metrics.setdefault("results", []).append({"case": subtest.id(), "status": "failed", "error": str(err[1])})


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
