"""Streaming uploads through the real C ABI and independent H1/H2 fixture."""
import asyncio
import hashlib
import io
import time
import unittest
import uuid

from test_stability import FixtureCase, EvidenceResult, SLAB
from fastls import FastlsError, FastlsTimeoutError, Timeout


class Upload(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "upload-" + cls.metrics["label"]

    def test_64_mib_sync_h1_h2_bounded_queue(self):
        for protocol in ("https1", "https2"):
            with self.session(timeout=60) as session:
                peak = 0
                def source():
                    nonlocal peak
                    for _ in range(1024):
                        peak = max(peak, session.stats()["buffered_upload_bytes"])
                        yield SLAB
                result = session.post(self.fixture[protocol] + "/upload", data=source()).json()
                self.assertEqual(result["bytes"], 64 * 1024 * 1024)
                digest = hashlib.sha256()
                for _ in range(1024): digest.update(SLAB)
                self.assertEqual(result["sha256"], digest.hexdigest())
                self.assertEqual(result["protocol"], "HTTP/2.0" if protocol == "https2" else "HTTP/1.1")
                self.assertEqual(result["length"], -1)
                self.assertLessEqual(peak, 65536)
                self.metrics["cases"][protocol] = {**result, "peak_upload_queue": peak}

    def test_64_mib_async_and_event_loop(self):
        async def run():
            ticks = 0
            async def tick():
                nonlocal ticks
                while True:
                    ticks += 1
                    await asyncio.sleep(0.001)
            task = asyncio.create_task(tick())
            try:
                async with self.session(async_=True, timeout=60) as session:
                    async def source():
                        for _ in range(1024):
                            yield SLAB
                    response = await session.post(self.fixture["https2"] + "/upload", data=source())
                    self.assertEqual(response.json()["bytes"], 64 * 1024 * 1024)
                    self.assertGreater(ticks, 5)
            finally:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
        asyncio.run(run())

    def test_files_large_bytes_and_unicode(self):
        with self.session() as session:
            for data in (SLAB * 10, "中文" * 100000):
                expected = data.encode() if isinstance(data, str) else data
                response = session.post(self.fixture["https2"] + "/upload", data=data)
                self.assertEqual(response.json()["sha256"], hashlib.sha256(expected).hexdigest())
            file = io.BytesIO(SLAB * 20)
            result = session.post(self.fixture["https1"] + "/upload", data=file).json()
            self.assertEqual(result["bytes"], 20 * len(SLAB))
            self.assertFalse(file.closed)

    def test_empty_and_oversized_chunks(self):
        with self.session() as session:
            for source, expected in ((iter([]), b""), (iter([b"", bytearray(b"a"), memoryview(b"b"), SLAB * 3]), b"ab" + SLAB * 3)):
                result = session.post(self.fixture["https2"] + "/upload", data=source).json()
                self.assertEqual(result["sha256"], hashlib.sha256(expected).hexdigest())

    def test_sync_source_failure_cleans_up(self):
        def source():
            yield SLAB
            raise RuntimeError("producer failed")
        with self.session() as session:
            with self.assertRaisesRegex(RuntimeError, "producer failed"):
                session.post(self.fixture["https2"] + "/upload", data=source())
            self.assertEqual(session.get(self.fixture["https2"] + "/bytes?n=4").content, SLAB[:4])

    def test_async_source_failure_cleans_up(self):
        async def run():
            async def source():
                yield SLAB
                raise RuntimeError("async producer failed")
            async with self.session(async_=True) as session:
                with self.assertRaisesRegex(RuntimeError, "async producer failed"):
                    await session.post(self.fixture["https2"] + "/upload", data=source())
        asyncio.run(run())

    def test_async_cancel_waiting_producer_and_h2_sibling(self):
        async def run():
            finalized = asyncio.Event()
            async def source():
                try:
                    yield SLAB
                    await asyncio.Event().wait()
                finally:
                    finalized.set()
            async with self.session(async_=True) as session:
                task = asyncio.create_task(session.post(self.fixture["https2"] + "/upload", data=source()))
                await asyncio.sleep(0.15)
                response = await session.get(self.fixture["https2"] + "/bytes?n=4")
                self.assertEqual(response.content, SLAB[:4])
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertTrue(finalized.is_set())
                cleanup_started = asyncio.Event()
                async def closing_source():
                    try:
                        yield SLAB
                        await asyncio.Event().wait()
                    finally:
                        cleanup_started.set()
                        await asyncio.Event().wait()
                task = asyncio.create_task(session.post(self.fixture["https2"] + "/upload-reject", data=closing_source()))
                await asyncio.wait_for(cleanup_started.wait(), 5)
                task.cancel()  # Cancellation during producer cleanup must propagate.
                with self.assertRaises(asyncio.CancelledError): await task
        asyncio.run(run())

    def test_total_timeout_includes_stalled_async_producer(self):
        async def run():
            async def source():
                await asyncio.Event().wait()
                yield SLAB
            async with self.session(async_=True) as session:
                with self.assertRaises(FastlsTimeoutError) as caught:
                    await session.post(self.fixture["https2"] + "/upload", data=source(), timeout=0.2)
                self.assertEqual(caught.exception.phase, "total")
        asyncio.run(run())

    def test_headers_timeout_starts_after_upload(self):
        def source():
            yield SLAB
            time.sleep(0.2)
            yield SLAB
        with self.session() as session:
            response = session.post(self.fixture["https2"] + "/upload", data=source(), timeout=Timeout(total=5, headers=0.15))
            self.assertEqual(response.json()["bytes"], 2 * len(SLAB))

    def test_slow_receiver_backpressure(self):
        with self.session(timeout=30) as session:
            result = session.post(self.fixture["https2"] + "/upload-slow", data=(SLAB for _ in range(64))).json()
            self.assertEqual(result["bytes"], 4 * 1024 * 1024)
            self.assertEqual(result["sha256"], hashlib.sha256(SLAB * 64).hexdigest())

    def test_write_timeout_waiting_producer(self):
        async def run():
            for protocol in ("https1", "https2"):
                async def source():
                    yield SLAB
                    await asyncio.Event().wait()
                async with self.session(async_=True) as session:
                    with self.assertRaises(FastlsTimeoutError) as caught:
                        await session.post(self.fixture[protocol] + "/upload", data=source(), timeout=Timeout(total=5, write=0.2))
                    self.assertEqual(caught.exception.phase, "write")
        asyncio.run(run())

    def test_write_timeout_receiver_not_reading(self):
        for protocol in ("https1", "https2"):
            identity = uuid.uuid4().hex
            with self.session() as session:
                try:
                    with self.assertRaises(FastlsTimeoutError) as caught:
                        session.post(self.fixture[protocol] + "/wait?id=" + identity, data=(SLAB for _ in range(4096)), timeout=Timeout(total=10, write=0.25))
                    self.assertEqual(caught.exception.phase, "write")
                finally:
                    # H1 handlers which never read the request body cannot observe
                    # peer disconnect until they read or write. Release explicitly.
                    self.control("/release", identity)

    def test_write_progress_resets_inactivity_clock(self):
        async def run():
            async def source():
                for _ in range(8):
                    yield SLAB
                    await asyncio.sleep(0.08)
            async with self.session(async_=True) as session:
                response = await session.post(self.fixture["https2"] + "/upload", data=source(), timeout=Timeout(total=5, write=0.3))
                self.assertEqual(response.json()["bytes"], len(SLAB) * 8)
        asyncio.run(run())

    def test_redirect_does_not_replay_stream(self):
        with self.session() as session:
            response = session.post(self.fixture["https2"] + "/redirect-code?code=307", data=iter([SLAB]))
            self.assertEqual(response.status_code, 307)
            response = session.post(self.fixture["https2"] + "/redirect-code?code=302", data=(SLAB for _ in range(128)))
            self.assertEqual(response.json()["method"], "GET")
        async def run():
            async def source():
                while True: yield SLAB
            async with self.session(async_=True) as session:
                response = await session.post(self.fixture["https2"] + "/redirect-code?code=302", data=source())
                self.assertEqual(response.json()["method"], "GET")
        asyncio.run(run())

    def test_invalid_chunks_headers_and_async_in_sync(self):
        with self.session() as session:
            for source in (iter(["text"]), iter([123])):
                with self.assertRaises(TypeError):
                    session.post(self.fixture["https2"] + "/upload", data=source)
            for header in ("Content-Length", "Transfer-Encoding"):
                with self.assertRaises(FastlsError):
                    session.post(self.fixture["https2"] + "/upload", data=iter([SLAB]), headers={header: "1"})
            async def source(): yield SLAB
            with self.assertRaises(TypeError):
                session.post(self.fixture["https2"] + "/upload", data=source())

    def test_early_response_stops_async_producer(self):
        async def run():
            closed = asyncio.Event()
            async def source():
                try:
                    yield SLAB
                    await asyncio.Event().wait()
                finally: closed.set()
            async with self.session(async_=True) as session:
                response = await session.post(self.fixture["https2"] + "/upload-reject", data=source())
                self.assertEqual(response.status_code, 413)
                self.assertTrue(closed.is_set())
                sync_closed = []
                def sync_source():
                    try:
                        while True: yield SLAB
                    finally: sync_closed.append(True)
                response = await session.post(self.fixture["https2"] + "/upload-reject", data=sync_source())
                self.assertEqual(response.status_code, 413)
                self.assertTrue(sync_closed)
                # Early 2xx could be a duplex echo server. Refuse explicitly
                # rather than silently truncate the upload or wait for timeout.
                with self.assertRaisesRegex(FastlsError, "duplex"):
                    await session.post(self.fixture["https2"] + "/upload-early-ok", data=source())
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
