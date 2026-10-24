"""N3 acceptance against an independent loopback H1/H2 multipart parser."""
import asyncio
import base64
import hashlib
import io
import math
import time
import unittest
from unittest.mock import patch

from test_stability import FixtureCase, EvidenceResult, SLAB, resources
from fastls import FastlsHTTPError


class FileSource:
    """Virtual binary file: bounded reads, no seek/length and no full allocation."""
    name = "large.bin"

    def __init__(self, size, fail=False, sample_memory=False):
        self.left, self.size, self.fail = size, size, fail
        self.closed, self.reads, self.maximum = False, 0, 0
        self.sample_memory = sample_memory
        self.peak_rss = resources()["rss_bytes"] if sample_memory else None

    def read(self, size):
        if not 0 < size <= 65536:
            raise AssertionError("unbounded multipart file read")
        self.maximum = max(self.maximum, size)
        self.reads += 1
        if self.sample_memory and self.reads % 32 == 0:
            measured = resources()["rss_bytes"]
            if measured is not None:
                self.peak_rss = max(self.peak_rss or 0, measured)
        if self.fail and self.reads == 3:
            raise OSError("fixture file read failed")
        take = min(size, self.left)
        self.left -= take
        return SLAB[:take]

    def close(self):
        self.closed = True


class RequestOptions(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "request-options-" + cls.metrics["label"]

    def body(self, response):
        return base64.b64decode(response.json()["body"] or "")

    def test_query_preserves_existing_encoding_repeated_keys_and_unicode(self):
        with self.session() as s:
            for key in ("http", "https1", "https2"):
                r = s.get(self.fixture[key] + "/inspect?old=a%20b&tag=old#fragment",
                          params=[("tag", ["中文", "a b"]), ("none", None), ("empty", ""), ("yes", True)])
                info = r.json()
                self.assertEqual(info["query"], {"old": ["a b"], "tag": ["old", "中文", "a b"], "empty": [""], "yes": ["True"]})
                self.assertTrue(info["raw_query"].startswith("old=a%20b&tag=old&"))
                self.assertNotIn("fragment", info["raw_query"])
                self.assertEqual(s.get(self.fixture[key] + "/inspect", params="x=a%2Bb&x=2").json()["query"], {"x": ["a+b", "2"]})

    def test_json_null_empty_values_and_explicit_content_type(self):
        with self.session(headers={"CONTENT-TYPE": "application/custom+json"}) as s:
            for payload, expected in ((None, b"null"), ({}, b"{}"), ([], b"[]"), ("", b'""'), ({"中文": True}, '{"中文":true}'.encode())):
                r = s.post(self.fixture["https2"] + "/inspect", json=payload)
                self.assertEqual(self.body(r), expected)
                self.assertEqual(r.json()["headers"]["Content-Type"], ["application/custom+json"])
            r = s.post(self.fixture["https1"] + "/inspect", json=False, headers={"content-type": "application/json"})
            self.assertEqual(self.body(r), b"false")
            self.assertEqual(r.json()["headers"]["Content-Type"], ["application/json"])
        with self.session() as s:
            self.assertEqual(s.post(self.fixture["http"] + "/inspect", json=0).json()["headers"]["Content-Type"], ["application/json"])
            self.assertEqual(self.body(s.post(self.fixture["http"] + "/inspect")), b"")

    def test_form_encoding_and_raw_body_compatibility(self):
        with self.session() as s:
            for key in ("https1", "https2"):
                r = s.post(self.fixture[key] + "/inspect", data=[("tag", ["中文", "a b"]), ("nil", None), ("empty", "")])
                self.assertEqual(self.body(r), b"tag=%E4%B8%AD%E6%96%87&tag=a+b&empty=")
                self.assertEqual(r.json()["headers"]["Content-Type"], ["application/x-www-form-urlencoded"])
                for body in (b"raw", "raw", [b"ra", b"w"], iter([b"raw"])):
                    self.assertEqual(self.body(s.post(self.fixture[key] + "/inspect", data=body)), b"raw")
                self.assertEqual(self.body(s.post(self.fixture[key] + "/inspect", data=[])), b"")

    def test_invalid_options_fail_without_native_handle_or_file_read(self):
        source = FileSource(1024)
        cases = [dict(json={}, data=b""), dict(json=None, files={}), dict(json=math.nan),
                 dict(json=object()), dict(params=123), dict(params="x=#fragment"), dict(params="中文"),
                 dict(data={"x": object()}), dict(params={1: "x"}),
                 dict(files={"file": ("bad\r\nname", source)}), dict(files={"file": ("a", iter([b"x"]))}),
                 dict(files={"file": source}, headers={"CONTENT-TYPE": "multipart/form-data"}),
                 dict(files={"file": source}, data=b"raw"), dict(files={"file": source}, duplex=True)]
        for header in ("Content-Length", "Transfer-Encoding"):
            for option in (dict(json={}), dict(data={}), dict(files={"file": source})):
                cases.append(dict(option, headers={header: "0"}))
        with self.session() as s:
            for options in cases:
                with self.subTest(options=list(options)), self.assertRaises((ValueError, TypeError)):
                    s.post(self.fixture["https2"] + "/inspect", **options)
                self.assertEqual(s.stats()["open_requests"], 0)
        self.assertEqual(source.reads, 0)
        self.assertFalse(source.closed)

    def test_multipart_fields_repeated_files_unicode_and_empty_body(self):
        with self.session() as s:
            for key in ("http", "https1", "https2"):
                owned = io.BytesIO(b"skip-content")
                owned.seek(5)
                result = s.post(self.fixture[key] + "/multipart", data=[("tag", ["中文", ""])],
                                files=[("file", ("附件.bin", owned)), ("file", ("empty.txt", b"", "text/plain"))]).json()
                parts = result["parts"]
                self.assertEqual([p["name"] for p in parts], ["tag", "tag", "file", "file"])
                self.assertEqual([p["bytes"] for p in parts], [6, 0, 7, 0])
                self.assertEqual(parts[2]["filename"], "附件.bin")
                self.assertEqual(parts[2]["sha256"], hashlib.sha256(b"content").hexdigest())
                self.assertEqual(parts[3]["type"], "text/plain")
                self.assertFalse(owned.closed)
                owned.close()
                self.assertEqual(s.post(self.fixture[key] + "/multipart", files={}).json()["parts"], [])

    def test_multipart_immediate_response_after_terminal_boundary(self):
        # Force headers to arrive before the producer gets another Python turn.
        # A separate EOF write incorrectly rejects this valid server response.
        def delayed(native):
            original = native.write
            def write(handle, data, end=False):
                code = original(handle, data, end=end)
                if code == 0 and data.startswith(b"--fastls-") and data.endswith(b"--\r\n"):
                    deadline = time.monotonic() + 5
                    while native.call("fastls_info", handle)["state"] == "pending":
                        if time.monotonic() > deadline:
                            raise AssertionError("server did not respond after terminal boundary")
                        time.sleep(.001)
                return code
            return write
        with self.session() as s, patch.object(s._native, "write", delayed(s._native)):
            for _ in range(10):
                self.assertEqual(s.post(self.fixture["https2"] + "/multipart", files={"file": ("a.bin", b"content")}).json()["parts"][0]["bytes"], 7)
        async def run():
            async with self.session(async_=True) as s:
                with patch.object(s._native, "write", delayed(s._native)):
                    for _ in range(10):
                        r = await s.post(self.fixture["https2"] + "/multipart", files={"file": ("a.bin", b"content")})
                        self.assertEqual(r.json()["parts"][0]["bytes"], 7)
        asyncio.run(run())

    def test_multipart_64mib_slow_consumer_bounded_reads_and_memory(self):
        before = resources()["rss_bytes"]
        observed = []
        with self.session(timeout=45) as s:
            for key in ("https1", "https2"):
                source = FileSource(64 * 1024 * 1024, sample_memory=True)
                result = s.post(self.fixture[key] + "/multipart-slow", files={"file": source}).json()
                part = result["parts"][0]
                digest = hashlib.sha256()
                for _ in range(1024):
                    digest.update(SLAB)
                self.assertEqual(part["bytes"], source.size)
                self.assertEqual(part["sha256"], digest.hexdigest())
                self.assertFalse(source.closed)
                self.assertEqual(source.maximum, 65536)
                self.assertGreaterEqual(source.reads, 1024)
                if before is not None and source.peak_rss is not None:
                    self.assertLess(source.peak_rss - before, 48 * 1024 * 1024)
                observed.append(dict(protocol=result["protocol"], bytes=part["bytes"], max_read=source.maximum, peak_rss=source.peak_rss))
        after = resources()["rss_bytes"]
        if before is not None and after is not None:
            self.assertLess(after - before, 48 * 1024 * 1024)
        self.metrics["cases"]["multipart_large"] = dict(transfers=observed, rss_before=before, rss_after=after)

    def test_multipart_read_failure_keeps_file_open_and_cleans_request(self):
        with self.session() as s:
            for key in ("https1", "https2"):
                source = FileSource(1024 * 1024, fail=True)
                with self.assertRaisesRegex(OSError, "fixture file read failed"):
                    s.post(self.fixture[key] + "/multipart", files={"file": source})
                self.assertFalse(source.closed)
                self.assertEqual(s.stats()["open_requests"], 0)
                self.assertEqual(s.get(self.fixture[key] + "/status?code=204").status_code, 204)

    def test_redirects_buffered_body_replay_and_one_shot_multipart(self):
        with self.session() as s:
            for key in ("https1", "https2"):
                base = self.fixture[key] + "/redirect-code?code="
                for options, expected in ((dict(json={"x": 1}), b'{"x":1}'), (dict(data={"x": 1}), b"x=1")):
                    response = s.post(base + "307", **options)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.body(response), expected)
                for code in (307, 308):
                    file = FileSource(4 * 1024 * 1024)
                    self.assertEqual(s.post(base + str(code), files={"file": file}).status_code, code)
                    self.assertFalse(file.closed)
                file = FileSource(4 * 1024 * 1024)
                self.assertEqual(s.post(base + "302", files={"file": file}).json()["method"], "GET")
                self.assertFalse(file.closed)

    def test_response_charset_override_and_status_error(self):
        with self.session() as s:
            for key in ("https1", "https2"):
                r = s.get(self.fixture[key] + "/charset")
                self.assertEqual(r.encoding, "iso-8859-1")
                self.assertEqual(r.text, "café")
                r.encoding = "utf-8"
                self.assertEqual(r.text, "caf�")
                r.encoding = "not-a-codec"
                with self.assertRaises(LookupError): _ = r.text
                for code in (204, 304, 400, 503):
                    r = s.get(self.fixture[key] + "/status?code=" + str(code), allow_redirects=False)
                    self.assertEqual(r.ok, code < 400)
                    if code >= 400:
                        with self.assertRaises(FastlsHTTPError) as caught: r.raise_for_status()
                        self.assertIs(caught.exception.response, r)
                        self.assertEqual(caught.exception.status_code, code)
                    else: self.assertIsNone(r.raise_for_status())

    def test_async_query_json_form_and_charset(self):
        async def run():
            async with self.session(async_=True) as s:
                for key in ("https1", "https2"):
                    r = await s.post(self.fixture[key] + "/inspect", params={"q": ["a", "中文"]}, json=None)
                    self.assertEqual(r.json()["query"], {"q": ["a", "中文"]})
                    self.assertEqual(self.body(r), b"null")
                    r = await s.post(self.fixture[key] + "/inspect", data={"q": "中文"})
                    self.assertEqual(self.body(r), b"q=%E4%B8%AD%E6%96%87")
                    r = await s.get(self.fixture[key] + "/charset")
                    self.assertEqual(r.text, "café")
                    r = await s.get(self.fixture[key] + "/status?code=503")
                    with self.assertRaises(FastlsHTTPError): r.raise_for_status()
        asyncio.run(run())

    def test_async_multipart_file_failure_and_success(self):
        async def run():
            async with self.session(async_=True) as s:
                for key in ("https1", "https2"):
                    for fail in (True, False):
                        source = FileSource(2 * 1024 * 1024, fail=fail)
                        if fail:
                            with self.assertRaisesRegex(OSError, "fixture file read failed"):
                                await s.post(self.fixture[key] + "/multipart", files={"file": source})
                        else:
                            r = await s.post(self.fixture[key] + "/multipart", files={"file": source}, data={"tag": "value"})
                            self.assertEqual(r.json()["parts"][1]["bytes"], source.size)
                        self.assertFalse(source.closed)
        asyncio.run(run())

    def test_async_64mib_multipart_bounded_stream(self):
        async def run():
            async with self.session(async_=True, timeout=45) as s:
                observed = []
                for key in ("https1", "https2"):
                    source = FileSource(64 * 1024 * 1024, sample_memory=True)
                    before = source.peak_rss
                    result = (await s.post(self.fixture[key] + "/multipart-slow", files={"file": source})).json()
                    part = result["parts"][0]
                    digest = hashlib.sha256()
                    for _ in range(1024): digest.update(SLAB)
                    self.assertEqual(part["bytes"], source.size)
                    self.assertEqual(part["sha256"], digest.hexdigest())
                    self.assertFalse(source.closed)
                    if before is not None and source.peak_rss is not None:
                        self.assertLess(source.peak_rss - before, 48 * 1024 * 1024)
                    observed.append(dict(protocol=result["protocol"], bytes=part["bytes"], max_read=source.maximum, peak_rss=source.peak_rss))
                self.metrics["cases"]["async_multipart_large"] = dict(transfers=observed)
        asyncio.run(run())

    def test_large_json_and_form_switch_to_stream_without_corruption(self):
        text = "中文" * 100000
        with self.session() as s:
            for key in ("https1", "https2"):
                r = s.post(self.fixture[key] + "/inspect", json={"value": text})
                import json
                self.assertEqual(json.loads(self.body(r)), {"value": text})
                r = s.post(self.fixture[key] + "/inspect", data={"value": text})
                from urllib.parse import parse_qs
                self.assertEqual(parse_qs(self.body(r).decode()), {"value": [text]})

    def test_async_conflicts_fail_before_upload_and_status_check_before_read(self):
        async def run():
            source = FileSource(1024)
            async with self.session(async_=True) as s:
                for options in (dict(json=None, data=b""), dict(files={"f": source}, duplex=True), dict(json=float("inf"))):
                    with self.assertRaises(ValueError):
                        await s.post(self.fixture["https2"] + "/inspect", **options)
                    self.assertEqual(s.stats()["open_requests"], 0)
                async with s.stream("GET", self.fixture["https2"] + "/status?code=503") as r:
                    with self.assertRaises(FastlsHTTPError): r.raise_for_status()
                    self.assertIsNone(r._content)
                self.assertEqual(source.reads, 0)
                self.assertFalse(source.closed)
        asyncio.run(run())

    def test_async_cancel_slow_multipart_keeps_file_and_session_usable(self):
        async def run():
            async with self.session(async_=True, timeout=60) as s:
                for key in ("https1", "https2"):
                    source = FileSource(1024 * 1024 * 1024)
                    task = asyncio.create_task(s.post(self.fixture[key] + "/multipart-slow", files={"file": source}))
                    for _ in range(100):
                        if source.reads > 4: break
                        await asyncio.sleep(.01)
                    self.assertGreater(source.reads, 4)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                    self.assertFalse(source.closed)
                    self.assertGreater(source.left, 0)
                    self.assertEqual(s.stats()["open_requests"], 0)
                    self.assertEqual((await s.get(self.fixture[key] + "/status?code=204")).status_code, 204)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
