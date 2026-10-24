"""Multi-value response fields and the real native session cookie store."""
import asyncio
import json
import time
import unittest
from urllib.parse import urlencode
import uuid

from test_stability import FixtureCase, EvidenceResult, FastlsError


class CookieAcceptance(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "cookies-" + cls.metrics["label"]

    def echoed(self, session, url, **kwargs):
        data = session.get(url + "/inspect", **kwargs).json()
        return "; ".join(data["headers"].get("Cookie", []))

    def test_repeated_fields_case_insensitive_without_splitting_values(self):
        for name in ("https1", "https2"):
            with self.subTest(protocol=name), self.session() as session:
                response = session.get(self.fixture[name] + "/cookie-fields")
                values = response.headers.get_list("set-cookie")
                self.assertEqual(len(values), 5)
                self.assertEqual(values[1], "future=two; Expires=Fri, 01 Jan 2100 00:00:00 GMT; Path=/")
                self.assertEqual(response.headers["SET-cookie"], values[0])
                self.assertIn("set-cookie", response.headers)
                self.assertEqual(response.headers.get_list("X-LIST"), ["a, b", "literal/,/value"])
                self.assertEqual(response.headers.get_list("missing"), [])
                self.assertEqual(response.headers.get("missing", "default"), "default")
                self.assertEqual(len([p for p in response.headers.multi_items() if p[0].lower() == "set-cookie"]), 5)
                values.clear()
                self.assertEqual(len(response.headers.get_list("Set-Cookie")), 5)
                with self.assertRaises(TypeError):
                    response.headers["X-List"] = "changed"

    def test_automatic_path_secure_loopback_host_scope_and_foreign_domain_rejection(self):
        for name in ("https1", "https2"):
            with self.subTest(protocol=name), self.session() as session:
                base = self.fixture[name]
                session.get(base + "/cookie-fields")
                cookies = session.cookies.get_dict(base + "/")
                self.assertEqual(cookies, {"root": "one", "future": "two", "secure": "four"})
                self.assertEqual(session.cookies.get("private", url=base + "/private/item"), "three")
                self.assertIsNone(session.cookies.get("private", url=base + "/private-other"))
                insecure = session.cookies.get_dict(self.fixture["http"])
                # Go 1.26 follows browser secure-origin behavior for loopback hosts:
                # Secure cookies may be sent to http://127.0.0.1 and localhost.
                self.assertEqual(insecure.get("secure"), "four")
                self.assertNotIn("foreign", insecure)
                localhost = base.replace("127.0.0.1", "localhost")
                self.assertEqual(session.cookies.get_dict(localhost), {})
                self.assertIn("secure=four", self.echoed(session, base))
                self.assertIn("secure=four", self.echoed(session, self.fixture["http"]))

    def test_manual_domain_default_path_duplicate_names_and_exact_delete(self):
        with self.session() as session:
            cookies = session.cookies
            cookies.set("id", "root", url="https://shop.example.com/", path="/")
            cookies.set("id", "private", url="https://shop.example.com/private/page")
            self.assertEqual(cookies.get_list("https://shop.example.com/private/next"), [{"name": "id", "value": "private"}, {"name": "id", "value": "root"}])
            self.assertEqual(cookies.get("id", url="https://shop.example.com/private/next"), "private")
            cookies.delete("id", url="https://shop.example.com/private/page", path="/private")
            self.assertEqual(cookies.get("id", url="https://shop.example.com/private/next"), "root")
            cookies.set("shared", "domain", url="https://shop.example.com", domain=".example.com", path="/")
            self.assertEqual(cookies.get_dict("https://other.example.com"), {"shared": "domain"})
            self.assertEqual(cookies.get_dict("https://notexample.com"), {})

    def test_expiry_max_age_precedence_and_deletion(self):
        with self.session() as session:
            url = self.fixture["https2"]
            cookies = session.cookies
            cookies.set("past", "gone", url=url, expires="2000-01-01T00:00:00Z")
            self.assertIsNone(cookies.get("past", url=url))
            cookies.set("short", "yes", url=url, max_age=1, expires="2000-01-01T00:00:00Z")
            self.assertEqual(cookies.get("short", url=url), "yes")
            time.sleep(1.15)
            self.assertIsNone(cookies.get("short", url=url))
            cookies.set("remove", "yes", url=url, path="/")
            cookies.delete("remove", url=url, path="/")
            self.assertIsNone(cookies.get("remove", url=url))
            cookies.set("zero", "yes", url=url)
            cookies.set("zero", "", url=url, max_age=0)
            self.assertIsNone(cookies.get("zero", url=url))

    def test_server_overwrites_and_deletes_existing_cookie(self):
        for name in ("https1", "https2"):
            with self.session() as session:
                base = self.fixture[name]
                session.get(base + "/cookie-set?name=token&value=first")
                session.get(base + "/cookie-set?name=token&value=second")
                self.assertEqual(self.echoed(session, base), "token=second")
                session.get(base + "/cookie-set?name=token&max_age=-1")
                self.assertEqual(session.cookies.get_dict(base), {})
                self.assertEqual(self.echoed(session, base), "")

    def test_redirect_sends_only_target_hosts_stored_cookies(self):
        for name in ("https1", "https2"):
            with self.session() as session:
                base = self.fixture[name]
                session.cookies.set("source", "private", url=base)
                session.cookies.set("target", "allowed", url=base.replace("127.0.0.1", "localhost"))
                final = session.get(base + "/redirect-host").json()
                self.assertEqual("; ".join(final["headers"]["Cookie"]), "target=allowed")

    def test_redirect_responses_update_store_before_following(self):
        for name in ("https1", "https2"):
            with self.subTest(protocol=name), self.session() as session:
                base = self.fixture[name]
                final = session.get(base + "/cookie-redirect").json()
                outgoing = "; ".join(final["headers"]["Cookie"])
                self.assertIn("first=one", outgoing)
                self.assertIn("second=two", outgoing)
                self.assertEqual(session.cookies.get_dict(base), {"first": "one", "second": "two"})
                session.cookies.clear()
                response = session.get(base + "/cookie-redirect", allow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(session.cookies.get_dict(base), {"first": "one"})

    def test_explicit_cookie_header_controls_outgoing_and_still_accepts_response(self):
        for name in ("https1", "https2"):
            with self.subTest(protocol=name), self.session() as session:
                base = self.fixture[name]
                session.cookies.set("saved", "yes", url=base)
                self.assertEqual(self.echoed(session, base, headers={"cOoKiE": "manual=only"}), "manual=only")
                self.assertEqual(self.echoed(session, base, headers={"Cookie": ""}), "")
                session.get(base + "/cookie-set?name=received&value=yes", headers={"Cookie": "manual=only"})
                self.assertEqual(session.cookies.get_dict(base), {"saved": "yes", "received": "yes"})

    def test_sessions_isolated_profiles_shared_and_clear_keeps_connection_pool(self):
        with self.session() as first, self.session() as second:
            base = self.fixture["https2"]
            initial = first.get(base + "/cookie-set?name=shared&value=yes")
            self.assertEqual(second.cookies.get_dict(base), {})
            other = next(p for p in first.list_profiles() if p.startswith("chrome/") and p != first.profile)
            self.assertIn("shared=yes", self.echoed(first, base, profile=other))
            first.close_idle_connections()
            self.assertEqual(first.cookies.get("shared", url=base), "yes")
            before = first.get(base + "/inspect")
            first.cookies.clear()
            after = first.get(base + "/inspect")
            self.assertNotIn("Cookie", after.json()["headers"])
            self.assertEqual(before.headers["X-Test-Connection"], after.headers["X-Test-Connection"])

    def test_disabled_store_and_access_after_session_close(self):
        with self.session(cookies=False) as session:
            base = self.fixture["https2"]
            session.get(base + "/cookie-set")
            self.assertEqual(session.cookies.get_dict(base), {})
            self.assertEqual(self.echoed(session, base, headers={"Cookie": "manual=only"}), "manual=only")
            with self.assertRaisesRegex(FastlsError, "disabled"):
                session.cookies.set("no", "cookie", url=base)
        with self.assertRaisesRegex(FastlsError, "closed"):
            session.cookies.get_list(base)

    def test_async_concurrent_updates_and_clear(self):
        async def run():
            async with self.session(async_=True) as session:
                base = self.fixture["https2"]
                responses = await asyncio.gather(*(session.get(base + "/cookie-set?" + urlencode({"name": "k" + str(i), "value": str(i)})) for i in range(32)))
                self.assertTrue(all(len(r.headers.get_list("set-cookie")) == 1 for r in responses))
                self.assertEqual(session.cookies.get_dict(base), {"k" + str(i): str(i) for i in range(32)})
                session.cookies.clear()
                self.assertEqual(session.cookies.get_list(base), [])
        asyncio.run(run())

    def test_cookie_headers_available_before_stream_finishes(self):
        for name in ("https1", "https2"):
            with self.subTest(protocol=name), self.session() as session:
                base = self.fixture[name]
                with session.stream("GET", base + "/cookie-hold?id=" + uuid.uuid4().hex) as response:
                    self.assertEqual(response.headers.get_list("Set-Cookie"), ["streamed=ready; Path=/"])
                    self.assertEqual(next(response.iter_bytes(5)), b"first")
                    self.assertEqual(session.cookies.get("streamed", url=base), "ready")
                    self.assertIn("streamed=ready", self.echoed(session, base))
                    response.cancel()
                self.assertEqual(session.cookies.get("streamed", url=base), "ready")

    def test_invalid_cookie_commands_and_inputs_leave_store_unchanged(self):
        with self.session() as session:
            url = self.fixture["https2"]
            session.cookies.set("valid", "kept", url=url)
            cases = [dict(name="bad;name", value="x", url=url), dict(name="bad", value="a\nb", url=url),
                     dict(name="bad", value="x", url="file:///tmp/none"), dict(name="bad", value="x", url=url, domain="evil.invalid"),
                     dict(name="bad", value="x", url="https://shop.example.co.uk", domain=".co.uk"),
                     dict(name="bad", value="x", url=url, expires="tomorrow"), dict(name="bad", value="x" * 4097, url=url)]
            cases.append(dict(name="bad", value="x", url=url, max_age=2147483648))
            for arguments in cases:
                with self.subTest(arguments=list(arguments)), self.assertRaises(FastlsError):
                    session.cookies.set(**arguments)
            for command in ({"action": "unknown"}, {"action": "get", "url": url, "unknown": True}):
                raw = json.dumps(command).encode()
                with self.assertRaises(FastlsError):
                    session._native.call("fastls_session_cookies", session._session, raw, len(raw))
            self.assertEqual(session.cookies.get_dict(url), {"valid": "kept"})


if __name__ == "__main__":
    unittest.main(verbosity=2, testRunner=unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult))
