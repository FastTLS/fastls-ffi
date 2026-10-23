"""Reusable-session acceptance against independently observed connection IDs."""
import asyncio
import unittest
import uuid
import time

from test_stability import FixtureCase, EvidenceResult, FastlsError
from test_network import WireServer


class Pooling(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "pooling-" + cls.metrics["label"]

    def connection(self, response):
        value = response.headers["X-Test-Connection"]
        self.assertNotEqual(value, "<nil>")
        return value

    def test_sequential_reuse_and_idle_close(self):
        observed = {}
        for name in ("http", "https1", "https2"):
            with self.subTest(protocol=name), self.session() as session:
                connections = [self.connection(session.get(self.fixture[name] + "/bytes?n=4", headers={"X-Sequence": str(i)})) for i in range(6)]
                self.assertEqual(len(set(connections)), 1)
                session.close_idle_connections()
                next_id = self.connection(session.get(self.fixture[name] + "/bytes?n=4"))
                self.assertNotEqual(next_id, connections[0])
                observed[name] = {"reused": connections, "after_idle_close": next_id}
        self.metrics["cases"]["reuse"] = observed

    def test_sessions_profiles_and_trust_are_isolated(self):
        with self.session() as first, self.session() as second:
            url = self.fixture["https2"] + "/bytes?n=4"
            a = self.connection(first.get(url))
            self.assertNotEqual(a, self.connection(second.get(url)))
            other = next(p for p in first.list_profiles() if p.startswith("chrome/") and p != first.profile)
            b = self.connection(first.get(url, profile=other))
            self.assertNotEqual(a, b)
            self.assertEqual(a, self.connection(first.get(url)))
            self.assertEqual(b, self.connection(first.get(url, profile=other)))
            # Even inside one native session, a changed CA must not borrow the trusted pool.
            original = first._ca_pem
            from pathlib import Path
            first._ca_pem = Path(self.fixture["wrong_ca_file"]).read_text()
            with self.assertRaises(FastlsError):
                first.get(url)
            first._ca_pem = original
            self.assertEqual(a, self.connection(first.get(url)))

    def test_first_request_headers_do_not_become_pool_defaults(self):
        for name in ("https1", "https2"):
            with self.session() as session:
                url = self.fixture[name] + "/inspect"
                first = session.get(url, headers={"User-Agent": "first-only", "X-Only": "first", "Cookie": "manual=first"})
                self.assertEqual(first.json()["headers"]["User-Agent"], ["first-only"])
                second = session.get(url)
                headers = second.json()["headers"]
                self.assertNotEqual(headers["User-Agent"], ["first-only"])
                self.assertNotIn("X-Only", headers)
                self.assertNotIn("Cookie", headers)
                self.assertEqual(self.connection(first), self.connection(second))

    def test_h2_multiplex_cancel_and_close_idle_preserve_other_stream(self):
        async def run():
            async with self.session(async_=True) as session:
                url = self.fixture["https2"]
                warm = self.connection(await session.get(url + "/bytes?n=4"))
                tokens = [uuid.uuid4().hex for _ in range(24)]
                async def work(index):
                    async with session.stream("GET", url + "/hold?id=" + tokens[index]) as response:
                        self.assertEqual(self.connection(response), warm)
                        chunks = response.aiter_bytes(5)
                        self.assertEqual(await anext(chunks), b"first")
                        await ready.put(index)
                        await release.wait()
                        if index % 2:
                            response.cancel()
                            with self.assertRaises(FastlsError):
                                await anext(chunks)
                        else:
                            self.control("/release", tokens[index])
                            self.assertEqual(await anext(chunks), b"last!")
                ready, release = asyncio.Queue(), asyncio.Event()
                tasks = [asyncio.create_task(work(i)) for i in range(24)]
                try:
                    for _ in tasks:
                        await asyncio.wait_for(ready.get(), 10)
                    session.close_idle_connections()
                    release.set()
                    await asyncio.wait_for(asyncio.gather(*tasks), 15)
                finally:
                    release.set()
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                # Cancellation of half the streams must not discard the shared connection.
                self.assertEqual(warm, self.connection(await session.get(url + "/bytes?n=4")))
        asyncio.run(run())

    def test_session_close_cancels_active_body_and_recovers_registry(self):
        baseline = self.monitor.stats()["open_sessions"]
        session = self.session()
        with session.stream("GET", self.fixture["https2"] + "/hold?id=" + uuid.uuid4().hex) as response:
            chunks = response.iter_bytes(5)
            self.assertEqual(next(chunks), b"first")
            session.close()
            with self.assertRaises(FastlsError):
                next(chunks)
        session.close()
        self.idle()
        self.assertEqual(self.monitor.stats()["open_sessions"], baseline)
        with self.assertRaises(FastlsError):
            session.get(self.fixture["https2"])

    def test_discovery_waiter_deadline_and_cached_origin_progress(self):
        async def run():
            with WireServer() as peer:
                async with self.session(async_=True) as session:
                    healthy_url = self.fixture["https2"] + "/bytes?n=4"
                    connection = self.connection(await session.get(healthy_url))
                    stalled_url = "https://" + peer.address
                    stalled = asyncio.create_task(session.get(stalled_url, timeout=20))
                    try:
                        deadline = time.perf_counter() + 5
                        while not peer.accepted.is_set() and time.perf_counter() < deadline:
                            await asyncio.sleep(0.02)
                        self.assertTrue(peer.accepted.is_set())
                        started = time.perf_counter()
                        with self.assertRaises(FastlsError):
                            await session.get(stalled_url, timeout=0.3)
                        self.assertLess(time.perf_counter() - started, 3)
                        response = await asyncio.wait_for(session.get(healthy_url), 3)
                        self.assertEqual(connection, self.connection(response))
                    finally:
                        stalled.cancel()
                        await asyncio.gather(stalled, return_exceptions=True)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
