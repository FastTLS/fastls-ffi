"""Integration tests against the real shared library and a loopback HTTP server."""
import asyncio
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from fastls import Session, AsyncSession, FastlsError

BODY = bytes(range(256)) * 2048


async def wait_event(event, timeout=2):
    deadline = time.perf_counter() + timeout
    while not event.is_set():
        if time.perf_counter() >= deadline:
            return False
        await asyncio.sleep(0.02)
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path == "/wait":
                self.server.entered.set()
                self.connection.settimeout(3)
                if self.connection.recv(1) == b"":
                    self.server.cancelled.set()
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/headers")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(dict(self.headers)).encode() if self.path == "/headers" else BODY
            if self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Length", "10")
                self.end_headers()
                self.wfile.write(b"first")
                self.wfile.flush()
                self.server.release.wait(3)
                self.wfile.write(b"last!")
                return
            if self.path == "/stream-cancel":
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b"first")
                self.wfile.flush()
                self.connection.settimeout(3)
                if self.connection.recv(1) == b"":
                    self.server.cancelled.set()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError, ConnectionError):
            pass


class Integration(unittest.TestCase):
    def setUp(self):
        # Give each case independent events and sockets, including daemon handlers.
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.release = threading.Event()
        self.server.entered = threading.Event()
        self.server.cancelled = threading.Event()
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def test_profiles_headers_and_unknown_profile(self):
        with Session(headers={"User-Agent": "session-value"}) as s:
            ids = s.list_profiles()
            self.assertIn("chrome/156/windows", ids)
            r = s.get(self.url + "/headers", headers={"user-agent": "caller-value", "sec-ch-ua": "custom"})
            headers = {k.lower(): v for k, v in r.json().items()}
            self.assertEqual(headers["user-agent"], "caller-value")
            self.assertEqual(headers["sec-ch-ua"], "custom")
            self.assertEqual(r.match["profile_id"], "chrome/156/windows")
            with self.assertRaises(FastlsError):
                s.get(self.url, profile="unknown/version/platform")
            self.assertEqual(len(s._handles), 0)

    def test_sync_stream_first_chunk_before_completion(self):
        with Session(timeout=2) as s:
            try:
                with s.stream("GET", self.url + "/stream") as r:
                    iterator = r.iter_bytes(5)
                    self.assertEqual(next(iterator), b"first")
                    self.assertFalse(self.server.release.is_set())
                    self.server.release.set()
                    self.assertEqual(b"".join(iterator), b"last!")
            finally:
                self.server.release.set()
            self.assertEqual(len(s._handles), 0)

    def test_binary_upload_download_and_redirects(self):
        with Session() as s:
            self.assertEqual(s.get(self.url).content, BODY)
            data = b"\x00\xff\x80a\x00"
            self.assertEqual(s.post(self.url, data=data).content, data)
            self.assertEqual(s.get(self.url + "/redirect", allow_redirects=False).status_code, 302)
            self.assertEqual(s.get(self.url + "/redirect").status_code, 200)

    def test_timeout_and_session_close(self):
        s = Session(timeout=0.15)
        with self.assertRaisesRegex(FastlsError, "deadline"):
            s.get(self.url + "/wait")
        self.assertTrue(self.server.cancelled.wait(2))
        self.assertEqual(len(s._handles), 0)
        with s.stream("GET", self.url) as r:
            s.close()
            with self.assertRaises(FastlsError):
                next(r.iter_bytes())
        s.close()
        with self.assertRaisesRegex(FastlsError, "closed"):
            s.get(self.url)

    def test_abi_invalid_handles_and_buffers(self):
        with Session() as s:
            native = s._native
            with self.assertRaises(FastlsError):
                native.call("fastls_info", 0)
            native.call("fastls_close", 0)
            native.call("fastls_close", 0)
            length = ctypes.c_size_t(123)
            code = native.lib.fastls_read(0, None, 4, ctypes.byref(length))
            self.assertEqual(code, -2)
            self.assertEqual(length.value, 0)

    def test_async_requests_keep_loop_responsive_and_cancel(self):
        async def scenario():
            async with AsyncSession(timeout=2) as s:
                count = 0
                stop = False

                async def ticker():
                    nonlocal count
                    while not stop:
                        count += 1
                        await asyncio.sleep(0.02)

                ticker_task = asyncio.create_task(ticker())
                try:
                    async with s.stream("GET", self.url + "/stream") as r:
                        iterator = r.aiter_bytes(5)
                        self.assertEqual(await anext(iterator), b"first")
                        pending = asyncio.create_task(anext(iterator))
                        await asyncio.sleep(0.08)
                        self.assertFalse(pending.done())
                        self.assertGreater(count, 1)
                        self.server.release.set()
                        self.assertEqual(await pending, b"last!")
                        await iterator.aclose()
                    responses = await asyncio.gather(*(s.get(self.url) for _ in range(8)))
                    self.assertTrue(all(r.content == BODY for r in responses))
                    self.assertEqual(len(s._handles), 0)
                    task = asyncio.create_task(s.get(self.url + "/wait"))
                    self.assertTrue(await wait_event(self.server.entered))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(await wait_event(self.server.cancelled))
                    self.assertEqual(len(s._handles), 0)
                finally:
                    self.server.release.set()
                    stop = True
                    await ticker_task

        asyncio.run(scenario())

    def test_async_cancel_during_body(self):
        async def scenario():
            async with AsyncSession() as s:
                async with s.stream("GET", self.url + "/stream-cancel") as r:
                    iterator = r.aiter_bytes(5)
                    self.assertEqual(await anext(iterator), b"first")
                    task = asyncio.create_task(anext(iterator))
                    await asyncio.sleep(0.03)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertEqual(len(s._handles), 0)
                    self.assertTrue(await wait_event(self.server.cancelled))
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
