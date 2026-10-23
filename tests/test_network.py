"""Deterministic network faults; only loopback endpoints and real native calls."""
import asyncio
import base64
from contextlib import contextmanager
import socket
import threading
import time
import unittest
import uuid

from test_stability import FixtureCase, EvidenceResult, FastlsError


class WireServer:
    """One TCP peer: accept, optionally return exact wire bytes, observe closure."""
    def __init__(self, reply=None):
        self.reply = reply
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.address = "127.0.0.1:" + str(self.listener.getsockname()[1])
        self.accepted = threading.Event()
        self.disconnected = threading.Event()
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    peer, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            with peer:
                peer.settimeout(0.1)
                self.accepted.set()
                request = bytearray()
                while not self.stop.is_set():
                    try:
                        data = peer.recv(65536)
                    except socket.timeout:
                        continue
                    except (ConnectionResetError, ConnectionAbortedError):
                        self.disconnected.set()
                        return
                    if not data:
                        self.disconnected.set()
                        return
                    request.extend(data)
                    if self.reply is not None and b"\r\n\r\n" in request:
                        parts = [self.reply] if isinstance(self.reply, bytes) else self.reply
                        for part in parts:
                            peer.sendall(part)
                            if len(parts) > 1:
                                time.sleep(0.002)
                        return  # Closing the socket is part of the fault.
        except BaseException as error:
            self.error = error

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(3)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError("fault peer failed to shut down")
        if self.error:
            raise self.error


class NetworkFaults(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["suite"] = "network-faults"
        cls.metrics["label"] = "network-" + cls.metrics["label"]

    @contextmanager
    def bounded_failure(self, maximum=5):
        started = time.perf_counter()
        with self.assertRaises(FastlsError):
            yield
        self.assertLess(time.perf_counter() - started, maximum)

    def healthy(self, session, name="https2"):
        response = session.get(self.fixture[name] + "/bytes?n=4")
        self.assertEqual(response.content, bytes([0, 1, 128, 255]))

    def test_silent_http_and_tls_handshake_timeout(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme), WireServer() as peer, self.session() as session:
                with self.bounded_failure():
                    session.get(scheme + "://" + peer.address, timeout=0.35)
                self.assertTrue(peer.accepted.wait(1), "timeout happened before contacting the peer")
                self.assertTrue(peer.disconnected.wait(3), "timed-out connection was retained")
                self.idle()
                self.healthy(session)

    def test_cancel_stalled_tls_keeps_event_loop_running(self):
        async def run():
            with WireServer() as peer:
                async with self.session(async_=True) as session:
                    task = asyncio.create_task(session.get("https://" + peer.address, timeout=20))
                    ticks, deadline = 0, time.perf_counter() + 5
                    while not peer.accepted.is_set() and time.perf_counter() < deadline:
                        ticks += 1
                        await asyncio.sleep(0.02)
                    self.assertTrue(peer.accepted.is_set())
                    # The peer never sends ServerHello; cancellation must interrupt TLS.
                    for _ in range(5):
                        ticks += 1
                        await asyncio.sleep(0.02)
                    task.cancel()
                    started = time.perf_counter()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertLess(time.perf_counter() - started, 3)
                    self.assertGreaterEqual(ticks, 5)
                    deadline = time.perf_counter() + 3
                    while not peer.disconnected.is_set() and time.perf_counter() < deadline:
                        await asyncio.sleep(0.02)
                    self.assertTrue(peer.disconnected.is_set())
                    response = await session.get(self.fixture["https2"] + "/bytes?n=4")
                    self.assertEqual(len(response.content), 4)
        asyncio.run(run())

    def test_invalid_http_wire_is_never_successful_eof(self):
        cases = {
            "status": b"HTTP/1.1 nope\r\n\r\n",
            "chunk-size": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nZZ\r\nbad\r\n",
            "chunk-truncated": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\nabc",
            "length-truncated": b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nabc",
            "length-conflict": b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nab",
            "header": b"HTTP/1.1 200 OK\r\ninvalid header\r\n\r\n",
        }
        for name, reply in cases.items():
            with self.subTest(fault=name), WireServer(reply) as peer, self.session() as session:
                with self.bounded_failure():
                    session.get("http://" + peer.address, timeout=2)
                self.assertTrue(peer.accepted.is_set())
                self.idle()
                self.healthy(session)
        self.metrics["cases"]["malformed_wire"] = list(cases)

    def test_fragmented_valid_chunked_binary_with_trailers(self):
        wire = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nTrailer: X-Checksum\r\n\r\n"
                b"4;fixture=yes\r\n\x00\x01\x80\xff\r\n3\r\nend\r\n0\r\nX-Checksum: fixture\r\n\r\n")
        # One-byte writes deliberately split CRLF, chunk metadata and body data.
        with WireServer([wire[i:i+1] for i in range(len(wire))]) as peer, self.session() as session:
            response = session.get("http://" + peer.address, timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"\x00\x01\x80\xffend")

    def test_aborted_h1_connection_and_h2_stream_recover(self):
        for name in ("https1", "https2"):
            with self.subTest(protocol=name), self.session() as session:
                for _ in range(8):
                    with self.bounded_failure():
                        session.get(self.fixture[name] + "/abort")
                    self.healthy(session, name)
                self.idle()

    def test_body_deadline_while_waiting_and_consumer_paused(self):
        for name in ("https1", "https2"):
            for paused in (False, True):
                with self.subTest(protocol=name, paused=paused), self.session() as session:
                    id = uuid.uuid4().hex
                    with session.stream("GET", self.fixture[name] + "/hold?id=" + id, timeout=0.8) as response:
                        chunks = response.iter_bytes(5)
                        self.assertEqual(next(chunks), b"first")
                        if paused:
                            time.sleep(1)
                        with self.bounded_failure():
                            next(chunks)
                    self.idle()
                    self.healthy(session, name)

    def test_async_cancel_while_waiting_for_next_body_chunk(self):
        async def run():
            for name in ("https1", "https2"):
                id = uuid.uuid4().hex
                async with self.session(async_=True) as session:
                    async with session.stream("GET", self.fixture[name] + "/hold?id=" + id) as response:
                        chunks = response.aiter_bytes(5)
                        self.assertEqual(await anext(chunks), b"first")
                        pending = asyncio.create_task(anext(chunks))
                        await asyncio.sleep(0.08)
                        self.assertFalse(pending.done())
                        pending.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await pending
                        await self.wait_state(id, "cancelled")
                    healthy = await session.get(self.fixture[name] + "/bytes?n=4")
                    self.assertEqual(len(healthy.content), 4)
        asyncio.run(run())

    def test_redirect_method_and_binary_body_contract(self):
        payload = b"\x00\x80\xffredirect\x00"
        for name in ("https1", "https2"):
            with self.session() as session:
                for status in (301, 302, 303, 307, 308):
                    with self.subTest(protocol=name, status=status):
                        result = session.post(self.fixture[name] + "/redirect-code?code=" + str(status), data=payload).json()
                        preserve = status in (307, 308)
                        self.assertEqual(result["method"], "POST" if preserve else "GET")
                        self.assertEqual(base64.b64decode(result["body"] or ""), payload if preserve else b"")

    def test_redirect_disabled_and_redirect_loop_are_bounded(self):
        for name in ("https1", "https2"):
            with self.session() as session:
                response = session.get(self.fixture[name] + "/redirect-code?code=307", allow_redirects=False)
                self.assertEqual(response.status_code, 307)
                self.assertEqual(response.headers["Location"], "/inspect")
                with self.bounded_failure():
                    session.get(self.fixture[name] + "/redirect-loop", timeout=3)
                self.idle()
                self.healthy(session, name)

    def test_redirect_to_different_host_strips_sensitive_headers(self):
        for name in ("https1", "https2"):
            with self.session() as session:
                headers = {"Authorization": "Bearer fixture-only", "Cookie": "fixture=private", "X-Test-Public": "keep"}
                original = session.get(self.fixture[name] + "/inspect", headers=headers).json()
                self.assertEqual(original["headers"]["Authorization"], [headers["Authorization"]])
                self.assertEqual(original["headers"]["Cookie"], [headers["Cookie"]])
                redirected = session.get(self.fixture[name] + "/redirect-host", headers=headers).json()
                self.assertNotIn("Authorization", redirected["headers"])
                self.assertNotIn("Cookie", redirected["headers"])
                self.assertEqual(redirected["headers"]["X-Test-Public"], ["keep"])

    def test_head_empty_responses_and_error_status(self):
        for name in ("https1", "https2"):
            with self.session() as session:
                response = session.request("HEAD", self.fixture[name] + "/bytes?n=4096")
                self.assertEqual(response.content, b"")
                self.assertEqual(response.headers["Content-Length"], "4096")
                for status in (204, 304, 503):
                    response = session.get(self.fixture[name] + "/status?code=" + str(status))
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.content, b"")


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
