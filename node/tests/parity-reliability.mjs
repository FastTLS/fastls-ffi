import { parity, session, fixture, assert, sdk, root, readFileSync, randomUUID, control, delay, eventually, sha } from './harness.mjs';
import { test } from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
const p = (name, fn) => parity('test_reliability.py', name, fn);
const policy = options => new sdk.RetryPolicy({ backoffMs: 0, ...options });
const endpoint = (protocol = 'https2', options = {}) => {
  const id = options.id ?? randomUUID();
  return [fixture[protocol] + '/retry?' + new URLSearchParams({ ...options, id }), id];
};
const spec = () => new sdk.ProfileSpec(JSON.parse(readFileSync(resolve(root, 'tests/fixtures/profile_spec.json'), 'utf8')));

p('test_status_retry_is_opt_in_and_bounded', async t => {
  for (const protocol of ['http', 'https2', 'https3']) {
    const s = session(t, protocol === 'https3' ? { httpVersion: 'h3' } : {});
    let [url] = endpoint(protocol, { failures: 2 });
    assert.equal((await s.get(url)).status, 503);
    [url] = endpoint(protocol, { failures: 2 });
    assert.equal((await s.get(url, { retry: policy() })).json().attempts, 3);
    const [failure, id] = endpoint(protocol, { failures: 5 });
    assert.equal((await s.get(failure, { retry: policy() })).status, 503);
    assert.equal((await control('/state', id)).attempts, 3);
  }
});
p('test_non_idempotent_methods_require_authorization', async t => {
  assert.throws(() => policy({ methods: ['POST'] }));
  const s = session(t, { retry: policy() });
  let [url, id] = endpoint('https2', { failures: 1 });
  assert.equal((await s.post(url, { body: 'business' })).status, 503);
  assert.equal((await control('/state', id)).attempts, 1);
  [url] = endpoint('https2', { failures: 1 });
  const response = await s.post(url, { body: 'business', retry: policy({ methods: ['POST'], allowNonIdempotent: true }) });
  assert.equal(Buffer.from(response.json().body, 'base64').toString(), 'business');
  [url] = endpoint('https2', { failures: 1 });
  assert.equal((await s.get(url, { retry: null })).status, 503);
});
p('test_one_shot_sources_and_explicit_factories', async t => {
  const s = session(t, { retry: policy() });
  let [url, id] = endpoint('https2', { failures: 1 });
  assert.equal((await s.put(url, { body: [Buffer.from('one-shot')] })).status, 503);
  assert.equal((await control('/state', id)).attempts, 1);
  let made = 0;
  const bodyFactory = () => { made++; return [Buffer.from('factory')]; };
  [url] = endpoint('https2', { failures: 2 });
  assert.equal(Buffer.from((await s.put(url, { bodyFactory })).json().body, 'base64').toString(), 'factory');
  assert.equal(made, 3);
  made = 0;
  assert.equal(Buffer.from((await s.put(fixture.https2 + '/redirect-code?code=307', { bodyFactory })).json().body, 'base64').toString(), 'factory');
  assert.equal(made, 2);
});
p('test_large_scalars_can_replay_307', async t => {
  const s = session(t);
  for (const body of [Buffer.alloc(600 * 1024, 120), '中'.repeat(200 * 1024)]) {
    const response = await s.put(fixture.https2 + '/redirect-code?code=307', { body });
    assert.equal(response.status, 200);
    assert.deepEqual(Buffer.from(response.json().body, 'base64'), Buffer.from(body));
  }
});
p('test_retry_after_cap_and_total_deadline', async t => {
  const s = session(t);
  let [url] = endpoint('https2', { failures: 1, after: '99999999999999999999999' });
  const started = performance.now();
  assert.equal((await s.get(url, { retry: policy({ maxDelayMs: 20 }) })).status, 200);
  assert.ok(performance.now() - started < 2000);
  const [failure, id] = endpoint('https2', { failures: 1, after: '100' });
  await assert.rejects(s.get(failure, { timeoutMs: 100, retry: policy() }), sdk.FastlsTimeoutError);
  assert.equal((await control('/state', id)).attempts, 1);
});
p('test_disconnect_retry_and_body_read_failure_boundary', async t => {
  const s = session(t);
  let [url] = endpoint('http', { failures: 1, disconnect: 1 });
  assert.equal((await s.get(url, { retry: policy() })).status, 200);
  const [failure, id] = endpoint('https2', { truncate: 1 });
  await assert.rejects(s.get(failure, { retry: policy() }), sdk.FastlsError);
  assert.equal((await control('/state', id)).attempts, 1);
});
p('test_cancellation_and_close_interrupt_backoff', async t => {
  const s = session(t);
  let [url, id] = endpoint('https2', { failures: 100, after: '5' });
  const controller = new AbortController();
  const task = s.get(url, { signal: controller.signal, retry: policy() });
  const rejected = assert.rejects(task, sdk.AbortError);
  await eventually(async () => (await control('/state', id)).attempts === 1);
  controller.abort(); await rejected;
  assert.equal(s.stats().open_requests, 0);
  assert.equal((await control('/state', id)).attempts, 1);
  const closing = session(t);
  [url, id] = endpoint('https2', { failures: 100, after: '5' });
  const operation = closing.get(url, { retry: policy() });
  const closed = assert.rejects(operation, sdk.FastlsError);
  await eventually(async () => (await control('/state', id)).attempts === 1);
  await closing.closeAsync();
  await closed;
});
p('test_policy_certificate_and_unclassified_errors_never_retry', async t => {
  const retry = policy();
  for (const code of ['request_error', 'cancelled', 'address_denied', 'certificate_error', 'decode_error', 'invalid_configuration', undefined]) {
    const error = new sdk.FastlsError('redacted'); error.code = code;
    assert.equal(retry.eligible('GET', 0, true, error), false);
  }
  const denied = session(t, { addressPolicy: { deny_cidrs: ['127.0.0.0/8'] }, retry });
  await assert.rejects(denied.get(fixture.https2 + '/inspect'), error => error.code === 'address_denied');
  const untrusted = new sdk.Session({ retry });
  try { await assert.rejects(untrusted.get(fixture.https2 + '/inspect'), error => error.code === 'certificate_error'); }
  finally { await untrusted.closeAsync(); }
});
p('test_factory_failure_and_duplex_rejection_release_resources', async t => {
  const s = session(t, { retry: policy() });
  let made = 0;
  const bodyFactory = () => { if (++made === 2) throw new Error('factory failed'); return 'body'; };
  const [url, id] = endpoint('https2', { failures: 2 });
  await assert.rejects(s.put(url, { bodyFactory }), /factory failed/);
  assert.equal((await control('/state', id)).attempts, 1);
  assert.equal(s.stats().open_requests, 0);
  await assert.rejects(s.get(url, { duplex: true }), TypeError);
  await assert.rejects(s.get(url, { bodyFactory, body: 'conflict' }), TypeError);
});
p('test_sync_close_and_concurrent_backoff_cleanup', async t => {
  const s = session(t), owners = [];
  for (let index = 0; index < 20; index++) {
    const [url, id] = endpoint('https2', { failures: 100, after: '5' });
    const controller = new AbortController();
    const task = s.get(url, { retry: policy(), signal: controller.signal });
    owners.push({ id, controller, rejected: assert.rejects(task, sdk.AbortError) });
  }
  try {
    await eventually(async () => (await Promise.all(owners.map(item => control('/state', item.id)))).every(state => state.attempts === 1));
    assert.equal((await s.get(fixture.https2 + '/inspect')).status, 200);
  } finally {
    for (const item of owners) item.controller.abort();
    await Promise.all(owners.map(item => item.rejected));
  }
  assert.equal(s.stats().open_requests, 0);
});
p('test_redirect_chain_shares_retry_budget', async t => {
  const s = session(t, { retry: policy({ maxRetries: 2 }) });
  const [target, id] = endpoint('https2', { failures: 3 });
  const [initial, initialId] = endpoint('https2', { failures: 1, target });
  assert.equal((await s.get(initial)).status, 503);
  assert.equal((await control('/state', initialId)).attempts, 2);
  assert.equal((await control('/state', id)).attempts, 2);
});
p('test_retry_policy_snapshot_validation_and_header_timeouts', async () => {
  const methods = ['GET'], statuses = [503];
  const retry = policy({ methods, statuses }); methods.push('POST'); statuses.push(401);
  assert.deepEqual(retry.methods, ['GET']); assert.deepEqual(retry.statuses, [503]);
  for (const options of [{ maxRetries: true }, { backoffMs: NaN }, { statuses: [401] }, { methods: 'GET' }, { jitter: 1 }, { backoffMs: null }, { max_retries: 100 }]) assert.throws(() => policy(options));
  assert.equal(retry.eligible('GET', 0, true, new sdk.FastlsTimeoutError('headers', 'headers')), true);
  for (const phase of ['read', 'total']) assert.equal(retry.eligible('GET', 0, true, new sdk.FastlsTimeoutError(phase, phase)), false);
});
p('test_profile_bundle_roundtrip_pin_and_wire_replay', async t => {
  const bundle = sdk.ProfileBundle.create(spec(), { source: 'local-fixture' });
  const again = new sdk.ProfileBundle(bundle.dumps(), { sha256: bundle.sha256 });
  assert.deepEqual(again.spec.toJSON(), bundle.spec.toJSON());
  assert.equal(again.sha256, bundle.sha256);
  const s = session(t, { profileSpec: again.spec });
  assert.deepEqual((await s.get(fixture.wire + '/')).json().settings, bundle.spec.toJSON().http2.settings);
});
p('test_profile_store_atomic_activation_and_rollback', async () => {
  const first = sdk.ProfileBundle.create(spec(), { source: 'one' });
  const value = first.spec.toJSON(); value.http2.settings = [{ id: 2, value: 0 }, { id: 4, value: 65536 }];
  const second = sdk.ProfileBundle.create(new sdk.ProfileSpec(value), { source: 'two' });
  const directory = mkdtempSync(join(tmpdir(), 'fastls-profile-'));
  try {
    const store = new sdk.ProfileStore(join(directory, 'store'));
    const one = join(directory, 'one.json'), two = join(directory, 'two.json');
    first.export(one); second.export(two); store.install(one, first.sha256); store.install(two, second.sha256);
    assert.equal(store.load().sha256, second.sha256);
    assert.equal(store.rollback().sha256, first.sha256);
    const before = readFileSync(join(directory, 'store/state.json'));
    assert.throws(() => store.install(two, '0'.repeat(64)));
    assert.deepEqual(readFileSync(join(directory, 'store/state.json')), before);
    assert.throws(() => store.load('../outside'));
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
p('test_bundle_rejects_duplicates_corruption_unknown_fields_and_backend_gaps', async () => {
  const bundle = sdk.ProfileBundle.create(spec(), { source: 'fixture' }), data = JSON.parse(bundle.dumps());
  const invalid = [bundle.dumps().replace('"schema_version":1', '"schema_version":1,"schema_version":1'),
    JSON.stringify({ ...data, unknown: true }), JSON.stringify({ ...data, sha256: '0'.repeat(64) }), JSON.stringify({ ...data, profile_id: 'different' })];
  for (const text of invalid) assert.throws(() => new sdk.ProfileBundle(text, { sha256: bundle.sha256 }));
  const raw = '{"schema_version":1,"id":"broken","tls":{}}', digest = sha(raw);
  assert.throws(() => new sdk.ProfileBundle(JSON.stringify({ ...data, spec_json: raw, sha256: digest, profile_id: 'broken' }), { sha256: digest }), sdk.FastlsError);
  assert.throws(() => new sdk.ProfileBundle(bundle.dumps(), { sha256: 'not-a-pin' }));
  for (const raw of ['{"schema_version":1,"schema_version":1}', '['.repeat(70) + '0' + ']'.repeat(70), '{"schema_version":1.0}', '{"text":"\\ud800"}']) {
    const digest = sha(raw);
    assert.throws(() => new sdk.ProfileBundle(JSON.stringify({ ...data, spec_json: raw, sha256: digest }), { sha256: digest }));
  }
  assert.throws(() => new sdk.ProfileBundle(' '.repeat(512 * 1024 + 1), { sha256: bundle.sha256 }));
});

test('fetch retry and explicit factories work with shared and standalone owners', async t => {
  const s = session(t), retry = new sdk.RetryPolicy({ backoffMs: 0, methods: ['POST'], allowNonIdempotent: true });
  for (const owned of [false, true]) {
    const url = fixture.https2 + '/retry?' + new URLSearchParams({ id: randomUUID(), failures: '1' });
    const options = { method: 'POST', retry, bodyFactory: () => [Buffer.from('fresh-body')] };
    const response = owned ? await sdk.fetch(url, { ...options, caFile: fixture.ca_file }) : await s.fetch(url, options);
    assert.equal(Buffer.from((await response.json()).body, 'base64').toString(), 'fresh-body');
  }
  await assert.rejects(s.fetch(fixture.http + '/inspect', { bodyFactory: () => 'forbidden' }), /GET\/HEAD/);
  const request = new Request(fixture.http + '/inspect', { method: 'POST', body: 'one-shot' });
  await assert.rejects(s.fetch(request, { bodyFactory: () => 'conflict' }), /Request body/);
});

test('fetch abort during retry backoff leaves the owner usable', async t => {
  const s = session(t), retry = new sdk.RetryPolicy({ backoffMs: 10000, jitter: false });
  const signal = AbortSignal.timeout(100);
  await assert.rejects(s.fetch(fixture.https2 + '/status?code=503', { retry, signal }), sdk.AbortError);
  assert.equal(s.stats().open_requests, 0);
  assert.equal((await s.get(fixture.https2 + '/status?code=204')).status, 204);
});
