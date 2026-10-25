"""HTTP/WS shared session cookies against the independent wire fixture."""
import asyncio
import json
import unittest
import uuid
from unittest.mock import patch
from test_stability import FixtureCase, EvidenceResult
from fastls import FastlsError, Session
from proxy_lab import ProxyLab

class WebSocketCookies(FixtureCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.metrics['label']='ws-cookies-'+cls.metrics['label']

    def url(self,key='https2',path='/ws'):
        return self.fixture[key].replace('http','ws',1)+path

    def inspect(self,s,key='https2',path='/ws',**options):
        with s.websocket(self.url(key,path),params={'mode':'inspect'},**options) as ws:
            ws.send('inspect')
            return json.loads(ws.recv())['headers'].get('Cookie',[])

    def test_http_login_to_ws_all_protocols(self):
        with self.session() as s:
            for key in ('http','https1','https2'):
                s.get(self.fixture[key]+'/cookie-set',params={'name':'login','value':'yes'})
                with s.websocket(self.url(key),params={'mode':'cookie-auth'}) as ws:
                    ws.send('authenticated');self.assertEqual(ws.recv(),'authenticated')
                self.assertEqual(self.inspect(s,key),['login=yes'])

    def test_ws_set_cookie_to_http_and_delete(self):
        with self.session() as s:
            for key in ('http','https1','https2'):
                with s.websocket(self.url(key),params={'set_cookie':['fresh=one; Path=/; HttpOnly','second=two; Path=/']}) as ws:
                    self.assertEqual(ws.headers.get_list('Set-Cookie'),['fresh=one; Path=/; HttpOnly','second=two; Path=/'])
                header=s.get(self.fixture[key]+'/inspect').json()['headers']['Cookie'][0]
                self.assertIn('fresh=one',header);self.assertIn('second=two',header)
                with s.websocket(self.url(key),params={'set_cookie':'fresh=; Max-Age=0; Path=/'}):pass
                self.assertIsNone(s.cookies.get('fresh',url=self.fixture[key]));s.cookies.clear()

    def test_domain_path_secure_and_duplicate_names(self):
        with self.session() as s:
            s.get(self.fixture['https1']+'/cookie-fields')
            s.cookies.set('dup','root',url=self.fixture['https1'],path='/')
            s.cookies.set('dup','private',url=self.fixture['https1'],path='/private')
            plain='; '.join(self.inspect(s,'http'))
            self.assertIn('root=one',plain);self.assertNotIn('secure=four',plain);self.assertNotIn('private=three',plain)
            secure='; '.join(self.inspect(s,'https1','/private/ws'))
            self.assertIn('secure=four',secure);self.assertIn('private=three',secure);self.assertLess(secure.index('dup=private'),secure.index('dup=root'));self.assertNotIn('foreign=bad',secure)
            self.assertEqual(self.inspect(s,'http',headers={'Cookie':''}),[''])
            self.assertEqual(s.cookies.get_dict(self.fixture['http'].replace('127.0.0.1','localhost')), {})

    def test_explicit_cookie_headers_override_and_empty(self):
        with self.session(headers={'Cookie':'session=manual'}) as s:
            s.cookies.set('stored','yes',url=self.fixture['https2'],path='/')
            self.assertEqual(self.inspect(s),['session=manual'])
            self.assertEqual(self.inspect(s,headers={'cOOkIe':'request=only'}),['request=only'])
            self.assertEqual(self.inspect(s,headers={'Cookie':''}),[''])
            with s.websocket(self.url(),params={'set_cookie':'returned=yes; Path=/'},headers={'Cookie':'only=explicit'}):pass
            self.assertEqual(s.cookies.get('returned',url=self.fixture['https2']),'yes')

    def test_disabled_store_and_manual_mode(self):
        with self.session(cookies=False) as s:
            s.get(self.fixture['https2']+'/cookie-set')
            self.assertEqual(self.inspect(s),[])
            with s.websocket(self.url(),params={'set_cookie':'ignored=yes; Path=/'}):pass
            self.assertEqual(s.cookies.get_list(self.fixture['https2']),[])
            self.assertEqual(self.inspect(s,headers={'Cookie':'manual=yes'}),['manual=yes'])
        with self.session() as s:
            s.cookies.set('seed','yes',url=self.fixture['https2'],path='/')
            self.assertEqual(self.inspect(s,cookie_mode='manual'),[])
            with s.websocket(self.url(),cookie_mode='manual',params={'set_cookie':'ignored=yes; Path=/'}):pass
            self.assertEqual(s.cookies.get_dict(self.fixture['https2']),{'seed':'yes'})

    def test_failed_http_handshakes_update_without_redirect(self):
        with self.session() as s:
            for mode in ('cookie-reject','cookie-redirect'):
                with self.assertRaises(FastlsError):s.ws_connect(self.url(),params={'mode':mode,'set_cookie':'error_cookie='+mode+'; Path=/'})
                self.assertEqual(s.cookies.get('error_cookie',url=self.fixture['https2']),mode)
                self.assertEqual(s.stats()['open_requests'],0)

    def test_invalid_upgrade_and_tls_do_not_write(self):
        with self.session() as s:
            for mode in ('cookie-bad-accept','cookie-compression'):
                with self.assertRaises(FastlsError):s.ws_connect(self.url(),params={'mode':mode,'set_cookie':'poison=no; Path=/'})
                self.assertIsNone(s.cookies.get('poison',url=self.fixture['https2']))
        with Session(ca_file=self.fixture['wrong_ca_file']) as s:
            with self.assertRaises(FastlsError):s.ws_connect(self.url(),params={'set_cookie':'poison=no; Path=/'})
            self.assertEqual(s.cookies.get_list(self.fixture['https2']),[])

    def test_session_isolation_and_concurrent_http_ws(self):
        async def run():
            async with self.session(async_=True) as s, self.session(async_=True) as other:
                async def pair(index):
                    await s.get(self.fixture['https2']+'/cookie-set',params={'name':'http'+str(index),'value':'yes'})
                    async with s.websocket(self.url(),params={'set_cookie':'ws'+str(index)+'=yes; Path=/'}):pass
                await asyncio.gather(*(pair(i) for i in range(12)))
                values=s.cookies.get_dict(self.fixture['https2']);self.assertEqual(len(values),24)
                self.assertTrue(all(value=='yes' for value in values.values()))
                self.assertEqual(other.cookies.get_list(self.fixture['https2']),[])
        asyncio.run(run())

    def test_all_proxies_authentication_and_cookie_isolation(self):
        for scheme in ('http','https','socks5','socks5h'):
            config=dict(certfile=self.fixture['proxy_cert_file'],keyfile=self.fixture['proxy_key_file']) if scheme=='https' else {}
            with ProxyLab(scheme,auth=True,**config) as proxy:
                with self.session(proxy=proxy.url,proxy_ca_file=self.fixture['proxy_ca_file']) as s:
                    s.get(self.fixture['https2']+'/cookie-set',params={'name':'login','value':'yes'})
                    with s.websocket(self.url(),params={'mode':'inspect','set_cookie':'from_ws=yes; Path=/'}) as ws:
                        ws.send('inspect');headers=json.loads(ws.recv())['headers']
                        self.assertEqual(headers['Cookie'],['login=yes']);self.assertNotIn('Proxy-Authorization',headers)
                    self.assertEqual(s.cookies.get('from_ws',url=self.fixture['https2']),'yes')
                    s.close_idle_connections()
                proxy.wait_idle()

    def test_cancelled_handshake_and_closed_session_recover(self):
        async def run():
            async with self.session(async_=True) as s:
                identifier=uuid.uuid4().hex
                task=asyncio.create_task(s.ws_connect(self.url(),params={'mode':'handshake-stall','id':identifier,'set_cookie':'late=no; Path=/'}))
                await self.wait_state(identifier,'started');task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertEqual(s.cookies.get_list(self.fixture['https2']),[])
                ws=await s.ws_connect(self.url());pending=asyncio.create_task(ws.recv());await asyncio.sleep(.02)
                await s.aclose()
                with self.assertRaises(FastlsError):await pending
            self.idle()
        asyncio.run(run())

    def test_legacy_capability_gate_and_manual_escape(self):
        with self.session() as s:
            original=s._native.call
            def call(name,*args):
                result=original(name,*args)
                return [x for x in result if x not in ('websocket_cookies','websocket_cookies_v1','websocket_cookie_control_v1')] if name=='fastls_features' else result
            with patch.object(s._native,'call',call):
                with self.assertRaisesRegex(FastlsError,'newer'):s.ws_connect(self.url())
                self.assertEqual(s.stats()['open_requests'],0)
                self.assertEqual(self.inspect(s,cookie_mode='manual',headers={'Cookie':'manual=yes'}),['manual=yes'])
                self.assertEqual(s.get(self.fixture['https2']+'/status?code=204').status_code,204)

    def test_control_capability_alias_preserves_manual_optout(self):
        with self.session() as s:
            original=s._native.call
            def call(name,*args):
                result=original(name,*args)
                return [x for x in result if x not in ('websocket_cookies','websocket_cookies_v1')] if name=='fastls_features' else result
            with patch.object(s._native,'call',call):
                s.cookies.set('seed','private',url=self.fixture['https2'],path='/')
                self.assertEqual(self.inspect(s),['seed=private'])
                self.assertEqual(self.inspect(s,cookie_mode='manual'),[])
                with s.websocket(self.url(),cookie_mode='manual',params={'set_cookie':'ignored=no; Path=/'}):pass
                self.assertEqual(s.cookies.get_dict(self.fixture['https2']),{'seed':'private'})

    def test_automatic_only_library_rejects_manual_before_handle(self):
        with self.session() as s:
            original=s._native.call
            def call(name,*args):
                result=original(name,*args)
                return [x for x in result if x not in ('websocket_cookies','websocket_cookie_control_v1')] if name=='fastls_features' else result
            with patch.object(s._native,'call',call):
                s.cookies.set('seed','private',url=self.fixture['https2'],path='/')
                with self.assertRaisesRegex(FastlsError,'cannot disable'):s.ws_connect(self.url(),cookie_mode='manual')
                with self.assertRaisesRegex(FastlsError,'newer'):s.ws_connect(self.url())
                self.assertEqual(s.stats()['open_requests'],0)
                self.assertEqual(s.cookies.get_dict(self.fixture['https2']),{'seed':'private'})

    def test_async_login_and_response_cookie_roundtrip(self):
        async def run():
            async with self.session(async_=True) as s:
                for key in ('http','https1','https2'):
                    await s.get(self.fixture[key]+'/cookie-set',params={'name':'login','value':'yes'})
                    async with s.websocket(self.url(key),params={'mode':'cookie-auth','set_cookie':'roundtrip=yes; Path=/'}) as ws:
                        await ws.send('authenticated');self.assertEqual(await ws.recv(),'authenticated')
                    self.assertIn('roundtrip=yes',(await s.get(self.fixture[key]+'/inspect')).json()['headers']['Cookie'][0])
                    s.cookies.clear()
        asyncio.run(run())

    def test_async_manual_mode_and_explicit_headers(self):
        async def run():
            async with self.session(async_=True,headers={'Cookie':'explicit=yes'}) as s:
                s.cookies.set('seed','yes',url=self.fixture['https2'],path='/')
                async with s.websocket(self.url(),cookie_mode='manual',params={'mode':'inspect','set_cookie':'ignored=no; Path=/'}) as ws:
                    await ws.send('inspect');self.assertEqual(json.loads(await ws.recv())['headers']['Cookie'],['explicit=yes'])
                self.assertEqual(s.cookies.get_dict(self.fixture['https2']),{'seed':'yes'})
                async with s.websocket(self.url(),headers={'Cookie':''},params={'mode':'inspect'}) as ws:
                    await ws.send('inspect');self.assertEqual(json.loads(await ws.recv())['headers']['Cookie'],[''])
        asyncio.run(run())

    def test_invalid_cookie_modes_fail_before_handle(self):
        with self.session() as s:
            for mode in (None,True,1,'automatic','',[]):
                with self.assertRaises(ValueError):s.ws_connect(self.url(),cookie_mode=mode)
                self.assertEqual(s.stats()['open_requests'],0)

if __name__=='__main__':
    unittest.main(verbosity=2,testRunner=unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult))
