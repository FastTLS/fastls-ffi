"""Python-only convenience API checks against the real library and local HTTP."""
import sys
from pathlib import Path
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import fastls
from fastls import Session, FastlsError
from test_python import BODY, Handler, IntegrationServer


class APIHandler(Handler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("X-Request-Method", self.command)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("X-Request-Method", self.command)
        self.send_header("Content-Length", "0")
        self.end_headers()


class API(unittest.TestCase):
    def setUp(self):
        self.server = IntegrationServer(("127.0.0.1", 0), APIHandler)
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

    def test_module_request_and_get_return_readable_closed_responses(self):
        for call in (lambda url, **kw: fastls.request("GET", url, **kw), fastls.get):
            with self.subTest(call=call):
                response = call(self.url + "/headers", headers={"X-Direct": "hello"}, timeout=2)
                self.assertEqual(response.json()["X-Direct"], "hello")
                self.assertIn('"X-Direct": "hello"', response.text)
                self.assertIsNone(response._handle)
                self.assertTrue(response._owner._closed)
                self.assertEqual(len(response._owner._handles), 0)
        first = fastls.get(self.url, params={"q": "hello world"})
        second = fastls.get(self.url)
        self.assertIn("q=hello+world", first.url)
        self.assertEqual(first.content, BODY)
        self.assertIsNot(first._owner, second._owner)

    def test_module_http_methods_preserve_body_and_http_error_behavior(self):
        for method in ("post", "put", "patch", "delete", "options", "head"):
            with self.subTest(method=method):
                kwargs = {} if method == "head" else {"json": {"hello": "world"}}
                response = getattr(fastls, method)(self.url, **kwargs)
                self.assertEqual(response.headers["X-Request-Method"], method.upper())
                self.assertEqual(response.content, b"" if method == "head" else b'{"hello":"world"}')
                self.assertTrue(response._owner._closed)
        response = fastls.request("CUSTOM", self.url)
        self.assertEqual(response.status_code, 501)
        self.assertTrue(response._owner._closed)
        with self.assertRaises(fastls.FastlsHTTPError) as caught:
            response.raise_for_status()
        self.assertIs(caught.exception.response, response)
        self.assertIn("CUSTOM", response.text)

    def test_module_request_releases_session_on_validation_and_timeout_errors(self):
        from unittest.mock import patch

        sessions = []
        def capture_session(**kwargs):
            session = Session(**kwargs)
            sessions.append(session)
            return session

        with patch("fastls.api.Session", side_effect=capture_session):
            with self.assertRaises(FastlsError):
                fastls.get(self.url, profile="unknown/version/platform", notifications="poll", cookies=False)
            with self.assertRaises(fastls.FastlsTimeoutError):
                fastls.get(self.url + "/wait", timeout=0.1)
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s._closed and not s._handles for s in sessions))


if __name__ == "__main__":
    unittest.main(verbosity=2)
