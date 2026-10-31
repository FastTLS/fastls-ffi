import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { recovery, publishSet, verifyRegistryProvenance, publisherIdentity } from './npm-recovery.mjs';

const reply = (status, body = {}) => new Response(JSON.stringify(body), { status });
const noWait = async () => {};
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
