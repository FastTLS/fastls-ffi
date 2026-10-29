"""Python-specific credentials and request-local Cookie acceptance."""
import asyncio
import base64
import unittest
from urllib.parse import urlencode
from test_stability import FixtureCase
from fastls import BasicAuth, BearerAuth, ClientSession, FastlsError, get


class PythonCompatibility(FixtureCase):
    def test_auth_precedence_disable_and_secret_representation(self):
        auth = BasicAuth("alice", "secret-value")
        self.assertNotIn("alice", repr(auth))
        self.assertNotIn("secret-value", repr(auth))
        self.assertNotIn("secret-token", repr(BearerAuth("secret-token")))
        with self.session(auth=auth) as session:
            url = self.fixture["https2"] + "/inspect"
            expected = "Basic " + base64.b64encode(b"alice:secret-value").decode()
            self.assertEqual(session.get(url).json()["headers"]["Authorization"], [expected])
            self.assertEqual(session.get(url, auth=BearerAuth("other")).json()["headers"]["Authorization"], ["Bearer other"])
            self.assertNotIn("Authorization", session.get(url, auth=None).json()["headers"])
            self.assertEqual(session.get(url, auth=("user", "pass"), headers={"AUTHORIZATION": "explicit"}).json()["headers"]["Authorization"], ["explicit"])

    def test_cross_origin_credentials_and_cookie_overlay_never_reappear(self):
        with self.session(auth=BearerAuth("private"), headers={"Authorization": "explicit", "Cookie": "manual=secret"}) as session:
            headers = session.get(self.fixture["https2"] + "/redirect-host", request_cookies={"temporary": "secret"}).json()["headers"]
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("Cookie", headers)
            returning = self.fixture["https2"].replace("127.0.0.1", "localhost") + "/retry?" + urlencode({"target": self.fixture["https2"] + "/inspect"})
            initial = self.fixture["https2"] + "/retry?" + urlencode({"target": returning})
            headers = session.get(initial, request_cookies={"temporary": "secret"}).json()["headers"]
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("Cookie", headers)

    def test_request_cookies_and_atomic_session_merge(self):
        url = self.fixture["https2"] + "/inspect"
        with self.session() as session:
            session.cookies.update({"first": "stored", "second": "kept"}, url=url)
            headers = session.get(url, request_cookies={"first": "temporary", "third": "local"}).json()["headers"]
            self.assertIn("first=temporary", headers["Cookie"][0])
            self.assertIn("second=kept", headers["Cookie"][0])
            self.assertIn("third=local", headers["Cookie"][0])
            self.assertEqual(session.cookies.get_dict(url), {"first": "stored", "second": "kept"})
            self.assertEqual(session.get(url, request_cookies={"x": "y"}, headers={"Cookie": "explicit=yes"}).json()["headers"]["Cookie"], ["explicit=yes"])
            with self.assertRaises(FastlsError):
                session.cookies.update({"accepted": "value", "bad": "newline\n"}, url=url)
            self.assertNotIn("accepted", session.cookies.get_dict(url))
            session.get(self.fixture["https2"] + "/cookie-redirect", request_cookies={"temporary": "value"})
            self.assertEqual(session.cookies.get("first", url=url), "one")
            self.assertIsNone(session.cookies.get("temporary", url=url))

    def test_oneshot_old_cookie_switch_and_new_overlay(self):
        response = get(self.fixture["https1"] + "/inspect", ca_file=self.fixture["ca_file"], cookies=False,
                       request_cookies={"one": "request"}, auth=("a", "b"))
        self.assertEqual(response.json()["headers"]["Cookie"], ["one=request"])
        self.assertTrue(response.json()["headers"]["Authorization"][0].startswith("Basic "))
        with self.assertRaises(TypeError):
            get(self.fixture["http"] + "/inspect", cookies={"wrong": "mapping"})

    def test_async_and_context_compatibility(self):
        async def check():
            async with self.session(async_=True, auth=BearerAuth("async")) as session:
                headers = (await session.get(self.fixture["https2"] + "/inspect", request_cookies={"x": "y"})).json()["headers"]
                self.assertEqual(headers["Authorization"], ["Bearer async"])
                self.assertEqual(headers["Cookie"], ["x=y"])
            async with ClientSession(ca_file=self.fixture["ca_file"], auth=BasicAuth("user", "pass")) as session:
                async with session.get(self.fixture["https2"] + "/inspect", auth=None, request_cookies={"ctx": "yes"}) as response:
                    headers = (await response.json())["headers"]
                    self.assertNotIn("Authorization", headers)
                    self.assertEqual(headers["Cookie"], ["ctx=yes"])
        asyncio.run(check())

    def test_bad_credentials_rejected_without_allocation(self):
        for factory in (lambda: BasicAuth("bad:name", "secret"), lambda: BasicAuth("user", "bad\r\n"), lambda: BearerAuth("bad token")):
            with self.assertRaises((ValueError, TypeError)):
                factory()
        with self.session() as session:
            baseline = session.stats()["open_requests"]
            with self.assertRaises(TypeError):
                session.get(self.fixture["http"] + "/inspect", auth=object())
            self.assertEqual(session.stats()["open_requests"], baseline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
