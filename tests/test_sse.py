"""SSE wire vectors, every-byte splits, and real H1/H2 library lifetimes."""
import asyncio
from dataclasses import FrozenInstanceError
import hashlib
import json
import time
import unittest
import uuid

from test_stability import FixtureCase, EvidenceResult
from fastls import FastlsError, FastlsTimeoutError, FastlsDecodeError, ServerSentEvent, Timeout
from fastls.sse import _Decoder, MAX_RETRY_MS


class SSE(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "sse-" + cls.metrics["label"]

    def decode(self, parts, maximum=1024*1024):
        decoder = _Decoder(maximum)
        events = []
        for part in parts:
            events.extend(decoder.feed(part))
        decoder.finish()
        return events, decoder

    def test_parser_every_split_and_single_bytes(self):
        wire = "\ufeff: ping\r\nid: 7\r\n\r\nevent: change\rdata: 中文😀\rdata: second\r\rdata\n\nid:\ndata:  tail\n\ndata: unfinished".encode()
        expected = [ServerSentEvent("中文😀\nsecond", "change", "7"), ServerSentEvent("", id="7"), ServerSentEvent(" tail")]
        for split in range(len(wire)+1):
            with self.subTest(split=split):
                events, decoder = self.decode([wire[:split], b"", wire[split:]])
                self.assertEqual(events, expected)
                self.assertEqual(decoder.last_event_id, "")
        self.assertEqual(self.decode([bytes([b]) for b in wire])[0], expected)

    def test_parser_endings_spaces_and_unknown_fields(self):
        cases = [
            (b"data:x\n\n", [ServerSentEvent("x")]),
            (b"data:x\r\r", [ServerSentEvent("x")]),
            (b"data:x\r\n\r\n", [ServerSentEvent("x")]),
            (b"data\ndata\n\n", [ServerSentEvent("\n")]),
            (b"data:  x\ndata:\tx\n\n", [ServerSentEvent(" x\n\tx")]),
            (b"Data:ignored\nunknown:yes\n:ping\n\n", []),
            (b"event: ignored\n\ndata:one\n\n", [ServerSentEvent("one")]),
            (b"data:a\n\ndata:b\n", [ServerSentEvent("a")]),
            (b"data:a\n\ndata:b", [ServerSentEvent("a")]),
        ]
        for wire, expected in cases:
            for width in (1, 2, 7, 65536):
                with self.subTest(wire=wire, width=width):
                    self.assertEqual(self.decode([wire[i:i+width] for i in range(0, len(wire), width)])[0], expected)

    def test_parser_utf8_replacement_and_only_initial_bom(self):
        wire = b"\xef\xbb\xbfdata: \xff\xed\xa0\x80\n\ndata: \xef\xbb\xbfend\n\n\xef\xbb\xbfdata: ignored\n\n"
        self.assertEqual(self.decode([bytes([b]) for b in wire])[0], [ServerSentEvent("\ufffd"*4), ServerSentEvent("\ufeffend")])

    def test_parser_id_control_blocks_null_and_incomplete_eof(self):
        decoder = _Decoder(1024)
        self.assertEqual(list(decoder.feed(b"id: one\n\n")), [])
        self.assertEqual(decoder.last_event_id, "one")
        events = list(decoder.feed(b"id: bad\0id\ndata:first\n\nid\n\ndata:second\n\nid: discarded\n"))
        self.assertEqual(events, [ServerSentEvent("first", id="one"), ServerSentEvent("second")])
        decoder.finish()
        self.assertEqual(decoder.last_event_id, "")

    def test_parser_retry_digits_control_and_clamp(self):
        wire = b"retry: 0015\n\nretry: +3\nretry: -2\nretry:\nretry: \xd9\xa2\ndata:one\n\nretry: 0\ndata:two\n\nretry: " + b"9"*5000 + b"\ndata:three\n\n"
        events, decoder = self.decode([wire])
        self.assertEqual(events, [ServerSentEvent("one"), ServerSentEvent("two", retry=0), ServerSentEvent("three", retry=MAX_RETRY_MS)])
        self.assertEqual(decoder.retry, MAX_RETRY_MS)
        zeroes = b"retry: " + b"0"*5000 + b"9\n\ndata:last\n\n"
        self.assertEqual(self.decode([zeroes])[1].retry, 9)

    def test_parser_line_and_multiline_size_boundaries(self):
        self.assertEqual(self.decode([b":"+b"x"*15+b"\n\n"], maximum=16)[0], [])
        with self.assertRaisesRegex(FastlsError, "line"):
            self.decode([b":"+b"x"*16], maximum=16)
        exact = b"data:" + b"a"*20 + b"\ndata:" + b"b"*11 + b"\n\n"
        self.assertEqual(self.decode([exact], maximum=32)[0][0].data, "a"*20+"\n"+"b"*11)
        with self.assertRaisesRegex(FastlsError, "event"):
            self.decode([exact.replace(b"b"*11, b"b"*12)], maximum=32)
        # A long series of discarded comments must not accumulate an event.
        decoder = _Decoder(32)
        for _ in range(1000): self.assertEqual(list(decoder.feed(b": ping\n")), [])
        self.assertEqual(decoder._size, 0)

    def test_parser_does_not_parse_ahead_of_consumer(self):
        decoder = _Decoder(16)
        pending = decoder.feed(b"data:first\n\n" + b"x"*17)
        self.assertEqual(next(pending), ServerSentEvent("first"))
        with self.assertRaises(FastlsError): next(pending)

    def test_event_json_and_immutability(self):
        event = ServerSentEvent('{"value":1}')
        self.assertEqual(event.json(), {"value": 1})
        with self.assertRaises(FrozenInstanceError): event.data = "changed"
        with self.assertRaises(json.JSONDecodeError): ServerSentEvent("[DONE]").json()

    def expected(self):
        return [ServerSentEvent("第一条\nsecond", "update", "41", 30), ServerSentEvent("", id="41"), ServerSentEvent(" final"), ServerSentEvent('{"ok":true}'), ServerSentEvent("[DONE]")]

    def test_sync_real_h1_h2_fragmented_vectors(self):
        for protocol in ("https1", "https2"):
            with self.subTest(protocol=protocol), self.session() as session:
                with session.sse("GET", self.fixture[protocol]+"/sse-vectors?fragment=1", last_event_id="resume") as events:
                    self.assertEqual(list(events), self.expected())
                    self.assertEqual(events.last_event_id, "")
                    self.assertEqual(events.retry, 30)
                    self.assertEqual(events.response.headers["X-Test-Accept"], "text/event-stream")
                    self.assertEqual(events.response.headers["X-Test-Last-Event-ID"], "resume")

    def test_async_real_h2_vectors_and_loop_progress(self):
        async def run():
            async with self.session(async_=True) as session:
                async with session.sse("GET", self.fixture["https2"]+"/sse-vectors?fragment=1") as events:
                    self.assertEqual([event async for event in events], self.expected())
                    self.assertEqual(events.retry, 30)
                ticks, done = 0, False
                async def ticker():
                    nonlocal ticks
                    while not done:
                        ticks += 1
                        await asyncio.sleep(0)
                async with session.sse("GET", self.fixture["https2"]+"/sse-many?n=2000&size=1") as events:
                    ticking = asyncio.create_task(ticker())
                    try:
                        iterator = aiter(events)
                        await anext(iterator)
                        first_ticks = ticks
                        self.assertEqual(len([event async for event in iterator]), 1999)
                        self.assertGreater(ticks-first_ticks, 1000)
                    finally:
                        done = True
                        await ticking
        asyncio.run(run())

    def test_gzip_decoding_and_raw_mode_rejection(self):
        with self.session() as session:
            with session.sse("GET", self.fixture["https2"]+"/sse-vectors?gzip=1") as events:
                self.assertTrue(events.response.decoded)
                self.assertEqual(list(events), self.expected())
            with self.assertRaisesRegex(FastlsError, "decoded"):
                with session.sse("GET", self.fixture["https2"]+"/sse-vectors?gzip=1", decode_content=False): pass

    def test_content_type_status_and_204(self):
        with self.session() as session:
            for path in ("/sse-bad?type=application/json", "/sse-bad?type=text/event-streaming", "/sse-bad?duplicate=1&type=text/event-stream", "/status?code=503"):
                with self.subTest(path=path), self.assertRaises(FastlsError):
                    with session.sse("GET", self.fixture["https2"]+path): pass
            with session.sse("GET", self.fixture["https2"]+"/status?code=204") as events:
                self.assertEqual(list(events), [])
                self.assertEqual(events.response.status_code, 204)

    def test_post_headers_and_manual_resume(self):
        with self.session(headers={"Accept": "application/json"}) as session:
            with session.sse("POST", self.fixture["https2"]+"/sse-post", data="payload", last_event_id="41") as events:
                result = next(iter(events))
                self.assertEqual(result.json(), dict(body="payload", accept="text/event-stream", last_id="41"))
                self.assertEqual(result.id, "41")
            with session.sse("POST", self.fixture["https2"]+"/sse-post", headers={"Accept": "custom"}, data="second") as events:
                self.assertEqual(next(iter(events)).json()["accept"], "custom")

    def test_first_event_and_cancel_preserve_active_h2_sibling(self):
        async def run():
            id = uuid.uuid4().hex
            async with self.session(async_=True) as session:
                async with session.sse("GET", self.fixture["https2"]+"/sse-hold?id="+id) as events:
                    iterator = aiter(events)
                    self.assertEqual((await anext(iterator)).data, "first")
                    self.assertFalse(self.state(id)["done"])
                    async with session.stream("GET", self.fixture["https2"]+"/drip") as sibling:
                        parts = sibling.aiter_bytes(5)
                        self.assertEqual(await anext(parts), b"abcde")
                        self.assertEqual(sibling.headers["X-Test-Connection"], events.response.headers["X-Test-Connection"])
                        pending = asyncio.create_task(anext(iterator))
                        await asyncio.sleep(0.03)
                        pending.cancel()
                        with self.assertRaises(asyncio.CancelledError): await pending
                        self.assertEqual(b"".join([part async for part in parts]), b"abcde"*5)
                await self.wait_state(id, "done")
                self.assertTrue(self.state(id)["cancelled"])
        asyncio.run(run())

    def test_sync_early_break_closes_network(self):
        id = uuid.uuid4().hex
        with self.session() as session:
            with session.sse("GET", self.fixture["https2"]+"/sse-hold?id="+id) as events:
                for event in events:
                    self.assertEqual(event.data, "first")
                    break
        asyncio.run(self.wait_state(id, "done"))
        self.assertTrue(self.state(id)["cancelled"])

    def test_async_session_close_unblocks_event_reader(self):
        async def run():
            session = self.session(async_=True)
            async with session.sse("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex) as events:
                iterator = aiter(events)
                await anext(iterator)
                pending = asyncio.create_task(anext(iterator))
                await asyncio.sleep(0.03)
                await session.aclose()
                with self.assertRaises(FastlsError): await pending
        asyncio.run(run())

    def test_read_timeout_and_heartbeat_total_deadline(self):
        async def run():
            async with self.session(async_=True) as session:
                for heartbeat, expected in ((False, "read"), (True, "total")):
                    timeout = Timeout(total=0.4 if heartbeat else 3, read=0.2)
                    with self.subTest(heartbeat=heartbeat), self.assertRaises(FastlsTimeoutError) as caught:
                        async with session.sse("GET", self.fixture["https2"]+"/sse-hold?id="+uuid.uuid4().hex+("&heartbeat=1" if heartbeat else ""), timeout=timeout) as events:
                            async for _ in events: pass
                    self.assertEqual(caught.exception.phase, expected)
        asyncio.run(run())

    def test_large_stream_backpressure_and_no_buffered_body_cap(self):
        count, size = 640, 65536
        with self.session(timeout=60) as session:
            id = uuid.uuid4().hex
            digest, seen, peak = hashlib.sha256(), 0, 0
            with session.sse("GET", self.fixture["https2"]+f"/sse-many?n={count}&size={size}&id={id}") as events:
                time.sleep(0.2)
                self.assertLess(self.state(id)["bytes"], count*(size+8))
                self.assertLessEqual(session.stats()["buffered_body_bytes"], 3*65536)
                for event in events:
                    self.assertEqual(len(event.data), size)
                    digest.update(event.data.encode())
                    seen += 1
                    peak = max(peak, session.stats()["buffered_body_bytes"])
            expected = hashlib.sha256()
            for _ in range(count): expected.update(b"x"*size)
            self.assertEqual(seen, count)
            self.assertEqual(digest.hexdigest(), expected.hexdigest())
            self.assertLessEqual(peak, 3*65536)
            self.metrics["cases"]["large_sse"] = dict(bytes=count*size, events=seen, sha256=digest.hexdigest(), peak_download=peak)

    def test_invalid_options_fail_before_native_allocation(self):
        with self.session() as session:
            for value in (True, 0, -1, 1.5, 64*1024*1024+1):
                with self.assertRaises(ValueError):
                    with session.sse("GET", self.fixture["https2"], max_event_bytes=value): pass
            for value in ("bad\0id", "bad\r\nid", 3):
                with self.assertRaises((ValueError, TypeError)):
                    with session.sse("GET", self.fixture["https2"], last_event_id=value): pass
            self.assertEqual(session.stats()["open_requests"], 0)

    def test_single_consumer_close_and_buffered_events(self):
        with self.session() as session:
            with session.sse("GET", self.fixture["https2"]+"/sse-vectors") as events:
                iterator = iter(events)
                next(iterator)
                with self.assertRaisesRegex(FastlsError, "one consumer"): iter(events)
                events.close()
                with self.assertRaises(FastlsError): next(iterator)
        async def run():
            async with self.session(async_=True) as session:
                async with session.sse("GET", self.fixture["https2"]+"/sse-vectors") as events:
                    with self.assertRaises(FastlsError): iter(events)
                    iterator = aiter(events)
                    await anext(iterator)
                    with self.assertRaises(FastlsError): aiter(events)
                    events.cancel()
                    with self.assertRaises(FastlsError): await anext(iterator)
        asyncio.run(run())

    def test_async_sse_over_duplex_upload(self):
        async def run():
            ready, ack = asyncio.Event(), asyncio.Event()
            async def source():
                await ready.wait()
                yield b"alpha"
                await ack.wait()
                yield b"beta"
            async with self.session(async_=True) as session:
                async with session.sse("POST", self.fixture["https2"]+"/sse-duplex", data=source(), duplex=True) as events:
                    iterator = aiter(events)
                    self.assertEqual((await anext(iterator)).data, "ready")
                    ready.set()
                    self.assertEqual((await anext(iterator)).data, "alpha")
                    ack.set()
                    self.assertEqual([event.data async for event in iterator], ["beta"])
        asyncio.run(asyncio.wait_for(run(), 10))

    def test_network_event_limit_closes_request(self):
        with self.session() as session:
            with self.assertRaisesRegex(FastlsError, "max_event_bytes"):
                with session.sse("GET", self.fixture["https2"]+"/sse-many?n=3&size=2048", max_event_bytes=1024) as events:
                    list(events)

    def test_compressed_error_cannot_become_clean_sse_eof(self):
        with self.session() as session:
            with self.assertRaises(FastlsDecodeError):
                with session.sse("GET", self.fixture["https2"]+"/sse-vectors?corrupt=1") as events:
                    list(events)
        async def run():
            async with self.session(async_=True) as session:
                with self.assertRaises(FastlsDecodeError):
                    async with session.sse("GET", self.fixture["https2"]+"/sse-vectors?corrupt=1") as events:
                        async for _ in events: pass
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
