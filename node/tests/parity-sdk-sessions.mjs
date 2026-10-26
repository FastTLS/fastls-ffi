import { parity, session, fixture, assert, Native } from './harness.mjs';
const p = (name, fn) => parity('test_sdk_sessions.py', name, fn);

p('test_native_context_has_no_cookie_store', async t => {
  const s = session(t), url = fixture.https2;
  await s.get(url + '/cookie-set?name=login&value=yes');
  const raw = Buffer.from(JSON.stringify({ action: 'get', url }));
  assert.throws(() => Native.load().call('session_cookies', s.handle, raw, raw.length), /no cookie store/);
  assert.equal(s.cookies.get('login', url), 'yes');
  s.cookies.clear();
  assert.equal((await s.get(url + '/inspect')).json().headers.Cookie, undefined);
});

p('test_sdk_redirect_limit_releases_handles_and_keeps_cookies', async t => {
  const s = session(t), url = fixture.https2;
  await assert.rejects(s.get(url + '/cookie-redirect', { maxRedirects: 0 }), /maximum redirects/);
  assert.equal(s.stats().open_requests, 0);
  assert.equal(s.cookies.get('first', url), 'one');
  assert.equal((await s.get(url + '/status?code=204')).status, 204);
});
