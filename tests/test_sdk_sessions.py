"""Application state belongs to SDKs; native contexts only own transport resources."""
import json
import unittest
from test_stability import FixtureCase, FastlsError


class SDKSessionAcceptance(FixtureCase):
    def test_native_context_has_no_cookie_store(self):
        with self.session() as session:
            url = self.fixture["https2"]
            session.get(url + "/cookie-set?name=login&value=yes")
            raw = json.dumps({"action": "get", "url": url}).encode()
            with self.assertRaisesRegex(FastlsError, "no cookie store"):
                session._native.call("fastls_session_cookies", session._session, raw, len(raw))
            self.assertEqual(session.cookies.get("login", url=url), "yes")
            session.cookies.clear()
            self.assertNotIn("Cookie", session.get(url + "/inspect").json()["headers"])

    def test_sdk_redirect_limit_releases_handles_and_keeps_cookies(self):
        with self.session() as session:
            url = self.fixture["https2"]
            with self.assertRaisesRegex(FastlsError, "maximum redirects"):
                session.get(url + "/cookie-redirect", max_redirects=0)
            self.assertEqual(session.stats()["open_requests"], 0)
            self.assertEqual(session.cookies.get("first", url=url), "one")
            self.assertEqual(session.get(url + "/status?code=204").status_code, 204)


if __name__ == "__main__":
    unittest.main(verbosity=2)
