"""Native streaming decoders and request phase deadlines against loopback fixtures."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import time
import unittest
from urllib.parse import urlencode
import uuid

from test_stability import FixtureCase, EvidenceResult, SLAB
from test_network import WireServer
from fastls import Timeout, FastlsTimeoutError, FastlsDecodeError

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "tests/fixtures/decode-vectors.json").read_text())
PAYLOAD = base64.b64decode(VECTORS["payload_base64"])
ENCODED = {name: base64.b64decode(data) for name, data in VECTORS["cases"].items()}


class DecodeTimeouts(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "decode-timeouts-" + cls.metrics["label"]

    def encoded(self, session, base, name, data=None, **kwargs):
        coding = {"raw-deflate": "deflate", "concatenated-gzip": "gzip"}.get(name, name)
        return session.post(base + "/encoded-echo?" + urlencode({"encoding": coding}), data=ENCODED[name] if data is None else data, **kwargs)

    def test_independent_codec_vectors_and_wire_headers(self):
        for protocol in ("https1", "https2"):
            with self.session() as session:
                for name in ENCODED:
                    with self.subTest(protocol=protocol, encoding=name):
                        response = self.encoded(session, self.fixture[protocol], name)
                        self.assertEqual(response.content, PAYLOAD)
                        self.assertTrue(response.decoded)
                        self.assertNotIn("Content-Encoding", response.headers)
                        self.assertNotIn("Content-Length", response.headers)
                        self.assertTrue(response.wire_headers["content-encoding"])
                        self.assertEqual(int(response.wire_headers["content-length"]), len(ENCODED[name]))

    def test_raw_mode_and_request_override_keep_exact_compressed_bytes(self):
        for protocol in ("https1", "https2"):
            with self.session(decode_content=False) as session:
                raw = self.encoded(session, self.fixture[protocol], "gzip")
                self.assertEqual(raw.content, ENCODED["gzip"])
                self.assertFalse(raw.decoded)
                self.assertEqual(raw.headers["Content-Encoding"], "gzip")
                decoded = self.encoded(session, self.fixture[protocol], "gzip", decode_content=True)
                self.assertEqual(decoded.content, PAYLOAD)
                self.assertEqual(raw.headers["X-Test-Connection"], decoded.wire_headers["X-Test-Connection"])

    def test_truncation_and_checksum_errors_are_not_eof(self):
        broken = {name: data[:-3] for name, data in ENCODED.items() if name != "gzip, br"}
        corrupted = bytearray(ENCODED["gzip"]); corrupted[-8] ^= 1
        broken["gzip-crc"] = bytes(corrupted)
        for protocol in ("https1", "https2"):
            with self.session() as session:
                for name, data in broken.items():
                    with self.subTest(protocol=protocol, encoding=name), self.assertRaises(FastlsDecodeError):
                        self.encoded(session, self.fixture[protocol], "gzip" if name == "gzip-crc" else name, data=data)
                self.assertEqual(session.get(self.fixture[protocol] + "/bytes?n=4").content, SLAB[:4])

    def test_decoded_limit_exact_boundary_and_unsupported_encoding(self):
        with self.session() as session:
            base = self.fixture["https2"]
            for name in ENCODED:
                self.assertEqual(self.encoded(session, base, name, max_decoded_bytes=len(PAYLOAD)).content, PAYLOAD)
                with self.assertRaisesRegex(FastlsDecodeError, "max_decoded_bytes"):
                    self.encoded(session, base, name, max_decoded_bytes=len(PAYLOAD) - 1)
            for coding in ("unknown", "gzip, unknown", "gzip, gzip, gzip, gzip, gzip"):
                with self.assertRaises(FastlsDecodeError):
                    session.post(base + "/encoded-echo?" + urlencode({"encoding": coding}), data=b"test")
            raw = session.post(base + "/encoded-echo?encoding=unknown", data=b"raw", decode_content=False)
            self.assertEqual(raw.content, b"raw")

    def test_compressed_stream_first_chunk_precedes_server_finish(self):
        for protocol in ("https1", "https2"):
            with self.session() as session:
                token = uuid.uuid4().hex
                with session.stream("GET", self.fixture[protocol] + "/gzip-hold?id=" + token) as response:
                    chunks = response.iter_bytes(5)
                    self.assertEqual(next(chunks), b"first")
                    self.assertFalse(self.state(token)["done"])
                    self.control("/release", token)
                    self.assertEqual(b"".join(chunks), b"last!")

    def test_64_mib_decoded_stream_hash_buffer_bound_and_buffered_cap(self):
        with self.session(timeout=90) as session:
            digest, length = hashlib.sha256(), 0
            with session.stream("GET", self.fixture["https2"] + "/gzip-stream?n=67108864") as response:
                time.sleep(0.1)
                self.assertLessEqual(session.stats()["buffered_body_bytes"], 3 * 65536)
                for chunk in response.iter_bytes():
                    digest.update(chunk); length += len(chunk)
            expected = hashlib.sha256()
            for _ in range(1024):
                expected.update(SLAB)
            self.assertEqual(length, 64 << 20)
            self.assertEqual(digest.digest(), expected.digest())
            from fastls import FastlsError
            with self.assertRaisesRegex(FastlsError, "32MiB"):
                session.get(self.fixture["https2"] + "/gzip-stream?n=34603008")

    def test_connect_timeout_during_tls_handshake(self):
        with WireServer() as peer, self.session() as session:
            with self.assertRaises(FastlsTimeoutError) as result:
                session.get("https://" + peer.address, timeout=Timeout(total=4, connect=0.3))
            self.assertEqual(result.exception.phase, "connect")
            self.assertTrue(peer.accepted.is_set())
            self.assertTrue(peer.disconnected.wait(2))

    def test_headers_timeout_h1_h2_and_per_request_policy(self):
        for protocol in ("https1", "https2"):
            with self.session(timeout=Timeout(total=5, headers=0.3)) as session:
                session.get(self.fixture[protocol] + "/bytes?n=4")
                started = time.perf_counter()
                with self.assertRaises(FastlsTimeoutError) as result:
                    session.get(self.fixture[protocol] + "/wait?id=" + uuid.uuid4().hex)
                self.assertEqual(result.exception.phase, "headers")
                self.assertLess(time.perf_counter() - started, 3)
                self.assertEqual(len(session.get(self.fixture[protocol] + "/bytes?n=4", timeout=2).content), 4)

    def test_redirect_hop_phase_resets_but_total_deadline_spans_hops(self):
        for protocol in ("https1", "https2"):
            with self.session() as session:
                url = self.fixture[protocol] + "/delay-redirect?n=2"
                self.assertEqual(session.get(url, timeout=Timeout(total=3, headers=0.25)).content, b"done")
                with self.assertRaises(FastlsTimeoutError) as result:
                    session.get(url, timeout=Timeout(total=0.25, headers=0.3))
                self.assertEqual(result.exception.phase, "total")

    def test_read_timeout_h1_h2_and_lazy_gzip_header(self):
        for protocol in ("https1", "https2"):
            with self.session(timeout=Timeout(total=5, headers=0.25, read=0.4)) as session:
                for path in ("/hold?id=" + uuid.uuid4().hex, "/encoded-wait"):
                    with session.stream("GET", self.fixture[protocol] + path) as response:
                        if path.startswith("/hold"):
                            iterator = response.iter_bytes(5)
                            self.assertEqual(next(iterator), b"first")
                        with self.assertRaises(FastlsTimeoutError) as result:
                            response.read()
                        self.assertEqual(result.exception.phase, "read")

    def test_read_timeout_resets_on_progress_total_deadline_does_not(self):
        for protocol in ("https1", "https2"):
            with self.session() as session:
                response = session.get(self.fixture[protocol] + "/drip", timeout=Timeout(total=3, read=0.25))
                self.assertEqual(response.content, b"abcde" * 6)
                with self.assertRaises(FastlsTimeoutError) as result:
                    session.get(self.fixture[protocol] + "/drip", timeout=Timeout(total=0.25, read=0.2))
                self.assertEqual(result.exception.phase, "total")

    def test_slow_consumer_does_not_spend_read_budget(self):
        with self.session(timeout=Timeout(total=5, read=0.2)) as session:
            with session.stream("GET", self.fixture["https2"] + "/bytes?n=67108864") as response:
                deadline = time.perf_counter() + 3
                while session.stats()["buffered_body_bytes"] < 2 * 65536 and time.perf_counter() < deadline:
                    time.sleep(0.02)
                time.sleep(0.5)
                self.assertEqual(next(response.iter_bytes(4)), SLAB[:4])
                response.cancel()

    def test_async_read_timeout_preserves_other_h2_stream_and_loop(self):
        async def run():
            async with self.session(async_=True, timeout=Timeout(total=5, read=0.25)) as session:
                base = self.fixture["https2"]
                warm = await session.get(base + "/bytes?n=4")
                failed = asyncio.create_task(session.get(base + "/hold?id=" + uuid.uuid4().hex))
                healthy = asyncio.create_task(session.get(base + "/drip"))
                ticks = 0
                while not failed.done():
                    ticks += 1
                    await asyncio.sleep(0.02)
                with self.assertRaises(FastlsTimeoutError) as result:
                    await failed
                self.assertEqual(result.exception.phase, "read")
                self.assertGreater(ticks, 2)
                response = await healthy
                self.assertEqual(response.content, b"abcde" * 6)
                self.assertEqual(response.headers["X-Test-Connection"], warm.headers["X-Test-Connection"])
        asyncio.run(run())

    def test_head_and_identity_are_not_decoded(self):
        with self.session() as session:
            base = self.fixture["https2"]
            response = session.request("HEAD", base + "/encoded-echo?encoding=gzip")
            self.assertEqual(response.content, b"")
            self.assertFalse(response.decoded)
            identity = session.post(base + "/encoded-echo?encoding=identity", data=b"plain")
            self.assertEqual(identity.content, b"plain")
            self.assertFalse(identity.decoded)

    def test_invalid_policy_and_decoded_limit_fail_before_native_registration(self):
        for value in (0, -1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Timeout(read=value)
        with self.session() as session:
            for maximum in (0, -1, True, 1 << 41):
                with self.assertRaises(ValueError):
                    session.get(self.fixture["https2"], max_decoded_bytes=maximum)
            self.assertEqual(session.stats()["open_requests"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
