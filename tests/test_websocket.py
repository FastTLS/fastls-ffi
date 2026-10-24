"""WebSocket acceptance against an independently written RFC6455 wire server."""
import asyncio
import base64
import hashlib
import json
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from test_stability import FixtureCase, EvidenceResult, SLAB, resources
from fastls import FastlsError, FastlsTimeoutError, WebSocketClosed
from proxy_lab import ProxyLab


class WebSockets(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "websocket-" + cls.metrics["label"]

    def url(self, key="https2", mode=""):
        return self.fixture[key].replace("http", "ws", 1) + "/ws" + ("?mode=" + mode if mode else "")

    def test_text_binary_empty_and_fragmented_client_frames(self):
        observed = []
        with self.session() as s:
            for key in ("http", "https1", "https2"):
                with s.websocket(self.url(key), max_message_bytes=2*1024*1024) as ws:
                    for body in ("", "你好🌍", b"", b"\x00\x80\xff", SLAB*16):
                        ws.send(body)
                        self.assertEqual(ws.recv(), body)
                    self.assertEqual(ws.headers["X-WS-Protocol"], "HTTP/1.1")
                    observed.append(key)
                self.assertEqual(ws.close_code, 1000)
        self.metrics["cases"]["protocols"] = observed

    def test_profile_headers_query_subprotocol_and_alpn(self):
        with self.session(headers={"User-Agent": "custom-ws", "X-Caller": "preserve"}) as s:
            with s.websocket(self.url("https1", "inspect"), params={"tag": ["中文", "two"]}, subprotocols=["chat.v1", "chat.v2"]) as ws:
                ws.send("inspect")
                info = json.loads(ws.recv())
                self.assertEqual(ws.subprotocol, "chat.v1")
                self.assertEqual(info["headers"]["User-Agent"], ["custom-ws"])
                self.assertEqual(info["headers"]["X-Caller"], ["preserve"])
                self.assertEqual(info["query"]["tag"], ["中文", "two"])
                self.assertEqual(info["protocol"], "HTTP/1.1")
                self.assertEqual(info["alpn"], "http/1.1")
                self.assertNotIn("Sec-Websocket-Extensions", info["headers"])

    def test_incoming_fragmented_utf8_interleaved_ping_and_auto_pong(self):
        with self.session() as s, s.websocket(self.url(mode="fragment")) as ws:
            self.assertEqual(ws.recv(), "你好🌍")
            self.assertEqual(json.loads(ws.recv()), {"pong": "server-ping"})
            self.assertEqual(ws.info["websocket"]["pings"], 1)
            ws.send("still healthy")
            self.assertEqual(ws.recv(), "still healthy")

    def test_ping_pong_and_explicit_pong(self):
        with self.session() as s, s.websocket(self.url()) as ws:
            ws.ping(b"binary\x00\xff")
            deadline = time.monotonic() + 3
            while not ws.info["websocket"]["pongs"]:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.005)
            self.assertEqual(base64.b64decode(ws.info["websocket"]["last_pong_base64"]), b"binary\x00\xff")
            ws.pong(b"unsolicited")
            ws.send("ok")
            self.assertEqual(ws.recv(), "ok")

    def test_neutral_profiles_and_one_byte_limit(self):
        with self.session() as s:
            for profile in ("library/fastls/2/any/portable-h1", "library/fastls/2/any/portable-h2"):
                with s.websocket(self.url("https1"), profile=profile, max_message_bytes=1) as ws:
                    ws.send("x")
                    self.assertEqual(ws.recv(), "x")
                    ws.ping(b"control-independent-of-message-limit")
                self.assertEqual(ws.close_code, 1000)

    def test_remote_close_code_reason_and_local_close(self):
        with self.session() as s:
            ws = s.ws_connect(self.url(mode="remote-close"))
            with self.assertRaises(WebSocketClosed) as caught:
                ws.recv()
            self.assertEqual((caught.exception.code, caught.exception.reason), (1001, "bye"))
            self.assertEqual(ws.close_code, 1001)
            ws.close()
            ws = s.ws_connect(self.url())
            ws.close(4001, "完成")
            self.assertEqual(ws.close_code, 4001)
            self.assertEqual(ws.close_reason, "完成")

    def test_invalid_frames_utf8_limits_and_abrupt_disconnect(self):
        with self.session() as s:
            for mode in ("bad-utf8", "bad-rsv", "bad-opcode", "bad-control", "masked", "bad-close-code", "bad-close-utf8", "oversize", "abrupt"):
                with self.subTest(mode=mode), self.assertRaises(FastlsError):
                    with s.websocket(self.url(mode=mode), max_message_bytes=1024) as ws:
                        ws.recv()
                self.assertEqual(s.stats()["open_requests"], 0)
            with s.websocket(self.url()) as ws:
                ws.send("recovered")
                self.assertEqual(ws.recv(), "recovered")

    def test_handshake_reject_redirect_bad_accept_and_unoffered_compression(self):
        with self.session() as s:
            for mode in ("reject", "redirect", "bad-accept", "compression", "hidden-compression", "bad-subprotocol", "huge-headers"):
                with self.subTest(mode=mode), self.assertRaises(FastlsError):
                    s.ws_connect(self.url(mode=mode))
                self.assertEqual(s.stats()["open_requests"], 0)

    def test_wss_trust_and_hostname_validation(self):
        from fastls import Session
        for ca in (None, self.fixture["wrong_ca_file"]):
            with Session(ca_file=ca) as s, self.assertRaises(FastlsError):
                s.ws_connect(self.url("https1"))
        with Session(ca_file=self.fixture["proxy_wrong_host_ca_file"]) as s, self.assertRaises(FastlsError):
            s.ws_connect(self.fixture["ws_wrong_host"].replace("http","ws",1)+"/ws")

    def test_write_timeout_with_nonreading_peer(self):
        with self.session() as s:
            with s.websocket(self.url(mode="slow-read"), max_message_bytes=16*1024*1024, write_timeout=.15) as ws:
                with self.assertRaises(FastlsTimeoutError) as caught:
                    ws.send(b"x"*(16*1024*1024))
                self.assertEqual(caught.exception.phase, "write")

    def test_async_cancel_blocked_send_and_single_writer(self):
        async def run():
            async with self.session(async_=True) as s:
                ws = await s.ws_connect(self.url(mode="slow-read"), max_message_bytes=16*1024*1024)
                task = asyncio.create_task(ws.send(b"x"*(16*1024*1024)))
                for _ in range(100):
                    if ws.info["websocket"]["write_pending"]: break
                    await asyncio.sleep(.01)
                self.assertFalse(task.done())
                self.assertTrue(ws.info["websocket"]["write_pending"])
                self.assertLessEqual(s.stats()["buffered_ws_write_bytes"], 16*1024*1024)
                with self.assertRaisesRegex(FastlsError,"send is active"): await ws.send("second")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertEqual(s.stats()["open_requests"],0)
        asyncio.run(run())

    def test_handshake_and_read_timeouts_and_close_budget(self):
        with self.session() as s:
            with self.assertRaises(FastlsTimeoutError) as caught:
                s.ws_connect(self.url(mode="handshake-stall"), handshake_timeout=.15)
            self.assertEqual(caught.exception.phase, "handshake")
            with s.websocket(self.url(), read_timeout=.15) as ws:
                with self.assertRaises(FastlsTimeoutError) as caught:
                    ws.recv()
                self.assertEqual(caught.exception.phase, "read")
            ws = s.ws_connect(self.url(mode="close-stall"), close_timeout=.15)
            before = time.monotonic()
            ws.close()
            self.assertLess(time.monotonic()-before, 1.5)

    def test_invalid_parameters_and_sends_keep_connection_usable(self):
        with self.session() as s:
            for options in (dict(max_message_bytes=0), dict(max_message_bytes=True), dict(max_message_bytes=16777217), dict(read_timeout=-1),
                            dict(handshake_timeout=float("nan")), dict(subprotocols="chat"), dict(subprotocols=["bad protocol"]),
                            dict(headers={"Sec-WebSocket-Key": "override"}), dict(headers={"Connection": "close"}),
                            dict(headers={"X-Test": "\r\n"}), dict(proxy="socks4://127.0.0.1:1")):
                with self.subTest(options=options), self.assertRaises((FastlsError, ValueError, TypeError)):
                    s.ws_connect(self.url(), **options)
                self.assertEqual(s.stats()["open_requests"], 0)
            for url in ("https://127.0.0.1/ws", self.url()+"#fragment", "ws://user:secret@localhost/ws"):
                with self.assertRaises(FastlsError): s.ws_connect(url)
            with s.websocket(self.url(), max_message_bytes=32) as ws:
                for message in (object(), "x"*33, b"x"*33):
                    with self.assertRaises((TypeError, ValueError)): ws.send(message)
                with self.assertRaises(ValueError): ws.ping(b"x"*126)
                with self.assertRaises(ValueError): ws.close(1005)
                ws.send("valid")
                self.assertEqual(ws.recv(), "valid")

    def test_single_reader_and_close_from_other_thread(self):
        with self.session() as s:
            ws = s.ws_connect(self.url())
            with ThreadPoolExecutor(max_workers=1) as pool:
                reader = pool.submit(ws.recv)
                deadline = time.monotonic()+3
                while not ws._reader.locked():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.005)
                with self.assertRaisesRegex(FastlsError, "recv is active"): ws.recv()
                ws.abort()
                with self.assertRaises(FastlsError): reader.result(timeout=3)

    def test_slow_consumer_has_bounded_native_queue(self):
        with self.session() as s:
            ws = s.ws_connect(self.url(mode="flood"), max_message_bytes=65536)
            time.sleep(.1)
            snapshot = s.stats()
            self.assertLessEqual(snapshot["buffered_body_bytes"], 2*65536)
            for _ in range(4): self.assertEqual(len(ws.recv()), 65536)
            ws.abort()
            self.metrics["cases"]["slow_consumer"] = snapshot

    def test_all_tunnels_authentication_and_no_proxy_credentials_at_target(self):
        for scheme in ("http", "https", "socks5", "socks5h"):
            options = dict(certfile=self.fixture["proxy_cert_file"],keyfile=self.fixture["proxy_key_file"]) if scheme=="https" else {}
            with ProxyLab(scheme, auth=True, **options) as p:
                with self.session(proxy=p.url, proxy_ca_file=self.fixture["proxy_ca_file"]) as s:
                    for key in ("http", "https2"):
                        with s.websocket(self.url(key, "inspect")) as ws:
                            ws.send(b"payload")
                            headers = json.loads(ws.recv())["headers"]
                            self.assertNotIn("Proxy-Authorization", headers)
                        with s.websocket(self.url(key), proxy=None) as ws:
                            ws.send("direct")
                            self.assertEqual(ws.recv(), "direct")
                p.wait_idle()

    def test_async_messages_and_default_notification_idle(self):
        async def run():
            async with self.session(async_=True, notifications="required") as s:
                async with s.websocket(self.url(), subprotocols=["chat"]) as ws:
                    before = threading.active_count()
                    task = asyncio.create_task(ws.recv())
                    await asyncio.sleep(.1)
                    self.assertEqual(threading.active_count(), before)
                    self.assertFalse(task.done())
                    self.assertEqual(s.notification_mode, "async-events")
                    await ws.send("first")
                    self.assertEqual(await task, "first")
                    for body in ("", b"", "中文🌍", SLAB*4):
                        await ws.send(body)
                        self.assertEqual(await ws.recv(), body)
                    await ws.ping(b"async")
                    await ws.pong(b"pong")
                self.assertEqual(ws.close_code, 1000)
        asyncio.run(run())

    def test_async_cancel_recv_preserves_sibling_and_http(self):
        async def run():
            async with self.session(async_=True) as s:
                async with s.websocket(self.url()) as first, s.websocket(self.url()) as second:
                    task = asyncio.create_task(first.recv())
                    await asyncio.sleep(.03)
                    with self.assertRaisesRegex(FastlsError, "recv is active"): await first.recv()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                    await second.send("sibling")
                    self.assertEqual(await second.recv(), "sibling")
                    self.assertEqual((await s.get(self.fixture["https2"]+"/status?code=204")).status_code, 204)
        asyncio.run(run())

    def test_async_handshake_cancel_session_close_and_poll_fallback(self):
        async def run():
            async with self.session(async_=True) as s:
                task = asyncio.create_task(s.ws_connect(self.url(mode="handshake-stall")))
                await asyncio.sleep(.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                ws = await s.ws_connect(self.url())
                task = asyncio.create_task(ws.recv())
                await asyncio.sleep(.03)
                await s.aclose()
                with self.assertRaises(FastlsError): await task
            async with self.session(async_=True, notifications="poll") as s:
                async with s.websocket(self.url(mode="fragment")) as ws:
                    self.assertEqual(await ws.recv(), "你好🌍")
                    self.assertEqual(json.loads(await ws.recv()), {"pong": "server-ping"})
        asyncio.run(run())

    def test_async_close_timeout_and_read_failure(self):
        async def run():
            async with self.session(async_=True) as s:
                ws = await s.ws_connect(self.url(mode="close-stall"), close_timeout=.15)
                start = time.monotonic()
                await ws.aclose()
                self.assertLess(time.monotonic()-start, 1.5)
                async with s.websocket(self.url(), read_timeout=.15) as ws:
                    with self.assertRaises(FastlsTimeoutError): await ws.recv()
                for mode in ("bad-utf8", "bad-opcode", "oversize"):
                    with self.assertRaises(FastlsError):
                        async with s.websocket(self.url(mode=mode), max_message_bytes=1024) as ws:
                            await ws.recv()
        asyncio.run(run())

    def test_repeated_connections_cleanup(self):
        baseline = self.idle()
        with self.session() as s:
            for _ in range(25):
                with s.websocket(self.url()) as ws:
                    ws.send("round")
                    self.assertEqual(ws.recv(), "round")
        final = self.idle()
        self.assertEqual(final["buffered_ws_write_bytes"], 0)
        self.assertLessEqual(final["goroutines"], baseline["goroutines"]+2)
        self.metrics["cases"]["recovery"] = dict(baseline=baseline, final=final, connections=25)


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
