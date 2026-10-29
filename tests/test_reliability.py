"""Shared Python/Node business retry and offline profile acceptance."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import uuid
from test_stability import FixtureCase
from fastls import RetryPolicy, FastlsError, FastlsTimeoutError, ProfileBundle, ProfileStore, ProfileSpec, Session

SPEC = Path(__file__).parent / "fixtures/profile_spec.json"


class Reliability(FixtureCase):
    def endpoint(self, protocol="https2", **options):
        options.setdefault("id", uuid.uuid4().hex)
        return self.fixture[protocol] + "/retry?" + urlencode(options), options["id"]

    def test_status_retry_is_opt_in_and_bounded(self):
        for protocol in ("http", "https2", "https3"):
            with self.session(**({"http_version": "h3"} if protocol == "https3" else {})) as session:
                url, _ = self.endpoint(protocol, failures=2)
                self.assertEqual(session.get(url).status_code, 503)
                url, _ = self.endpoint(protocol, failures=2)
                response = session.get(url, retry=RetryPolicy(backoff=0))
                self.assertEqual(response.json()["attempts"], 3)
                url, ident = self.endpoint(protocol, failures=5)
                self.assertEqual(session.get(url, retry=RetryPolicy(backoff=0)).status_code, 503)
                self.assertEqual(self.state(ident)["attempts"], 3)

    def test_non_idempotent_methods_require_authorization(self):
        with self.assertRaises(ValueError):
            RetryPolicy(methods=("POST",))
        with self.session(retry=RetryPolicy(backoff=0)) as session:
            url, ident = self.endpoint(failures=1)
            self.assertEqual(session.post(url, data=b"business").status_code, 503)
            self.assertEqual(self.state(ident)["attempts"], 1)
            url, _ = self.endpoint(failures=1)
            policy = RetryPolicy(methods=("POST",), allow_non_idempotent=True, backoff=0)
            response = session.post(url, data=b"business", retry=policy)
            self.assertEqual(base64.b64decode(response.json()["body"]), b"business")
            url, _ = self.endpoint(failures=1)
            self.assertEqual(session.get(url, retry=None).status_code, 503)

    def test_one_shot_sources_and_explicit_factories(self):
        with self.session(retry=RetryPolicy(backoff=0)) as session:
            url, ident = self.endpoint(failures=1)
            self.assertEqual(session.put(url, data=iter([b"one-shot"])).status_code, 503)
            self.assertEqual(self.state(ident)["attempts"], 1)
            made = []
            def factory():
                made.append(True)
                return iter([b"factory"])
            url, _ = self.endpoint(failures=2)
            response = session.put(url, body_factory=factory)
            self.assertEqual(base64.b64decode(response.json()["body"]), b"factory")
            self.assertEqual(len(made), 3)
            made.clear()
            response = session.put(self.fixture["https2"] + "/redirect-code?code=307", body_factory=factory)
            self.assertEqual(base64.b64decode(response.json()["body"]), b"factory")
            self.assertEqual(len(made), 2)

    def test_large_scalars_can_replay_307(self):
        with self.session() as session:
            for body in (b"x" * (600 * 1024), "中" * (200 * 1024)):
                response = session.put(self.fixture["https2"] + "/redirect-code?code=307", data=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(base64.b64decode(response.json()["body"]), body.encode() if isinstance(body, str) else body)

    def test_retry_after_cap_and_total_deadline(self):
        with self.session() as session:
            url, _ = self.endpoint(failures=1, after="99999999999999999999999")
            started = time.monotonic()
            self.assertEqual(session.get(url, retry=RetryPolicy(backoff=0, max_delay=0.02)).status_code, 200)
            self.assertLess(time.monotonic() - started, 2)
            url, ident = self.endpoint(failures=1, after="100")
            with self.assertRaises(FastlsTimeoutError):
                session.get(url, timeout=0.1, retry=RetryPolicy(backoff=0))
            self.assertEqual(self.state(ident)["attempts"], 1)

    def test_disconnect_retry_and_body_read_failure_boundary(self):
        with self.session() as session:
            url, _ = self.endpoint("http", failures=1, disconnect=1)
            self.assertEqual(session.get(url, retry=RetryPolicy(backoff=0)).status_code, 200)
            url, ident = self.endpoint(truncate=1)
            with self.assertRaises(FastlsError):
                session.get(url, retry=RetryPolicy(backoff=0))
            self.assertEqual(self.state(ident)["attempts"], 1)

    def test_cancellation_and_close_interrupt_backoff(self):
        async def check():
            async with self.session(async_=True) as session:
                url, ident = self.endpoint(failures=100, after="5")
                task = asyncio.create_task(session.get(url, retry=RetryPolicy(backoff=0)))
                for _ in range(200):
                    if self.state(ident)["attempts"] == 1:
                        break
                    await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(session.stats()["open_requests"], 0)
                self.assertEqual(self.state(ident)["attempts"], 1)
            session = self.session(async_=True)
            url, ident = self.endpoint(failures=100, after="5")
            task = asyncio.create_task(session.get(url, retry=RetryPolicy(backoff=0)))
            for _ in range(200):
                if self.state(ident)["attempts"] == 1:
                    break
                await asyncio.sleep(0.01)
            await session.aclose()
            with self.assertRaises(FastlsError):
                await asyncio.wait_for(task, 1)
        asyncio.run(check())

    def test_policy_certificate_and_unclassified_errors_never_retry(self):
        retry = RetryPolicy(backoff=0)
        for code in ("request_error", "cancelled", "address_denied", "certificate_error", "decode_error", "invalid_configuration", None):
            self.assertFalse(retry.eligible("GET", 0, True, error=FastlsError("redacted", code=code)))
        with self.session(address_policy={"deny_cidrs": ["127.0.0.0/8"]}, retry=retry) as session:
            with self.assertRaises(FastlsError) as failure:
                session.get(self.fixture["https2"] + "/inspect")
            self.assertEqual(failure.exception.code, "address_denied")
        with Session(retry=retry) as session:
            with self.assertRaises(FastlsError) as failure:
                session.get(self.fixture["https2"] + "/inspect")
            self.assertEqual(failure.exception.code, "certificate_error")

    def test_sync_close_and_concurrent_backoff_cleanup(self):
        session = self.session()
        url, ident = self.endpoint(failures=100, after="5")
        errors = []
        def worker():
            try:
                session.get(url, retry=RetryPolicy(backoff=0))
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            for _ in range(200):
                if self.state(ident)["attempts"] == 1:
                    break
                time.sleep(0.01)
        finally:
            session.close()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors and isinstance(errors[0], FastlsError))
        async def check():
            async with self.session(async_=True) as owner:
                tasks = [asyncio.create_task(owner.get(self.endpoint(failures=100, after="5")[0], retry=RetryPolicy(backoff=0))) for _ in range(20)]
                try:
                    for _ in range(200):
                        if len(owner._retry_waiters) == 20:
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(len(owner._retry_waiters), 20)
                    self.assertEqual((await owner.get(self.fixture["https2"] + "/inspect")).status_code, 200)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                self.assertEqual(len(owner._retry_waiters), 0)
                self.assertEqual(owner.stats()["open_requests"], 0)
        asyncio.run(check())

    def test_factory_failure_and_duplex_rejection_release_resources(self):
        with self.session(retry=RetryPolicy(backoff=0)) as session:
            made = []
            def factory():
                made.append(True)
                if len(made) == 2:
                    raise RuntimeError("factory failed")
                return b"body"
            url, ident = self.endpoint(failures=2)
            with self.assertRaises(RuntimeError):
                session.put(url, body_factory=factory)
            self.assertEqual(self.state(ident)["attempts"], 1)
            self.assertEqual(session.stats()["open_requests"], 0)
            with self.assertRaises(ValueError):
                session.get(url, duplex=True)
            with self.assertRaises(ValueError):
                session.get(url, body_factory=lambda: b"x", data=b"conflict")

    def test_redirect_chain_shares_retry_budget(self):
        with self.session(retry=RetryPolicy(max_retries=2, backoff=0)) as session:
            target, ident = self.endpoint(failures=3)
            initial, initial_id = self.endpoint(failures=1, target=target)
            self.assertEqual(session.get(initial).status_code, 503)
            self.assertEqual(self.state(initial_id)["attempts"], 2)
            self.assertEqual(self.state(ident)["attempts"], 2)

    def test_retry_policy_snapshot_validation_and_header_timeouts(self):
        methods, statuses = ["GET"], [503]
        policy = RetryPolicy(methods=methods, statuses=statuses)
        methods.append("POST"); statuses.append(401)
        self.assertEqual(policy.methods, ("GET",))
        self.assertEqual(policy.statuses, (503,))
        for options in ({"max_retries": True}, {"backoff": float("nan")}, {"statuses": [401]}, {"methods": "GET"}, {"jitter": 1}):
            with self.assertRaises((ValueError, TypeError)):
                RetryPolicy(**options)
        self.assertTrue(policy.eligible("GET", 0, True, error=FastlsTimeoutError("headers", "headers")))
        self.assertFalse(policy.eligible("GET", 0, True, error=FastlsTimeoutError("total", "total")))
        self.assertFalse(policy.eligible("GET", 0, True, error=FastlsTimeoutError("read", "read")))

    def test_profile_bundle_roundtrip_pin_and_wire_replay(self):
        bundle = ProfileBundle.create(ProfileSpec(json.loads(SPEC.read_text())), source="local-fixture")
        again = ProfileBundle(bundle.dumps(), sha256=bundle.sha256)
        self.assertEqual(again.spec.to_dict(), bundle.spec.to_dict())
        self.assertEqual(again.sha256, bundle.sha256)
        with self.session(profile_spec=again.spec) as session:
            self.assertEqual(session.get(self.fixture["wire"] + "/").json()["settings"], bundle.spec.to_dict()["http2"]["settings"])

    def test_profile_store_atomic_activation_and_rollback(self):
        first = ProfileBundle.create(ProfileSpec(json.loads(SPEC.read_text())), source="one")
        value = first.spec.to_dict(); value["http2"]["settings"] = [{"id": 2, "value": 0}, {"id": 4, "value": 65536}]
        second = ProfileBundle.create(ProfileSpec(value), source="two")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            store = ProfileStore(directory / "store")
            first.export(directory / "first.json"); second.export(directory / "second.json")
            store.install(directory / "first.json", sha256=first.sha256)
            store.install(directory / "second.json", sha256=second.sha256)
            self.assertEqual(store.load().sha256, second.sha256)
            self.assertEqual(store.rollback().sha256, first.sha256)
            before = (directory / "store/state.json").read_bytes()
            with self.assertRaises(ValueError):
                store.install(directory / "second.json", sha256="0" * 64)
            self.assertEqual((directory / "store/state.json").read_bytes(), before)
            with self.assertRaises(ValueError):
                store.load("../outside")

    def test_bundle_rejects_duplicates_corruption_unknown_fields_and_backend_gaps(self):
        bundle = ProfileBundle.create(ProfileSpec(json.loads(SPEC.read_text())), source="fixture")
        data = json.loads(bundle.dumps())
        invalid = [bundle.dumps().replace('"schema_version":1', '"schema_version":1,"schema_version":1', 1),
                   json.dumps(dict(data, unknown=True)), json.dumps(dict(data, sha256="0" * 64)),
                   json.dumps(dict(data, profile_id="different"))]
        for text in invalid:
            with self.assertRaises(ValueError):
                ProfileBundle(text, sha256=bundle.sha256)
        raw = '{"schema_version":1,"id":"broken","tls":{}}'
        bad = dict(data, spec_json=raw, profile_id="broken", sha256=hashlib.sha256(raw.encode()).hexdigest())
        with self.assertRaises(FastlsError):
            ProfileBundle(json.dumps(bad), sha256=bad["sha256"])
        with self.assertRaises(ValueError):
            ProfileBundle(bundle.dumps(), sha256="not-a-pin")
        for raw in ('{"schema_version":1,"schema_version":1}', '[' * 70 + '0' + ']' * 70,
                    '{"schema_version":1.0}', '{"text":"\\ud800"}'):
            digest = hashlib.sha256(raw.encode()).hexdigest()
            with self.assertRaises((ValueError, FastlsError)):
                ProfileBundle(json.dumps(dict(data, spec_json=raw, sha256=digest)), sha256=digest)
        with self.assertRaises(ValueError):
            ProfileBundle(" " * (512 * 1024 + 1), sha256=bundle.sha256)


if __name__ == "__main__":
    unittest.main(verbosity=2)
