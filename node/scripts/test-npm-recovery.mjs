import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { recovery, publishSet, verifyRegistryProvenance, publisherIdentity, publicationState } from './npm-recovery.mjs';

const reply = (status, body = {}) => new Response(JSON.stringify(body), { status });
const noWait = async () => {};
test('pending uploads resume read-only without resetting absolute time or republishing', async () => {
  const items = [{ name: 'native', version: '1.0.0', sha256: 'a'.repeat(64), url: 'native' }];
  const identity = { producer: 'a'.repeat(40), publisher: 'b'.repeat(40) };
  const state = publicationState(items, identity, undefined, { now: () => 100, attempt: '1/1' });
  let uploads = 0;
  await assert.rejects(publishSet(items, { state, now: () => 100, read: async (_url, options) => {
    if (options.missing) return null;
    throw Object.assign(Error('pending'), { code: 'registry-readback-pending' });
  }, publish: async () => { uploads++; return { status: 0 }; }, verify: async () => ({ verified: true }) }));
  assert.equal(state.items[0].state, 'uploaded-pending');
  const resumed = publicationState(items, identity, state, { now: () => 200, attempt: '1/2' });
  assert.equal(resumed.deadline, state.deadline);
  await publishSet(items, { state: resumed, confirmOnly: true,
    read: async () => ({}), publish: async () => { uploads++; throw Error('read-only mutation'); },
    verify: async () => ({ verified: true }) });
  assert.equal(uploads, 1);
  assert.equal(resumed.items[0].state, 'verified');
  assert.throws(() => publicationState(items, identity, resumed, { now: () => state.deadline, attempt: '1/3' }), /deadline/);
  assert.throws(() => publicationState(items, { producer: 'changed' }, state, { now: () => 200, attempt: '1/2' }), /identity/);
  assert.throws(() => publicationState(items, identity, resumed, { now: () => 200, attempt: '1/2' }), /attempt/);
});

test('three-attempt and read budgets survive fresh recovery instances', async () => {
  const item = { name: 'native', sha256: 'a'.repeat(64), version: '1.0.0' };
  let state = publicationState([item], {}, undefined, { now: () => 0, attempt: '1/1' });
  let requests = 0;
  for (let attempt = 1; attempt <= 3; attempt++) {
    if (attempt > 1) state = publicationState([item], {}, state, { now: () => 0, attempt: `1/${attempt}` });
    const read = recovery({ state, now: () => 0, sleep: noWait, request: async () => { requests++; return reply(404); } });
    await assert.rejects(read('https://registry.npmjs.org/native', { waitMissing: true }), /budget/);
  }
  assert.equal(requests, 15);
  await assert.rejects(recovery({ state, now: () => 0, sleep: noWait, request: async () => { requests++; return reply(200); } })(
    'https://registry.npmjs.org/native', { waitMissing: true }), /budget/);
  assert.equal(requests, 15);
  assert.throws(() => publicationState([item], {}, state, { now: () => 0, attempt: '2/1' }), /attempt/);
  assert.throws(() => publicationState([item], {}, { ...state, deadline: state.deadline + 1 },
    { now: () => 0, attempt: '2/1' }), /identity/);
});
test('bounded read retries propagation, network failures and temporary server errors', async () => {
  for (const fault of [404,408,429,500,502,503,504,'network']) {
    let calls = 0;
    const read = recovery({ sleep: noWait, request: async () => {
      if (++calls === 1) { if (fault === 'network') throw new TypeError('fetch failed'); return reply(fault); }
      return reply(200, { visible: true });
    } });
    assert.equal((await read('https://registry.npmjs.org/', { waitMissing: true })).visible, true);
    assert.equal(calls, 2);
  }
});
test('authentication, permission, conflict and malformed payload failures are immediate', async () => {
  for (const status of [401,403,409,400]) {
    let calls = 0;
    await assert.rejects(recovery({ request: async () => { calls++; return reply(status); }, sleep: noWait })('https://registry.npmjs.org/'));
    assert.equal(calls, 1);
  }
  await assert.rejects(recovery({ request: async () => new Response('{', { status: 200 }) })('https://registry.npmjs.org/'));
});
test('404 absence is distinct from expected propagation and never spins indefinitely', async () => {
  let calls = 0;
  const read = recovery({ request: async () => { calls++; return reply(404); }, sleep: noWait });
  assert.equal(await read('url', { missing: true }), null);
  await assert.rejects(read('url', { waitMissing: true }), /budget/);
  assert.equal(calls, 6);
  let now = 0;
  await assert.rejects(recovery({ now: () => now, budget: 10, sleep: async ms => { now += ms; },
    request: async () => reply(503) })('url'), /budget/);
});
test('real local HTTP registry fixture exposes an expected temporary 404', async t => {
  let requests = 0;
  const server = createServer((req,res) => { res.writeHead(++requests < 3 ? 404 : 200, { 'Content-Type': 'application/json' }); res.end('{"ok":true}'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  assert.equal((await recovery({ sleep: noWait })(`http://127.0.0.1:${server.address().port}`, { waitMissing: true })).ok, true);
  assert.equal(requests, 3);
});

test('post-upload reads bypass a cached preflight 404 without weakening verification', async () => {
  const original = 'https://registry.npmjs.org/%40fastls%2Ffastls/0.16.0';
  let uploaded = false;
  const seen = [];
  const read = recovery({ now: () => 123, sleep: noWait, request: async url => {
    seen.push(String(url));
    if (String(url) === original) return reply(404);
    const fresh = new URL(url);
    assert.equal(fresh.origin + fresh.pathname, new URL(original).origin + new URL(original).pathname);
    assert.equal(fresh.searchParams.get('fastls_readback'), '123-0');
    assert.ok(uploaded);
    return reply(200, { name: 'expected', version: '0.16.0' });
  } });
  let verified = false;
  await publishSet([{ name: 'expected', url: original }], { read,
    publish: async () => { uploaded = true; return { status: 0 }; },
    verify: async (_item, metadata) => { assert.equal(metadata.name, 'expected'); verified = true; } });
  assert.equal(verified, true);
  assert.equal(seen.length, 2);
});

test('failure diagnostics retain phase and final status while failed bodies are released', async () => {
  let cancelled = 0;
  const read = recovery({ sleep: noWait, maxAttempts: 2, request: async () => ({
    ok: false, status: 503, body: { cancel: async () => { cancelled++; } },
  }) });
  await assert.rejects(read('https://registry.npmjs.org/', { waitMissing: true }),
    error => error.name === 'RecoveryError' && /readback; HTTP 503/.test(error.message));
  assert.equal(cancelled, 2);
  let calls = 0;
  await assert.rejects(recovery({ sleep: noWait, request: async () => { calls++; return reply(401); } })(
    'https://registry.npmjs.org/', { waitMissing: true }), /rejected \(401\)/);
  assert.equal(calls, 1);
});

test('post-upload propagation uses the existing time budget without more read attempts', async () => {
  let clock = 0, calls = 0;
  const waits = [];
  const read = recovery({ now: () => clock,
    sleep: async ms => { waits.push(ms); clock += ms; },
    request: async () => { calls++; return clock < 30000 ? reply(404) : reply(200, { visible: true }); },
  });
  assert.equal((await read('https://registry.npmjs.org/package/1.0.0', { waitMissing: true })).visible, true);
  assert.deepEqual(waits, [5000, 10000, 20000]);
  assert.equal(calls, 4);
  assert.ok(clock <= 480000);
  let shortClock = 0;
  await assert.rejects(recovery({ now: () => shortClock, budget: 4000,
    sleep: async ms => { shortClock += ms; }, request: async () => reply(404),
  })('https://registry.npmjs.org/package/1.0.0', { waitMissing: true }), /budget/);
  assert.equal(shortClock, 4000);
});

test('canonical package documents distinguish absent versions and verify exact new metadata', async () => {
  const item = { name: '@fastls/fastls', version: '0.16.0', url: 'https://registry.npmjs.org/%40fastls%2Ffastls' };
  const version = { name: item.name, version: item.version, dist: { integrity: 'original' } };
  let uploaded = false, verified = false, calls = 0;
  const read = recovery({ sleep: noWait, request: async url => {
    calls++;
    assert.equal(new URL(url).pathname, '/%40fastls%2Ffastls');
    return reply(200, { name: item.name, versions: uploaded ? { [item.version]: version } : {} });
  } });
  await publishSet([item], { read, publish: async () => { uploaded = true; return { status: 0 }; },
    verify: async (_item, metadata) => { assert.deepEqual(metadata, version); verified = true; } });
  assert.equal(verified, true); assert.equal(calls, 2);
  for (const body of [{ name: 'other', versions: {} }, { name: item.name }, { name: item.name, versions: [] }]) {
    await assert.rejects(recovery({ request: async () => reply(200, body) })(item.url,
      { missing: true, packageName: item.name, packageVersion: item.version }), /document rejected/);
  }
  for (const metadata of [null, [], {}, { name: item.name, version: 'other' }]) {
    await assert.rejects(recovery({ request: async () => reply(200, { name: item.name, versions: { [item.version]: metadata } }) })(item.url,
      { missing: true, packageName: item.name, packageVersion: item.version }), /version document rejected/);
  }
});

test('missing exact version in a canonical document remains bounded', async () => {
  let clock = 0, calls = 0;
  const read = recovery({ now: () => clock, budget: 4000,
    sleep: async ms => { clock += ms; }, request: async () => { calls++; return reply(200, { name: 'p', versions: {} }); } });
  await assert.rejects(read('https://registry.npmjs.org/p',
    { waitMissing: true, packageName: 'p', packageVersion: '1.0.0' }), /version absent in package document/);
  assert.equal(calls, 1); assert.equal(clock, 4000);
});

function fixture() {
  const item = { name: '@fastls/fastls', version: '0.15.0' }, bytes = Buffer.from('original archive');
  const metadata = { ...item, dist: { integrity: 'sha512-' + createHash('sha512').update(bytes).digest('base64'),
    attestations: { url: 'https://registry.npmjs.org/-/npm/v1/attestations/@fastls%2ffastls@0.15.0' } } };
  const statement = { predicateType: 'https://slsa.dev/provenance/v1',
    subject: [{ name: 'pkg:npm/%40fastls/fastls@0.15.0', digest: { sha512: createHash('sha512').update(bytes).digest('hex') } }],
    predicate: { buildDefinition: { externalParameters: { workflow: { repository: 'https://github.com/FastTLS/fastls-ffi',
      path: '.github/workflows/npm-publish.yml', ref: 'refs/heads/main' } },
    internalParameters: { github: { repository_id: '1430610790', repository_owner_id: '250931946', event_name: 'workflow_dispatch' } },
    resolvedDependencies: [{ uri: 'git+https://github.com/FastTLS/fastls-ffi@refs/heads/main', digest: { gitCommit: 'a'.repeat(40) } }] },
    runDetails: { builder: { id: 'https://github.com/actions/runner/github-hosted' } } } };
  const verify = async (_bundle, options) => {
    assert.equal(options.certificateIdentityURI, publisherIdentity);
    assert.equal(options.certificateIssuer, 'https://token.actions.githubusercontent.com');
  };
  const check = async (certify = async () => {}, verifier = verify) => verifyRegistryProvenance(item, metadata, bytes,
    async () => ({ attestations: [{ predicateType: statement.predicateType,
      bundle: { dsseEnvelope: { payload: Buffer.from(JSON.stringify(statement)).toString('base64') } } }] }), certify, verifier);
  return { item, bytes, metadata, statement, check };
}
test('existing integrity alone cannot authorize a skip; signer and source must pass', async () => {
  const f = fixture();
  assert.equal((await f.check()).verified, true);
  await assert.rejects(f.check(async () => { throw Error('unrelated source'); }), /source/);
  await assert.rejects(f.check(async () => {}, async () => { throw Error('invalid cryptographic signature'); }), /signature/);
  f.metadata.dist.integrity = 'different'; await assert.rejects(f.check(), /integrity/);
});
test('signed subject, workflow, repository, branch, runner and commit substitutions fail', async () => {
  const mutations = [f => { f.statement.subject[0].digest.sha512 = '0'.repeat(128); },
    f => { f.statement.subject[0].name = 'pkg:npm/other@0.15.0'; },
    f => { f.statement.predicate.buildDefinition.externalParameters.workflow.ref = 'refs/pull/1/merge'; },
    f => { f.statement.predicate.buildDefinition.internalParameters.github.repository_id = '1'; },
    f => { f.statement.predicate.buildDefinition.externalParameters.workflow.path = 'other.yml'; },
    f => { f.statement.predicate.runDetails.builder.id = 'self-hosted'; },
    f => { f.statement.predicate.buildDefinition.resolvedDependencies[0].digest.gitCommit = 'invalid'; },
    f => { f.metadata.dist.attestations.url = 'https://attacker.example/'; }];
  for (const mutate of mutations) { const f = fixture(); mutate(f); await assert.rejects(f.check()); }
});
test('all six are preflighted; existing exact packages skip while the main uploads last', async () => {
  const items = Array.from({ length: 6 }, (_, i) => ({ name: i === 5 ? 'main' : `native${i}`, url: `${i}` }));
  const uploaded = [], records = [];
  await publishSet(items, { read: async (url, options) => options.waitMissing || Number(url) < 2 ? { url } : null,
    verify: async () => ({ verified: true }), publish: async i => { uploaded.push(i.name); return { status: 0 }; }, record: r => records.push(r) });
  assert.deepEqual(uploaded, ['native2','native3','native4','main']);
  assert.equal(records.length, 6);
  const mutations = [];
  await assert.rejects(publishSet(items, { read: async () => ({}), verify: async () => { throw Error('digest mismatch'); },
    publish: async i => mutations.push(i) }));
  assert.equal(mutations.length, 0);
});
test('409 requires matching provenance; partial failure preserves progress and never republishes blindly', async () => {
  const items = [{ name: 'first', url: 'first' }, { name: 'main', url: 'main' }], records = [];
  let uploads = 0;
  await assert.rejects(publishSet(items, { read: async (_url, options) => options.waitMissing ? {} : null,
    verify: async i => { if (i.name === 'main') throw Error('untrusted conflict'); return { verified: true }; },
    publish: async () => { uploads++; return { status: 409 }; }, record: r => records.push(r) }), /conflict/);
  assert.equal(uploads, 2); assert.equal(records.length, 1); assert.equal(records[0].name, 'first');
});
