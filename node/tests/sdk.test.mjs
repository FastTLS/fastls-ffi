import assert from 'node:assert/strict';
import { test, before, after, afterEach } from 'node:test';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { resolve, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { writeFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import { createHash } from 'node:crypto';
import { monitorEventLoopDelay } from 'node:perf_hooks';

const require = createRequire(import.meta.url);
const repository = resolve(import.meta.dirname, '../..');
const entry = process.env.FASTLS_NODE_PACKAGE
  ? createRequire(resolve(process.env.FASTLS_NODE_PACKAGE, 'package.json')).resolve('@fasttls/fastls')
  : require.resolve('..');
const { Session, AbortError, FastlsError, Headers } = require(entry);
let fixture, processFixture, exitFixture, monitor;
let stderr = '';
const ownedSessions = new Set();
const evidence = { node: process.version, platform: process.platform, arch: process.arch,
  label: process.env.FASTLS_NODE_PACKAGE ? 'package' : 'source', tests: [], measurements: {} };

function session(t, options = {}) {
  const value = new Session({ caFile: fixture.ca_file, timeoutMs: 15000, ...options });
  ownedSessions.add(value);
  t.after(() => value.close());
  return value;
}

async function eventually(probe, message, timeout = 5000) {
  const deadline = performance.now() + timeout;
  while (performance.now() < deadline) {
    if (await probe()) return;
    await delay(20);
  }
  assert.fail(message);
}

async function control(path, id) {
  const response = await fetch(`${fixture.http}${path}?id=${id}`, { signal: AbortSignal.timeout(3000) });
  return response.json();
}

function check(name, fn) {
  test(name, { timeout: 60000 }, async t => {
    const started = performance.now();
    try {
      await fn(t);
      evidence.tests.push({ name, passed: true, ms: performance.now() - started });
    } catch (error) {
      evidence.tests.push({ name, passed: false, error: String(error) });
      throw error;
    }
  });
}

before(async () => {
  const executable = resolve(repository, 'build', process.platform === 'win32' ? 'ffi-fixture.exe' : 'ffi-fixture');
  processFixture = spawn(executable, [], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  exitFixture = once(processFixture, 'exit');
  processFixture.stderr.setEncoding('utf8').on('data', chunk => { stderr = (stderr + chunk).slice(-8192); });
  const lines = createInterface({ input: processFixture.stdout });
  try {
    const [line] = await Promise.race([
      once(lines, 'line'),
      exitFixture.then(() => { throw new Error(`fixture exited: ${stderr}`); }),
      delay(10000, null, { ref: false }).then(() => { throw new Error('fixture startup timed out'); }),
    ]);
    fixture = JSON.parse(line);
  } finally { lines.close(); }
  monitor = new Session();
});

afterEach(async () => {
  try {
    if (monitor) assert.equal(monitor.stats().open_requests, 0, 'SDK retained request handles after the test');
  } finally {
    for (const value of ownedSessions) value.close();
    ownedSessions.clear();
  }
  if (!monitor) return;
  await eventually(() => {
    const stats = monitor.stats();
    return stats.open_requests === 0 && stats.workers === 0 && stats.open_sessions === 1;
  }, `native resources leaked: ${JSON.stringify(monitor.stats())}`);
});

after(async () => {
  monitor?.close();
  if (processFixture) {
    processFixture.stdin.end();
    const timer = setTimeout(() => processFixture.kill(), 10000);
    try { await exitFixture; } finally { clearTimeout(timer); }
  }
  writeFileSync(resolve(repository, 'build', `node-${evidence.label}-${Date.now()}.json`), JSON.stringify(evidence, null, 2));
});

check('CommonJS and ESM share the same public classes', async () => {
  const esm = await import(pathToFileURL(resolve(dirname(entry), 'index.mjs')).href);
  assert.equal(esm.Session, Session);
  assert.equal(esm.AbortError, AbortError);
  const headers = new Headers({ 'Set-Cookie': ['a=1', 'b=2; Expires=Fri, 01 Jan 2100 00:00:00 GMT'] });
  const values = headers.getAll('SET-cookie');
  values.pop();
  assert.equal(headers.getAll('set-cookie').length, 2);
  assert.equal([...headers.multiItems()].length, 2);
});

check('verified HTTPS H1/H2, profile metadata, header overrides and CA isolation', async t => {
  for (const [target, expected] of [['https1', 'HTTP/1.1'], ['https2', 'HTTP/2.0']]) {
    const s = session(t, { headers: { 'User-Agent': 'default', 'X-Default': 'yes' } });
    const r = await s.get(`${fixture[target]}/headers`, { headers: { 'user-agent': 'node-sdk' } });
    assert.equal(r.status, 200);
    assert.equal(r.json().protocol, expected);
    assert.deepEqual(r.json().headers['User-Agent'], ['node-sdk']);
    assert.deepEqual(r.json().headers['X-Default'], ['yes']);
    assert.ok(Object.keys(r.match).length > 0);
    assert.ok(s.listProfiles().includes('chrome/156/windows'));
    s.close();
  }
  for (const caFile of [undefined, fixture.wrong_ca_file]) {
    const s = session(t, { caFile });
    await assert.rejects(s.get(`${fixture.https2}/headers`), /certificate|authority/i);
    s.close();
  }
});

check('binary upload/download, Unicode and request validation', async t => {
  const s = session(t);
  const binary = Buffer.from([0, 255, 128, 1, 0, 65]);
  assert.deepEqual((await s.post(`${fixture.https2}/echo`, { body: binary })).content, binary);
  assert.equal((await s.post(`${fixture.http}/echo`, { body: '你好，fastls' })).text(), '你好，fastls');
  await assert.rejects(s.get(`${fixture.http}/headers`, { profile: 'unknown/profile' }));
  await assert.rejects(s.get(`${fixture.http}/headers`, { timeoutMs: 0 }), /timeoutMs/);
  await assert.rejects(s.get(`${fixture.http}/headers`, { headers: { X: 123 } }), /strings/);
  await assert.rejects(s.get(`${fixture.http}/headers`, { signal: {} }), /AbortSignal/);
  assert.equal((await s.post(`${fixture.http}/upload`, { body: Buffer.alloc(512 * 1024 + 1) })).json().bytes, 512 * 1024 + 1);
  await assert.rejects(s.get('http://user:secret@localhost/'), /credentials/);
});

check('connection reuse, isolation and closing idle connections', async t => {
  for (const target of ['https1', 'https2']) {
    const s = session(t), other = session(t);
    const url = `${fixture[target]}/headers`;
    const first = (await s.get(url)).headers.get('x-test-connection');
    assert.equal((await s.get(url)).headers.get('x-test-connection'), first);
    assert.notEqual((await other.get(url)).headers.get('x-test-connection'), first);
    s.closeIdleConnections();
    assert.notEqual((await s.get(url)).headers.get('x-test-connection'), first);
    s.close(); other.close();
  }
});

check('multi-value headers, automatic cookies, redirects and session isolation', async t => {
  const s = session(t), other = session(t);
  const r = await s.get(`${fixture.https2}/cookie-fields`);
  assert.equal(r.headers.getAll('set-cookie').length, 5);
  assert.deepEqual(r.headers.getAll('x-list'), ['a, b', 'literal/,/value']);
  assert.equal(s.cookies.get('root', fixture.https2), 'one');
  assert.equal(other.cookies.get('root', fixture.https2), undefined);
  assert.equal(s.cookies.get('private', fixture.https2), undefined);
  assert.equal(s.cookies.get('private', fixture.https2 + '/private'), 'three');
  assert.equal(s.cookies.get('foreign', fixture.https2), undefined);
  const redirected = await s.get(`${fixture.https2}/cookie-redirect`);
  assert.match(redirected.json().headers.Cookie[0], /first=one/);
  assert.match(redirected.json().headers.Cookie[0], /second=two/);
  s.cookies.set('manual', 'saved', { url: fixture.https2, path: '/' });
  assert.match((await s.get(`${fixture.https2}/inspect`)).json().headers.Cookie[0], /manual=saved/);
  const explicit = (await s.get(`${fixture.https2}/inspect`, { headers: { Cookie: 'manual=override' } })).json();
  assert.deepEqual(explicit.headers.Cookie, ['manual=override']);
  s.cookies.delete('manual', { url: fixture.https2, path: '/' });
  assert.equal(s.cookies.get('manual', fixture.https2), undefined);
  s.cookies.clear();
  assert.deepEqual(s.cookies.getAll(fixture.https2), []);
  const disabled = session(t, { cookies: false });
  await disabled.get(`${fixture.https2}/cookie-set`);
  assert.deepEqual(disabled.cookies.getAll(fixture.https2), []);
  assert.throws(() => disabled.cookies.set('x', 'y', { url: fixture.https2 }), /disabled/);
});

check('redirect control and HTTP error statuses remain responses', async t => {
  const s = session(t);
  assert.equal((await s.get(`${fixture.https2}/redirect`, { allowRedirects: false })).status, 302);
  const r = await s.get(`${fixture.https2}/status?code=503`);
  assert.equal(r.status, 503);
  assert.equal(r.ok, false);
  for (const status of [204, 304]) assert.equal((await s.get(`${fixture.https2}/status?code=${status}`)).content.length, 0);
  await assert.rejects(s.get(`${fixture.https2}/redirect-loop`), /redirect/i);
});

check('first streamed chunk arrives before completion and break frees the handle', async t => {
  const s = session(t);
  const r = await s.stream('GET', `${fixture.https2}/cookie-hold?id=first-chunk`);
  assert.equal(s.cookies.get('streamed', fixture.https2), 'ready');
  for await (const chunk of r.body) {
    assert.equal(chunk.toString(), 'first');
    assert.equal((await control('/state', 'first-chunk')).done, false);
    break;
  }
  assert.equal(s.stats().open_requests, 0);
  await eventually(async () => (await control('/state', 'first-chunk')).done, 'server did not see early close');
  r.close(); r.close();
});

check('AbortSignal before start, before headers and during a pending read', async t => {
  const s = session(t);
  await assert.rejects(s.get(`${fixture.https2}/headers`, { signal: AbortSignal.abort('before start') }), { name: 'AbortError' });
  const beforeHeaders = new AbortController();
  const waiting = s.get(`${fixture.https2}/wait?id=abort-headers`, { signal: beforeHeaders.signal });
  const rejection = assert.rejects(waiting, error => error instanceof AbortError && error.cause === 'stop');
  await eventually(async () => (await control('/state', 'abort-headers')).started, 'request never started');
  beforeHeaders.abort('stop');
  await rejection;
  const controller = new AbortController();
  const response = await s.stream('GET', `${fixture.https2}/hold?id=abort-read`, { signal: controller.signal });
  const iterator = response.body[Symbol.asyncIterator]();
  assert.equal((await iterator.next()).value.toString(), 'first');
  const read = iterator.next();
  const readRejection = assert.rejects(read, { name: 'AbortError' });
  controller.abort();
  await readRejection;
});

check('timeouts and truncated/reset bodies are errors, never successful EOF', async t => {
  const s = session(t);
  await assert.rejects(s.get(`${fixture.https2}/wait?id=timeout-headers`, { timeoutMs: 100 }), /deadline|timeout/);
  const r = await s.stream('GET', `${fixture.https2}/hold?id=timeout-body`, { timeoutMs: 150 });
  await assert.rejects(r.read(), /deadline|timeout/);
  for (const target of ['https1', 'https2']) {
    for (const path of ['/truncated', '/abort']) {
      await assert.rejects(s.get(`${fixture[target]}${path}`), error => error instanceof FastlsError);
    }
  }
});

check('closing response/session rejects pending operations and close is idempotent', async t => {
  const s = session(t);
  const r = await s.stream('GET', `${fixture.https2}/hold?id=close-response`);
  const iterator = r.body[Symbol.asyncIterator]();
  await iterator.next();
  const pendingRead = assert.rejects(iterator.next(), /closed/);
  r.close();
  await pendingRead;
  const waitingHeaders = assert.rejects(s.get(`${fixture.https2}/wait?id=close-session`), /closed/);
  s.close(); s.close();
  await waitingHeaders;
  await assert.rejects(s.get(`${fixture.http}/headers`), /closed/);
  assert.throws(() => s.cookies.clear(), /closed/);
});

check('one body consumer, stable retained chunks and explicit cancel', async t => {
  const s = session(t);
  const r = await s.stream('GET', `${fixture.https2}/hold?id=single-reader`);
  const first = r.body[Symbol.asyncIterator]();
  const saved = (await first.next()).value;
  const second = r.body[Symbol.asyncIterator]();
  await assert.rejects(second.next(), /consumer/);
  r.cancel();
  await assert.rejects(first.next(), { name: 'AbortError' });
  assert.equal(saved.toString(), 'first');

  const completed = await s.stream('GET', `${fixture.https2}/hold?id=retained-chunk`);
  const reader = completed.body[Symbol.asyncIterator]();
  const retained = (await reader.next()).value;
  await (await fetch(`${fixture.http}/release?id=retained-chunk`)).text();
  assert.equal((await reader.next()).value.toString(), 'last!');
  assert.equal(retained.toString(), 'first');
  assert.equal((await reader.next()).done, true);
});

check('64MiB streaming has bounded buffering and preserves all bytes', async t => {
  const s = session(t, { timeoutMs: 30000 });
  const size = 64 * 1024 * 1024;
  const r = await s.stream('GET', `${fixture.https2}/bytes?n=${size}`);
  await delay(100);
  const buffered = s.stats().buffered_body_bytes;
  assert.ok(buffered <= 3 * 65536, `bridge buffered ${buffered} bytes`);
  const hash = createHash('sha256');
  let bytes = 0, saved;
  for await (const chunk of r.body) {
    saved ??= Buffer.from(chunk);
    hash.update(chunk);
    bytes += chunk.length;
  }
  const expected = createHash('sha256');
  const slab = Buffer.alloc(65536);
  for (let i = 0; i < slab.length; i += 4) slab.set([0, 1, 128, 255], i);
  for (let i = 0; i < size; i += slab.length) expected.update(slab);
  assert.equal(bytes, size);
  assert.equal(hash.digest('hex'), expected.digest('hex'));
  assert.deepEqual(saved, slab.subarray(0, saved.length));
  evidence.measurements.streaming = { bytes, bufferedBeforeConsume: buffered };
});

check('buffered responses reject more than 32MiB and release resources', async t => {
  const s = session(t, { timeoutMs: 30000 });
  await assert.rejects(s.get(`${fixture.https2}/bytes?n=${33 * 1024 * 1024}`), /32MiB/);
});

check('128 mixed concurrent requests/cancellations keep event loop responsive', async t => {
  const s = session(t);
  const histogram = monitorEventLoopDelay({ resolution: 10 });
  histogram.enable();
  let ticks = 0;
  const heartbeat = setInterval(() => ticks++, 10);
  try {
    const tasks = Array.from({ length: 128 }, (_, index) => {
      if (index % 2 === 0) return s.get(`${fixture.https2}/bytes?n=65536&id=mixed-${index}`).then(r => assert.equal(r.content.length, 65536));
      const controller = new AbortController();
      const task = assert.rejects(s.get(`${fixture.https2}/wait?id=mixed-${index}`, { signal: controller.signal }), { name: 'AbortError' });
      setTimeout(() => controller.abort(), 80);
      return task;
    });
    await Promise.all(tasks);
    assert.ok(ticks > 1, 'event loop heartbeat stopped');
    const maxDelayMs = histogram.max / 1e6;
    assert.ok(maxDelayMs < 1000, `event loop blocked for ${maxDelayMs}ms`);
    evidence.measurements.concurrency = { requests: 128, heartbeatTicks: ticks, maxDelayMs, p99DelayMs: histogram.percentile(99) / 1e6 };
  } finally { clearInterval(heartbeat); histogram.disable(); }
});
