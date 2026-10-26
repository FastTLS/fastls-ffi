import { test } from 'node:test';
import { sdk, session, fixture, assert, monitor, eventually } from './harness.mjs';

test('fetch returns a standard streaming Response with fingerprint metadata', async () => {
  const response = await sdk.fetch(fixture.http + '/inspect', { headers: new Headers({ 'X-Fetch': 'yes' }) });
  assert.ok(response instanceof Response);
  assert.equal(response.status, 200);
  assert.equal(response.bodyUsed, false);
  assert.ok(response.match);
  assert.deepEqual((await response.json()).headers['X-Fetch'], ['yes']);
  assert.equal(response.bodyUsed, true);
  await assert.rejects(response.text(), TypeError);
  assert.equal(monitor.stats().open_requests, 0);
});

test('fetch accepts URL, Request, FormData and standard redirect modes', async () => {
  const request = new Request(fixture.http + '/inspect', { method: 'POST', body: 'hello' });
  const response = await sdk.fetch(request);
  assert.equal(Buffer.from((await response.json()).body, 'base64').toString(), 'hello');
  const form = new FormData(); form.set('greeting', 'hello');
  const submitted = await sdk.fetch(new URL(fixture.http + '/inspect'), { method: 'POST', body: form });
  assert.match(Buffer.from((await submitted.json()).body, 'base64').toString(), /hello/);
  const redirect = await sdk.fetch(fixture.http + '/redirect', { redirect: 'manual' });
  assert.equal(redirect.status, 302); await redirect.body.cancel();
  await assert.rejects(sdk.fetch(fixture.http + '/redirect', { redirect: 'error' }), /redirect/);
  const followed = await sdk.fetch(fixture.http + '/redirect');
  assert.equal(followed.redirected, true); await followed.arrayBuffer();
  assert.equal(monitor.stats().open_requests, 0);
});

test('fetch is stateless, Session.fetch shares SDK cookies and preserves its pool', async t => {
  const s = session(t);
  let response = await sdk.fetch(fixture.http + '/cookie-set?name=one&value=yes'); await response.text();
  response = await sdk.fetch(fixture.http + '/inspect');
  assert.equal((await response.json()).headers.Cookie, undefined);
  response = await s.fetch(fixture.http + '/cookie-set?name=login&value=yes'); await response.text();
  response = await s.fetch(fixture.http + '/inspect');
  assert.deepEqual((await response.json()).headers.Cookie, ['login=yes']);
  assert.equal((await s.get(fixture.http + '/status?code=204')).status, 204);
});

test('fetch cancels unread bodies and rejects unsupported options without leaking', async () => {
  const response = await sdk.fetch(fixture.http + '/bytes?n=65536');
  await response.body.cancel();
  const abort = new AbortController(); abort.abort();
  await assert.rejects(sdk.fetch(fixture.http + '/inspect', { signal: abort.signal }));
  await assert.rejects(sdk.fetch(fixture.http, { credentials: 'include' }), /credentials/);
  await assert.rejects(sdk.fetch(fixture.http, { dispatcher: {} }), /dispatcher/);
  const duringBody = new AbortController();
  const unread = await sdk.fetch(fixture.http + '/bytes?n=65536', { signal: duringBody.signal });
  duringBody.abort();
  await eventually(() => monitor.stats().open_sessions === 1, 'aborted fetch retained its temporary session');
  await assert.rejects(unread.text());
  await assert.rejects(sdk.fetch('file:///missing'), /cookie URL|HTTP|http/);
  assert.equal(monitor.stats().open_requests, 0);
});
