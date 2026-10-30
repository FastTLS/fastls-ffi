"""Real ABI and SDK capability contracts; Node counterparts use the same peer."""
import asyncio
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from test_stability import FixtureCase, FastlsError, SLAB
from fastls import ProfileSpec

SPEC_FILE = Path(__file__).parent / "fixtures" / "profile_spec.json"


class CapabilityAcceptance(FixtureCase):
    def test_policy_owner_snapshot_and_redirect_binding(self):
        policy = {"allow_hosts": ["127.0.0.1"], "allow_cidrs": ["127.0.0.0/8"]}
        with self.session(address_policy=policy) as allowed, self.session(address_policy={"deny_cidrs": ["127.0.0.0/8"]}) as denied:
            policy["allow_hosts"].append("localhost")
            url = self.fixture["https2"]
            self.assertEqual(allowed.get(url + "/inspect").status_code, 200)
            for session, path in ((denied, "/inspect"), (allowed, "/redirect-host")):
                with self.assertRaises(FastlsError) as failure:
                    session.get(url + path)
                self.assertEqual(failure.exception.code, "address_denied")
                self.assertEqual(session.stats()["open_requests"], 0)
            self.assertEqual(allowed.get(url + "/status?code=204").status_code, 204)

    def test_configuration_rejection_and_abi_allocation(self):
        with self.session() as session:
            baseline = session.stats()["open_sessions"]
            for value in ({"schema_version": 2}, {"schema_version": 1, "address_policy": None},
                          {"schema_version": 1, "profile_spec": {}}, {"schema_version": 1, "http_version": "h2"}):
                raw = json.dumps(value).encode()
                with self.assertRaises(FastlsError) as failure:
                    session._native.call("fastls_transport_create_with_options", raw, len(raw))
                self.assertEqual(failure.exception.code, "invalid_configuration")
            raw = b'{"schema_version":1,"schema_version":1}'
            with self.assertRaises(FastlsError):
                session._native.call("fastls_transport_create_with_options", raw, len(raw))
            with self.assertRaises(FastlsError) as failure:
                session._native.call("fastls_transport_create_with_options", None, 1)
            self.assertEqual(failure.exception.code, "invalid_configuration")
            self.assertEqual(session.stats()["open_sessions"], baseline)
            real = session._native
            for missing_symbol in (False, True):
                class LibraryView:
                    def __getattr__(self, name):
                        if missing_symbol and name == "fastls_transport_create_with_options":
                            raise AttributeError(name)
                        return getattr(real.lib, name)
                class LegacyView:
                    lib = LibraryView()
                    def call(self, name, *args):
                        value = real.call(name, *args)
                        if name == "fastls_features" and not missing_symbol:
                            return [v for v in value if v not in ("profile_spec_v1", "http3_v1", "transport_address_policy_v1")]
                        return value
                with patch("fastls.client.Native", return_value=LegacyView()):
                    for options in ({"address_policy": {}}, {"http_version": "h3"}):
                        with self.assertRaises(FastlsError) as failure:
                            self.session(**options)
                        self.assertEqual(failure.exception.code, "unsupported_capability")
            self.assertEqual(session.stats()["open_sessions"], baseline)

    def test_complete_spec_wire_and_pool_isolation(self):
        value = json.loads(SPEC_FILE.read_text())
        value["http2"]["settings"] = [{"id": 2, "value": 0}, {"id": 4, "value": 65536}]
        value["headers"]["pseudo_order"] = [":path", ":authority", ":method", ":scheme"]
        value["headers"]["order"] = ["x-b", "x-a", "user-agent", "accept"]
        first = ProfileSpec(value)
        value["tls"]["cipher_suites"] = list(reversed(value["tls"]["cipher_suites"]))
        second = ProfileSpec(value)
        with self.session() as session:
            for spec in (first, second, first):
                wire = session.get(self.fixture["wire"] + "/", profile_spec=spec, headers={"X-A": "a", "X-B": "b"}).json()
                expected = [int(c, 16) for c in spec.to_dict()["tls"]["cipher_suites"]]
                clean = lambda codes: [c for c in codes if c & 0x0f0f != 0x0a0a]
                self.assertEqual(clean(wire["ciphers"]), clean(expected))
                self.assertEqual(wire["settings"], [{"id": 2, "value": 0}, {"id": 4, "value": 65536}])
                self.assertEqual(wire["headers"][:4], [":path", ":authority", ":method", ":scheme"])
                self.assertLess(wire["headers"].index("x-b"), wire["headers"].index("x-a"))
                self.assertIn("h2", wire["alpn"])
            self.assertEqual(session.stats()["session_pools"], 2)

    def test_spec_presence_conflicts_and_snapshot(self):
        value = json.loads(SPEC_FILE.read_text())
        spec = ProfileSpec(value)
        value["schema_version"] = 999
        with self.session(profile_spec=spec) as session:
            self.assertEqual(session.get(self.fixture["https2"] + "/inspect").status_code, 200)
            with self.assertRaises(FastlsError):
                session.get(self.fixture["https2"], profile="chrome/156/windows")
        with self.assertRaises(ValueError):
            self.session(profile="chrome/156/windows", profile_spec=spec)
        for settings in ("missing", "null", "empty"):
            broken = spec.to_dict()
            if settings == "missing":
                del broken["http2"]["settings"]
            else:
                broken["http2"]["settings"] = None if settings == "null" else []
            if settings == "empty":
                with self.session(profile_spec=ProfileSpec(broken)) as session:
                    wire = session.get(self.fixture["wire"]).json()
                    self.assertEqual(wire["settings"], [])
            else:
                with self.assertRaises(FastlsError) as failure:
                    self.session(profile_spec=ProfileSpec(broken))
                self.assertEqual(failure.exception.code, "invalid_configuration")

    def test_h3_upload_download_sync_async(self):
        url = self.fixture["https3"]
        data = SLAB * 4
        with self.session(http_version="h3") as session:
            response = session.get(url + "/?n=" + str(len(data)))
            self.assertEqual(response.http_version, "HTTP/3.0")
            self.assertEqual(response.content, data)
            response = session.post(url + "/upload", data=iter((SLAB, SLAB)))
            self.assertEqual(response.json()["sha256"], hashlib.sha256(SLAB * 2).hexdigest())
            self.assertEqual(response.http_version, "HTTP/3.0")
        async def exercise():
            async with self.session(async_=True, http_version="h3") as session:
                response = await session.post(url + "/echo", data=data)
                self.assertEqual(await response.aread(), data)
                self.assertEqual(response.http_version, "HTTP/3.0")
        asyncio.run(exercise())

    def test_h3_cancel_sibling_and_recovery(self):
        with self.session(http_version="h3") as session:
            with session.stream("GET", self.fixture["https3"] + "/hold?id=h3-cancel") as response:
                self.assertEqual(next(response.iter_bytes()), b"first")
                self.assertEqual(session.get(self.fixture["https3"] + "/status?code=204").status_code, 204)
                response.cancel()
                with self.assertRaises(FastlsError):
                    response.read()
            self.assertEqual(session.get(self.fixture["https3"] + "/status?code=204").status_code, 204)

    def test_h3_policy_combination_and_owner_isolation(self):
        url = self.fixture["https3"]
        policy = {"allow_hosts": ["127.0.0.1"], "allow_cidrs": ["127.0.0.0/8"]}
        with self.session(http_version="h3", address_policy=policy) as allowed, self.session(http_version="h3", address_policy={"deny_cidrs": ["127.0.0.0/8"]}) as denied:
            policy["allow_hosts"][0] = "blocked.test"
            response = allowed.get(url + "/echo")
            self.assertEqual(response.http_version, "HTTP/3.0")
            with self.assertRaises(FastlsError) as failure:
                denied.get(url)
            self.assertEqual(failure.exception.code, "address_denied")
            with self.assertRaises(TypeError):
                denied.get(url, address_policy={})
            self.assertEqual(allowed.get(url + "/status?code=204").status_code, 204)
        with self.session(address_policy={"allow_cidrs": ["127.0.0.0/8"]}) as owner:
            self.assertEqual(owner.get(url, http_version="h3").http_version, "HTTP/3.0")
        async def exercise():
            async with self.session(async_=True, http_version="h3", address_policy={"allow_cidrs": ["127.0.0.0/8"]}) as owner:
                response = await owner.post(url + "/echo", data=SLAB)
                self.assertEqual(await response.aread(), SLAB)
        asyncio.run(exercise())

    def test_h3_policy_combined_capability_before_allocation(self):
        with self.session() as monitor:
            real = monitor._native
            baseline = monitor.stats()["open_sessions"]
            class LegacyView:
                lib = real.lib
                def __getattr__(self, name):
                    return getattr(real, name)
                def call(self, name, *args):
                    value = real.call(name, *args)
                    return [v for v in value if v != "http3_address_policy_v1"] if name == "fastls_features" else value
            with patch("fastls.client.Native", return_value=LegacyView()):
                with self.assertRaises(FastlsError) as failure:
                    self.session(http_version="h3", address_policy={})
                self.assertEqual(failure.exception.code, "unsupported_capability")
                with self.session(address_policy={}) as owner:
                    with self.assertRaises(FastlsError) as failure:
                        owner.get(self.fixture["https3"], http_version="h3")
                    self.assertEqual(failure.exception.code, "unsupported_capability")
                    self.assertEqual(owner.stats()["open_requests"], 0)
            self.assertEqual(monitor.stats()["open_sessions"], baseline)

    def test_unsupported_combinations_and_no_tcp_fallback(self):
        with self.session(http_version="h3", timeout=0.3) as session:
            for options in ({"profile": "chrome/156/windows"}, {"proxy": "http://127.0.0.1:1"}, {"http_version": "auto"}):
                with self.assertRaises(FastlsError) as failure:
                    session.get(self.fixture["https3"], **options)
                self.assertEqual(failure.exception.code, "unsupported_capability")
            with self.assertRaises(FastlsError):
                session.get(self.fixture["https2"] + "/status?code=204")
            self.assertEqual(session.stats()["open_requests"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
