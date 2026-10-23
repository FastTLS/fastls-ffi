"""Readiness races, idle work, real notification sockets and cancellation."""
import asyncio
import secrets
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from test_stability import FixtureCase, EvidenceResult, resources
from fastls import Session, AsyncSession, FastlsError
from fastls._native import Native
from fastls.notifications import AsyncNotifier


class Notifications(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "notifications-" + cls.metrics["label"]

    def test_sync_wait_releases_gil_and_does_not_poll_body(self):
        with self.session(notifications="required") as session:
            self.assertEqual(session.notification_mode, "native-wait")
            with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as response:
                parts = response.iter_bytes()
                next(parts)
                reads, waits = 0, 0
                read, wait = session._native.read, session._native.wait
                def counted_read(*args):
                    nonlocal reads
                    reads += 1
                    return read(*args)
                def counted_wait(*args):
                    nonlocal waits
                    waits += 1
                    return wait(*args)
                def cancel():
                    time.sleep(0.25)
                    response.cancel()
                thread = threading.Thread(target=cancel)
                with patch.object(session._native, "read", counted_read), patch.object(session._native, "wait", counted_wait):
                    thread.start()
                    with self.assertRaises(FastlsError): next(parts)
                    thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertLessEqual(reads, 3)
                self.assertGreaterEqual(waits, 1)
                self.metrics["cases"]["sync_idle"] = dict(seconds=0.25, read_calls=reads, wait_calls=waits)

    def test_async_idle_body_has_no_timer_polling_or_python_threads(self):
        baseline = {t.ident for t in threading.enumerate()}
        async def run():
            async with self.session(async_=True, notifications="required") as session:
                self.assertEqual(session.notification_mode, "async-events")
                async with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as response:
                    parts = response.aiter_bytes()
                    await anext(parts)
                    reads, ticks = 0, 0
                    native_read = session._native.read
                    def read(*args):
                        nonlocal reads
                        reads += 1
                        return native_read(*args)
                    with patch.object(session._native, "read", read):
                        task = asyncio.create_task(anext(parts))
                        await asyncio.sleep(0.05)
                        before = reads
                        for _ in range(5):
                            await asyncio.sleep(0.05)
                            ticks += 1
                        self.assertLessEqual(reads-before, 1)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError): await task
                    self.assertEqual({t.ident for t in threading.enumerate()}, baseline)
                    self.metrics["cases"]["async_idle"] = dict(seconds=0.25, read_calls=reads-before, loop_ticks=ticks)
        asyncio.run(run())

    def test_async_idle_headers_do_not_poll_info(self):
        async def run():
            id = uuid.uuid4().hex
            async with self.session(async_=True, notifications="required") as session:
                infos, native_call = 0, session._native.call
                def call(name, *args):
                    nonlocal infos
                    infos += int(name == "fastls_info")
                    return native_call(name, *args)
                with patch.object(session._native, "call", call):
                    task = asyncio.create_task(session.get(self.fixture["https2"]+"/wait?id="+id))
                    await self.wait_state(id, "started")
                    await asyncio.sleep(0.05)
                    before = infos
                    await asyncio.sleep(0.25)
                    self.assertLessEqual(infos-before, 1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
        asyncio.run(run())

    def test_generation_and_cancelled_shared_waiter(self):
        async def run():
            async with self.session(async_=True, notifications="required") as session:
                async with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as response:
                    await anext(response.aiter_bytes())
                    n, h = session._notifier, response._handle
                    await asyncio.sleep(0.03)
                    for _ in range(1000):
                        stamp = n.generation(h)
                        n.pulse(h)  # Signal between the caller's state check and wait.
                        await asyncio.wait_for(n.wait(h, stamp), 1)
                    stamp = n.generation(h)
                    first = asyncio.create_task(n.wait(h, stamp))
                    second = asyncio.create_task(n.wait(h, stamp))
                    await asyncio.sleep(0)
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError): await first
                    n.pulse(h)
                    await asyncio.wait_for(second, 1)
        asyncio.run(run())

    def test_128_mixed_requests_one_notification_connection(self):
        async def run():
            async with self.session(async_=True, notifications="required") as session:
                await session.get(self.fixture["https2"]+"/bytes?n=4")
                self.assertEqual(session.stats()["notifiers"], 1)
                ids = [uuid.uuid4().hex for _ in range(32)]
                held = [asyncio.create_task(session.get(self.fixture["https2"]+"/sse-hold?id="+id)) for id in ids]
                healthy = [asyncio.create_task(session.get(self.fixture["https2"]+"/bytes?n=128")) for _ in range(96)]
                await asyncio.sleep(0.08)
                for task in held: task.cancel()
                results = await asyncio.gather(*held, return_exceptions=True)
                self.assertTrue(all(isinstance(r, asyncio.CancelledError) for r in results))
                responses = await asyncio.gather(*healthy)
                self.assertTrue(all(len(r.content)==128 for r in responses))
                self.assertFalse(session._notifier.states)
                self.assertEqual(session.stats()["notifiers"], 1)
                self.metrics["cases"]["concurrency"] = dict(requests=128, cancelled=32, healthy=96, notifiers=1)
        asyncio.run(run())

    def test_independent_sessions_own_independent_notifiers(self):
        async def run():
            async with self.session(async_=True, notifications="required") as first, self.session(async_=True, notifications="required") as second:
                await asyncio.gather(first.get(self.fixture["https2"]), second.get(self.fixture["https2"]))
                self.assertEqual(first.stats()["notifiers"], 2)
                self.assertIsNot(first._notifier, second._notifier)
                await first.aclose()
                self.assertEqual(second.stats()["notifiers"], 1)
                self.assertEqual((await second.get(self.fixture["https2"])).status_code, 200)
        asyncio.run(run())

    def test_cancel_during_setup_closes_candidates_and_can_retry(self):
        async def run():
            session = self.session(async_=True, notifications="required")
            try:
                task = None
                seen = []
                original = AsyncNotifier._accept
                def accept(notifier, reader, writer):
                    seen.append(notifier)
                    original(notifier, reader, writer)
                    task.cancel()  # Before the newly created receiver task takes a turn.
                with patch.object(AsyncNotifier, "_accept", accept):
                    task = asyncio.create_task(session.get(self.fixture["https2"]))
                    with self.assertRaises(asyncio.CancelledError): await task
                self.assertTrue(seen)
                self.assertFalse(seen[0].writers)
                self.assertEqual(session.stats()["notifiers"], 0)
                self.assertEqual(session.stats()["open_requests"], 0)
                self.assertEqual((await session.get(self.fixture["https2"])).status_code, 200)
            finally:
                await session.aclose()
        asyncio.run(run())

    def test_response_close_from_other_thread_wakes_reader(self):
        async def run():
            async with self.session(async_=True, notifications="required") as session:
                async with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as response:
                    parts = response.aiter_bytes()
                    await anext(parts)
                    pending = asyncio.create_task(anext(parts))
                    await asyncio.sleep(0.03)
                    thread = threading.Thread(target=response.close)
                    thread.start()
                    with self.assertRaises(FastlsError): await asyncio.wait_for(pending, 2)
                    thread.join(2)
                self.assertEqual((await session.get(self.fixture["https2"])).status_code, 200)
        asyncio.run(run())

    def test_session_close_from_other_thread_wakes_all(self):
        async def run():
            session = self.session(async_=True, notifications="required")
            async with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as response:
                parts = response.aiter_bytes()
                await anext(parts)
                pending = asyncio.create_task(anext(parts))
                await asyncio.sleep(0.03)
                thread = threading.Thread(target=session.close)
                thread.start()
                with self.assertRaises(FastlsError): await asyncio.wait_for(pending, 2)
                thread.join(2)
            await session.aclose()
            self.assertFalse(session._notifier.states)
        asyncio.run(run())

    def test_broken_notification_channel_fails_waiters(self):
        async def run():
            async with self.session(async_=True, notifications="required") as session:
                async with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as response:
                    parts = response.aiter_bytes()
                    await anext(parts)
                    task = asyncio.create_task(anext(parts))
                    await asyncio.sleep(0.03)
                    session._notifier.writer.close()
                    with self.assertRaisesRegex(FastlsError, "notification"):
                        await asyncio.wait_for(task, 2)
                self.assertIsNotNone(session._notifier.failure)
                with self.assertRaisesRegex(FastlsError, "notification"):
                    await session.get(self.fixture["https2"])
        asyncio.run(run())

    def test_explicit_poll_mode_sync_async_and_sse(self):
        with self.session(notifications="poll") as session:
            self.assertEqual(session.notification_mode, "poll")
            with patch.object(session._native, "wait", side_effect=AssertionError("native wait used")):
                self.assertEqual(session.get(self.fixture["https2"]).status_code, 200)
        async def run():
            async with self.session(async_=True, notifications="poll") as session:
                async with session.sse("GET", self.fixture["https2"]+"/sse-vectors") as events:
                    self.assertEqual(len([event async for event in events]), 5)
                self.assertIsNone(session._notifier)
                self.assertEqual(session.notification_mode, "poll")
        asyncio.run(run())

    def test_old_capability_auto_fallback_and_required_gate(self):
        original = Native.call
        def call(native, name, *args):
            result = original(native, name, *args)
            return [x for x in result if x != "session_notifications"] if name=="fastls_features" else result
        with patch.object(Native, "call", call):
            with self.session() as session:
                self.assertEqual(session.notification_mode, "poll")
                self.assertEqual(session.get(self.fixture["https2"]).status_code, 200)
            with self.assertRaisesRegex(FastlsError, "newer"):
                self.session(notifications="required")

    def test_invalid_options_and_native_wait_arguments(self):
        for value in (True, False, "invalid", None):
            with self.assertRaises(ValueError): self.session(notifications=value)
        with self.session(notifications="required") as session:
            lib = session._native.lib
            self.assertEqual(lib.fastls_wait(0, 1, 0), -2)
            with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as response:
                for mask, timeout in ((0, 0), (8, 0), (2, 60001)):
                    self.assertEqual(lib.fastls_wait(response._handle, mask, timeout), -2)
                self.assertEqual(lib.fastls_wait(response._handle, 1, 0), 0)

    def test_watch_registration_validation_and_late_registration(self):
        with self.session() as session:
            for port, token, length in ((0, b"x"*32, 32), (1, None, 32), (1, b"x", 1)):
                with self.assertRaises(FastlsError):
                    session._native.call("fastls_session_watch", session._session, port, token, length)
            with self.assertRaises(FastlsError): session._native.call("fastls_session_events", session._session)
            with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex):
                with self.assertRaisesRegex(FastlsError, "before"):
                    session._native.call("fastls_session_watch", session._session, 1, b"x"*32, 32)

    def test_repeated_session_resource_recovery(self):
        baseline = self.monitor.stats()
        async def run():
            for _ in range(25):
                session = self.session(async_=True, notifications="required")
                async with session:
                    await session.get(self.fixture["https2"]+"/bytes?n=4")
                self.assertFalse(session._notifier.tasks)
                self.assertFalse(session._notifier.writers)
                self.assertFalse(session._notifier.states)
                self.assertEqual(session.stats()["notifiers"], 0)
        asyncio.run(run())
        final = self.idle()
        self.assertLessEqual(final["goroutines"], baseline["goroutines"]+6)
        self.metrics["cases"]["recovery"] = dict(sessions=25, baseline=baseline, final=final, process=resources())

    def test_concurrent_first_requests_initialize_once(self):
        async def run():
            session = self.session(async_=True, notifications="required")
            try:
                responses = await asyncio.gather(*(session.get(self.fixture["https2"]+"/bytes?n=4") for _ in range(32)))
                self.assertEqual(len(responses), 32)
                self.assertEqual(session.stats()["notifiers"], 1)
                self.assertEqual(len(session._notifier.tasks), 1)
            finally:
                await session.aclose()
        asyncio.run(run())

    def test_overflow_hint_rescans_all_owned_handles(self):
        async def run():
            async with self.session(async_=True, notifications="required") as session:
                ids = [uuid.uuid4().hex for _ in range(2)]
                async with session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+ids[0]) as first, session.stream("GET", self.fixture["https2"]+"/sse-hold?id="+ids[1]) as second:
                    a, b = first.aiter_bytes(), second.aiter_bytes()
                    await anext(a); await anext(b)
                    native_call = session._native.call
                    def call(name, *args):
                        result = native_call(name, *args)
                        return dict(handles=[], overflow=True) if name=="fastls_session_events" else result
                    with patch.object(session._native, "call", call):
                        tasks = [asyncio.create_task(anext(a)), asyncio.create_task(anext(b))]
                        await asyncio.sleep(0.03)
                        for id in ids: self.control("/release", id)
                        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
                        self.assertTrue(all(b"last" in result for result in results))
        asyncio.run(run())

    def test_wrong_handshake_fails_cleanly_then_retries(self):
        async def run():
            session = self.session(async_=True, notifications="required")
            try:
                with patch.object(secrets, "compare_digest", return_value=False):
                    with self.assertRaisesRegex(FastlsError, "start native notifications"):
                        await session.get(self.fixture["https2"])
                self.assertEqual(session.stats()["notifiers"], 0)
                self.assertEqual(session.stats()["open_requests"], 0)
                self.assertEqual((await session.get(self.fixture["https2"])).status_code, 200)
            finally:
                await session.aclose()
        asyncio.run(run())

    def test_context_initializes_before_body_and_cleans_failed_entry(self):
        async def run():
            async with self.session(async_=True, notifications="required") as session:
                self.assertTrue(session._notifier.ready.done())
                self.assertEqual(session.stats()["notifiers"], 1)
                self.assertEqual(session.stats()["open_requests"], 0)
            candidate = self.session(async_=True, notifications="required")
            with patch.object(secrets, "compare_digest", return_value=False):
                with self.assertRaises(FastlsError):
                    async with candidate: pass
            self.assertTrue(candidate._closed)
            self.assertEqual(candidate.stats()["notifiers"], 0)
            self.assertEqual(candidate.stats()["open_sessions"], 1)  # Diagnostic monitor only.
        asyncio.run(run())

    def test_different_event_loop_rejected_and_sync_close_allowed(self):
        session = self.session(async_=True, notifications="required")
        asyncio.run(session.get(self.fixture["https2"]))
        with self.assertRaisesRegex(FastlsError, "event loops"):
            asyncio.run(session.get(self.fixture["https2"]))
        session.close()
        self.assertEqual(session.stats()["notifiers"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
