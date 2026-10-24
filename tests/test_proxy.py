"""Real HTTP/SOCKS tunnels: identity, DNS, deadlines, streaming and cleanup."""
import asyncio
import hashlib
import os
from pathlib import Path
import time
import tempfile
import unittest
from unittest.mock import patch
import uuid

from test_stability import FixtureCase, EvidenceResult, SLAB
from fastls import FastlsError, FastlsTimeoutError, Timeout
from proxy_lab import ProxyLab


class Proxies(FixtureCase):
    def proxy(self, scheme="http", **kwargs):
        if scheme=="https":
            kwargs.setdefault("certfile",self.fixture["proxy_cert_file"])
            kwargs.setdefault("keyfile",self.fixture["proxy_key_file"])
        return ProxyLab(scheme,**kwargs)

    def session(self, async_=False, **kwargs):
        kwargs.setdefault("proxy_ca_file",self.fixture["proxy_ca_file"])
        return super().session(async_=async_,**kwargs)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics["label"] = "proxy-"+cls.metrics["label"]

    def test_protocols_authentication_and_target_header_isolation(self):
        for scheme in ("http", "https", "socks5", "socks5h"):
            for auth in (False, True):
                with self.subTest(scheme=scheme, auth=auth), self.proxy(scheme, auth=auth) as p:
                    with self.session(proxy=p.url) as s:
                        for key in ("http", "https1", "https2"):
                            r=s.get(self.fixture[key]+"/inspect")
                            self.assertEqual(r.status_code,200)
                            headers=r.json()["headers"]
                            self.assertNotIn("Proxy-Authorization",headers)
                            self.assertNotIn("Proxy-Connection",headers)
                            self.assertEqual(r.headers["X-Test-Protocol"],"HTTP/2.0" if key=="https2" else "HTTP/1.1")
                    p.wait_idle()
                    self.assertEqual(len(p.targets),3)
                    if scheme=="https":self.assertEqual(p.tls_protocols,["http/1.1"]*3)

    def test_request_override_direct_and_pool_credential_isolation(self):
        with self.proxy(auth=True) as first, self.proxy("socks5h",auth=True) as second:
            with self.session(proxy=first.url) as s:
                url=self.fixture["https2"]+"/bytes?n=4"
                a=s.get(url).headers["X-Test-Connection"]
                self.assertEqual(a,s.get(url).headers["X-Test-Connection"])
                b=s.get(url,proxy=second.url).headers["X-Test-Connection"]
                direct=s.get(url,proxy=None).headers["X-Test-Connection"]
                self.assertEqual(len({a,b,direct}),3)
                self.assertEqual(a,s.get(url).headers["X-Test-Connection"])
                with self.assertRaises(FastlsError): s.get(url,proxy=first.url.replace("s3cr%40t%3Asecret","wrong"))
                self.assertEqual(a,s.get(url).headers["X-Test-Connection"])
            first.wait_idle(); second.wait_idle()

    def test_socks_dns_modes(self):
        for scheme, expected in (("socks5",1),("socks5h",3)):
            with self.proxy(scheme) as p, self.session(proxy=p.url) as s:
                s.get(self.fixture["http"].replace("127.0.0.1","localhost")+"/bytes?n=4")
                self.assertEqual(p.methods,[expected])
                if scheme=="socks5h":
                    self.assertEqual(s.get(self.fixture["http"].replace("127.0.0.1","proxy-only.invalid")+"/bytes?n=4").status_code,200)
                else:
                    with self.assertRaises(FastlsError):s.get(self.fixture["http"].replace("127.0.0.1","proxy-only.invalid")+"/bytes?n=4")

    def test_invalid_urls_and_credentials_are_not_exposed(self):
        urls=["socks4://proxy-user:secret@127.0.0.1:1","http://proxy-user:secret@host:bad", "http://proxy-user:secret@host:0", "http://proxy-user:secret@host/path", "http://proxy-user:secret@host?query=secret", "http://proxy-user:sec%ZZret@host", "http://:secret@host"]
        with self.session() as s:
            for proxy in urls:
                with self.subTest(proxy=proxy), self.assertRaises(FastlsError) as caught:s.get(self.fixture["http"],proxy=proxy)
                self.assertNotIn("secret",str(caught.exception))
                self.assertNotIn("proxy-user",str(caught.exception))
            for name in ("Proxy-Authorization","proxy-connection"):
                with self.assertRaises(FastlsError):s.get(self.fixture["http"],headers={name:"secret"})
            self.assertEqual(s.stats()["open_requests"],0)

    def test_rejection_and_wrong_auth_do_not_retry_or_leak_secrets(self):
        for scheme in ("http","https","socks5","socks5h"):
            for mode in ("deny","normal"):
                with self.proxy(scheme,auth=True,mode=mode) as p:
                    with self.session(proxy=p.url.replace("s3cr%40t%3Asecret","bad-password")) as s:
                        with self.assertRaises(FastlsError) as caught:s.get(self.fixture["https2"])
                        self.assertNotIn("bad-password",str(caught.exception))
                        self.assertNotIn("proxy-user",str(caught.exception))
                        self.assertNotIn("CredentialMustNotBeEchoed",str(caught.exception))
                    p.wait_idle(); self.assertEqual(p.accepted,1)

    def test_connect_timeout_total_timeout_and_explicit_cancellation(self):
        for scheme, mode in (("http","stall"),("https","stall-tls"),("https","stall"),("socks5","stall"),("socks5h","stall-auth"),("socks5h","stall-connect")):
            with self.subTest(scheme=scheme,mode=mode), self.proxy(scheme,auth=True,mode=mode) as p:
                with self.session(proxy=p.url) as s:
                    for timeout, phase in ((Timeout(total=3,connect=.15),"connect"),(Timeout(total=.15),"total")):
                        started=time.monotonic()
                        with self.assertRaises(FastlsTimeoutError) as caught:s.get(self.fixture["https2"],timeout=timeout)
                        self.assertEqual(caught.exception.phase,phase)
                        self.assertLess(time.monotonic()-started,2)
                        p.wait_idle()
                async def run():
                    async with self.session(async_=True,proxy=p.url) as s:
                        task=asyncio.create_task(s.get(self.fixture["https2"]))
                        while p.accepted<3:await asyncio.sleep(.01)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):await task
                asyncio.run(asyncio.wait_for(run(),5));p.wait_idle()

    def test_wrong_target_ca_and_environment_proxy_is_ignored(self):
        with self.proxy() as p:
            with self.session(proxy=p.url) as s:
                s._ca_pem=Path(self.fixture["wrong_ca_file"]).read_text()
                with self.assertRaises(FastlsError):s.get(self.fixture["https2"])
            with patch.dict(os.environ,{"HTTP_PROXY":p.url,"HTTPS_PROXY":p.url,"ALL_PROXY":p.url}):
                before=p.accepted
                with self.session() as s:s.get(self.fixture["https2"])
                self.assertEqual(p.accepted,before)
            p.wait_idle()

    def test_plain_http_handshake_deadline_releases_socket(self):
        for scheme in ("http","https","socks5","socks5h"):
            with self.subTest(scheme=scheme), self.proxy(scheme,mode="stall") as p:
                with self.session(proxy=p.url) as s:
                    with self.assertRaises(FastlsTimeoutError):s.get(self.fixture["http"],timeout=Timeout(total=3,connect=.15))
                    p.wait_idle()

    def test_large_upload_download_sse_and_duplex(self):
        expected=hashlib.sha256(SLAB*1024).hexdigest()
        for scheme in ("http","https","socks5","socks5h"):
            with self.subTest(scheme=scheme), self.proxy(scheme,auth=True) as p:
                with self.session(proxy=p.url,timeout=60) as s:
                    digest=hashlib.sha256();size=0
                    with s.stream("GET",self.fixture["https2"]+"/bytes?n=67108864") as r:
                        for part in r.iter_bytes():digest.update(part);size+=len(part)
                    self.assertEqual((size,digest.hexdigest()),(67108864,expected))
                    r=s.post(self.fixture["https2"]+"/upload",data=iter([SLAB]*1024))
                    self.assertEqual(r.json()["bytes"],67108864)
                    self.assertEqual(r.json()["sha256"],expected)
                async def run():
                    async with self.session(async_=True,proxy=p.url,timeout=60) as s:
                        async def large_source():
                            for _ in range(1024): yield SLAB
                        upload=await s.post(self.fixture["https2"]+"/upload",data=large_source())
                        self.assertEqual(upload.json()["sha256"],expected)
                        digest=hashlib.sha256();size=0
                        async with s.stream("GET",self.fixture["https2"]+"/bytes?n=67108864") as response:
                            async for part in response.aiter_bytes():
                                digest.update(part);size+=len(part)
                                if size==len(part):
                                    await asyncio.sleep(.05)
                                    self.assertLessEqual(s.stats()["buffered_body_bytes"],3*65536)
                        self.assertEqual((size,digest.hexdigest()),(67108864,expected))
                        async with s.sse("GET",self.fixture["https2"]+"/sse-vectors") as events:
                            received=[e async for e in events]
                            self.assertTrue(received)
                        async def source():yield b"proxy-duplex"
                        async with s.stream("POST",self.fixture["https2"]+"/duplex",data=source(),duplex=True) as r:
                            self.assertEqual(b"".join([b async for b in r.aiter_bytes()]),b"ready\nproxy-duplex")
                asyncio.run(run());p.wait_idle()

    def test_cancel_one_h2_stream_keeps_shared_tunnel(self):
        for scheme in ("http","https","socks5","socks5h"):
            with self.proxy(scheme) as p:
                async def run():
                    async with self.session(async_=True,proxy=p.url) as s:
                        url=self.fixture["https2"]
                        initial=(await s.get(url+"/bytes?n=4")).headers["X-Test-Connection"]
                        async with s.stream("GET",url+"/sse-hold?id="+uuid.uuid4().hex) as held:
                            parts=held.aiter_bytes();await anext(parts)
                            held.cancel()
                            with self.assertRaises(FastlsError):await anext(parts)
                        healthy=await s.get(url+"/bytes?n=4")
                        self.assertEqual(initial,healthy.headers["X-Test-Connection"])
                        self.assertEqual(p.accepted,1)
                asyncio.run(run());p.wait_idle()

    def test_https_proxy_trust_is_independent_and_pool_isolated(self):
        with self.proxy("https",auth=True) as p:
            url=self.fixture["https2"]+"/bytes?n=4"
            with self.session(proxy=p.url) as s:
                original=s.get(url).headers["X-Test-Connection"]
                for wrong in (self.fixture["ca_file"],self.fixture["wrong_ca_file"],None):
                    with self.assertRaises(FastlsError):s.get(url,proxy_ca_file=wrong)
                    self.assertEqual(original,s.get(url).headers["X-Test-Connection"])
                direct=s.get(url,proxy=None)
                self.assertNotEqual(original,direct.headers["X-Test-Connection"])
                s._ca_pem=Path(self.fixture["proxy_ca_file"]).read_text()
                with self.assertRaises(FastlsError):s.get(url)
            p.wait_idle()
            with self.session(proxy_ca_file=None) as s:
                self.assertEqual(s.get(url,proxy=p.url,proxy_ca_file=self.fixture["proxy_ca_file"]).status_code,200)
            p.wait_idle()

    def test_https_proxy_hostname_is_verified(self):
        with self.proxy("https",certfile=self.fixture["proxy_wrong_host_cert_file"],keyfile=self.fixture["proxy_wrong_host_key_file"]) as p:
            with self.session(proxy=p.url,proxy_ca_file=self.fixture["proxy_wrong_host_ca_file"]) as s:
                with self.assertRaises(FastlsError) as caught:s.get(self.fixture["https2"])
                self.assertRegex(str(caught.exception),r"IP SAN|valid for|hostname")
            p.wait_idle();self.assertEqual(p.targets,[])

    def test_https_proxy_does_not_fallback_to_cleartext(self):
        with self.proxy("http") as p:
            with self.session(proxy=p.url.replace("http://","https://")) as s:
                with self.assertRaises(FastlsError):s.get(self.fixture["https2"])
            p.wait_idle();self.assertEqual(p.accepted,1);self.assertEqual(p.targets,[])

    def test_https_proxy_invalid_ca_rejected_without_request_leak(self):
        with tempfile.TemporaryDirectory() as directory, self.proxy("https") as p:
            ca=Path(directory)/"ca.pem";ca.write_text("")
            with self.assertRaises(ValueError):self.session(proxy=p.url,proxy_ca_file=ca)
            with self.session(proxy=p.url) as s:
                with self.assertRaises(ValueError):s.get(self.fixture["https2"],proxy_ca_file=ca)
                ca.write_text("not a certificate")
                with self.assertRaises(FastlsError):s.get(self.fixture["https2"],proxy_ca_file=ca)
                self.assertEqual(s.stats()["open_requests"],0)
            self.assertEqual(p.accepted,0)


if __name__=="__main__":unittest.main(verbosity=2,testRunner=unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult))
