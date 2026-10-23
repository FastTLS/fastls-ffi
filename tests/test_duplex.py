"""Real HTTP/2 simultaneous request and response streams."""
import asyncio
import hashlib
import unittest

from test_stability import FixtureCase, EvidenceResult, SLAB
from fastls import FastlsError, FastlsTimeoutError, Timeout


class Duplex(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "duplex-" + cls.metrics["label"]

    def test_async_first_reply_before_upload_and_half_close(self):
        async def run():
            ready, echoed = asyncio.Event(), asyncio.Event()
            async def source():
                await ready.wait()
                yield b"first"
                await echoed.wait()
                yield b"last"
            async with self.session(async_=True) as session:
                async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True) as response:
                    parts = response.aiter_bytes()
                    self.assertEqual(await anext(parts), b"ready\n")
                    ready.set()
                    self.assertEqual(await anext(parts), b"first")
                    echoed.set()
                    rest = b"".join([part async for part in parts])
                    self.assertEqual(rest, b"last")
                    await response.wait_upload()
        asyncio.run(asyncio.wait_for(run(), 10))

    def test_sync_manual_exchange_no_worker_threads(self):
        import threading
        baseline = {t.ident for t in threading.enumerate()}
        with self.session() as session:
            with session.duplex("POST", self.fixture["https2"] + "/duplex") as exchange:
                response = exchange.response
                parts = response.iter_bytes(5)
                self.assertEqual(next(parts) + next(parts), b"ready\n")
                exchange.send(b"first")
                self.assertEqual(next(parts), b"first")
                exchange.finish_upload()
                self.assertEqual(b"".join(parts), b"")
                self.assertEqual({t.ident for t in threading.enumerate()}, baseline)

    def test_64_mib_bidirectional_hash_and_bounds(self):
        async def run():
            async def source():
                for _ in range(1024): yield SLAB
            async with self.session(async_=True, timeout=90) as session:
                digest, size, peak_up, peak_down = hashlib.sha256(), 0, 0, 0
                async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True) as response:
                    marker = bytearray()
                    async for part in response.aiter_bytes():
                        if len(marker) < 6:
                            count = min(6-len(marker), len(part)); marker.extend(part[:count]); part = part[count:]
                        digest.update(part); size += len(part)
                        stats = session.stats()
                        peak_up = max(peak_up, stats["buffered_upload_bytes"])
                        peak_down = max(peak_down, stats["buffered_body_bytes"])
                    self.assertEqual(marker, b"ready\n")
                expected = hashlib.sha256()
                for _ in range(1024): expected.update(SLAB)
                self.assertEqual(size, 64 * 1024 * 1024)
                self.assertEqual(digest.hexdigest(), expected.hexdigest())
                self.assertLessEqual(peak_up, 65536)
                self.assertLessEqual(peak_down, 3 * 65536)
                self.metrics["cases"]["large_duplex"] = dict(bytes=size, sha256=digest.hexdigest(), peak_upload=peak_up, peak_download=peak_down)
        asyncio.run(run())

    def test_slow_response_consumer_applies_backpressure(self):
        async def run():
            produced = 0
            async def source():
                nonlocal produced
                for _ in range(512):
                    produced += 1
                    yield SLAB
            async with self.session(async_=True, timeout=45) as session:
                async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True) as response:
                    await asyncio.sleep(0.25)
                    before = produced
                    await asyncio.sleep(0.25)
                    self.assertLess(produced, 512)
                    self.assertLessEqual(session.stats()["buffered_upload_bytes"], 65536)
                    self.assertLessEqual(session.stats()["buffered_body_bytes"], 3*65536)
                    size = sum([len(part) async for part in response.aiter_bytes()])
                    self.assertEqual(size, 32*1024*1024+6)
                    self.metrics["cases"]["backpressure"] = dict(produced_at_pause=before, total=produced)
        asyncio.run(run())

    def test_producer_failure_after_headers_is_propagated(self):
        async def run():
            fail = asyncio.Event()
            async def source():
                await fail.wait()
                raise RuntimeError("late producer failure")
                yield b""
            async with self.session(async_=True) as session:
                with self.assertRaisesRegex(RuntimeError, "late producer failure"):
                    async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True) as response:
                        parts = response.aiter_bytes()
                        self.assertEqual(await anext(parts), b"ready\n")
                        fail.set()
                        await anext(parts)
        asyncio.run(run())

    def test_cancel_read_and_h2_sibling_survives(self):
        async def run():
            finalized = asyncio.Event()
            async def source():
                try:
                    await asyncio.Event().wait()
                    yield b""
                finally: finalized.set()
            async with self.session(async_=True) as session:
                async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True) as response:
                    parts = response.aiter_bytes()
                    await anext(parts)
                    task = asyncio.create_task(anext(parts))
                    await asyncio.sleep(0.05)
                    sibling = await session.get(self.fixture["https2"] + "/bytes?n=4")
                    self.assertEqual(sibling.content, SLAB[:4])
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                    self.assertTrue(finalized.is_set())
        asyncio.run(run())

    def test_close_session_cleans_paused_producer(self):
        async def run():
            finalized = asyncio.Event()
            async def source():
                try:
                    await asyncio.Event().wait()
                    yield b""
                finally:
                    await asyncio.sleep(0.02)
                    finalized.set()
            session = self.session(async_=True)
            async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True) as response:
                response.cancel()
                await asyncio.sleep(0)  # Session close must not interrupt async finalization.
                await session.aclose()
                self.assertTrue(finalized.is_set())
                self.assertFalse(session._producers)
        asyncio.run(run())

    def test_write_timeout_after_response_headers(self):
        async def run():
            async def source():
                await asyncio.Event().wait()
                yield b""
            async with self.session(async_=True) as session:
                with self.assertRaises(FastlsTimeoutError) as caught:
                    async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True, timeout=Timeout(total=5, write=0.25)) as response:
                        await response.aread()
                self.assertEqual(caught.exception.phase, "write")
        asyncio.run(run())

    def test_read_timeout_while_upload_keeps_progressing(self):
        async def run():
            async def source():
                while True:
                    yield SLAB
                    await asyncio.sleep(0.04)
            async with self.session(async_=True) as session:
                with self.assertRaises(FastlsTimeoutError) as caught:
                    async with session.stream("POST", self.fixture["https2"] + "/duplex-silent", data=source(), duplex=True, timeout=Timeout(total=5, write=1, read=0.2)) as response:
                        await response.aread()
                self.assertEqual(caught.exception.phase, "read")
        asyncio.run(run())

    def test_total_timeout_and_cancel_paused_consumer(self):
        async def run():
            async def source():
                await asyncio.Event().wait()
                yield b""
            async with self.session(async_=True) as session:
                with self.assertRaises(FastlsTimeoutError) as caught:
                    async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True, timeout=0.2) as response:
                        await response.aread()
                self.assertEqual(caught.exception.phase, "total")
                async with session.stream("POST", self.fixture["https2"] + "/duplex", data=source(), duplex=True) as response:
                    response.cancel()
                    with self.assertRaises(FastlsError): await response.aread()
        asyncio.run(run())

    def test_gzip_duplex_and_buffered_convenience_request(self):
        async def run():
            async def source():
                yield SLAB
                await asyncio.sleep(0.05)
                yield SLAB
            async with self.session(async_=True) as session:
                response = await session.post(self.fixture["https2"] + "/duplex-gzip", data=source(), duplex=True)
                self.assertTrue(response.decoded)
                self.assertEqual(response.content, b"ready\n" + SLAB * 2)
                empty = await session.post(self.fixture["https2"] + "/duplex", data=None, duplex=True)
                self.assertEqual(empty.content, b"ready\n")
        asyncio.run(run())

    def test_reject_redirect_and_response_eof_stops_upload(self):
        async def run():
            for path, status in (("/upload-reject", 413), ("/redirect-code?code=302", 302), ("/upload-early-ok", 200)):
                finalized = asyncio.Event()
                async def source():
                    try:
                        yield SLAB
                        await asyncio.Event().wait()
                    finally: finalized.set()
                async with self.session(async_=True) as session:
                    response = await session.post(self.fixture["https2"] + path, data=source(), duplex=True)
                    self.assertEqual(response.status_code, status)
                    self.assertTrue(finalized.is_set())
        asyncio.run(run())

    def test_h1_and_invalid_options_fail(self):
        async def run():
            async with self.session(async_=True) as session:
                for protocol in ("http", "https1"):
                    with self.assertRaisesRegex(FastlsError, "HTTP/2"):
                        await session.post(self.fixture[protocol] + "/upload", data=iter([b"data"]), duplex=True)
                with self.assertRaises(TypeError):
                    await session.post(self.fixture["https2"], duplex="yes")
        asyncio.run(run())
        with self.session() as session:
            with self.assertRaises(TypeError):
                session.post(self.fixture["https2"], duplex=True)
            with self.assertRaises(TypeError):
                with session.duplex("POST", self.fixture["https2"], data=b"wrong"): pass


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
