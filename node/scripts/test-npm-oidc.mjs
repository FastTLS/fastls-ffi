import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkTrust } from './check-npm-oidc.mjs';

const env = { GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: 'FastTLS/fastls-ffi',
  GITHUB_REF: 'refs/heads/main', GITHUB_SHA: 'a'.repeat(40),
  ACTIONS_ID_TOKEN_REQUEST_URL: 'https://pipelines.actions.githubusercontent.com/token',
  ACTIONS_ID_TOKEN_REQUEST_TOKEN: 'private-request-token' };
const claims = { iss: 'https://token.actions.githubusercontent.com', aud: 'npm:registry.npmjs.org',
  repository: env.GITHUB_REPOSITORY, ref: env.GITHUB_REF, sha: env.GITHUB_SHA, environment: 'npm',
  repository_id: '1430610790', repository_owner_id: '250931946',
  sub: 'repo:FastTLS@250931946/fastls-ffi@1430610790:environment:npm',
  workflow_ref: 'FastTLS/fastls-ffi/.github/workflows/npm-publish.yml@refs/heads/main' };
function stub(changes = {}, failAt = 0) {
  const calls = [];
  const request = async (url, options) => {
    calls.push({ url: String(url), options });
    if (calls.length === 1) return { ok: true, json: async () => ({ value:
      'header.' + Buffer.from(JSON.stringify({ ...claims, ...changes })).toString('base64url') + '.signature' }) };
    return { status: calls.length === failAt ? 401 : 201, json: async () => ({ token_type: 'oidc',
      token: 'private-exchanged-token', expires: new Date(Date.now() + 3600000).toISOString() }) };
  };
  return { request, calls };
}
test('six trust exchanges bind the expected identity and never expose tokens', async () => {
  const { request, calls } = stub();
  const report = await checkTrust(env, request);
  assert.equal(report.packages.length, 6);
  assert.equal(report.packages.at(-1).name, '@fastls/fastls');
  assert.equal(calls.length, 7);
  assert.ok(calls.slice(1).every(c => c.options.method === 'POST' && c.url.startsWith('https://registry.npmjs.org/-/npm/v1/oidc/')));
  assert.ok(!JSON.stringify(report).includes('private-'));
});
test('wrong repository, environment, workflow, commit and audience fail before exchange', async () => {
  for (const changes of [{ repository: 'other/repo' }, { environment: 'other' },
      { workflow_ref: 'other.yml' }, { sha: 'b'.repeat(40) }, { aud: 'other' },
      { sub: 'repo:FastTLS/fastls-ffi:ref:refs/heads/main' },
      { sub: 'repo:FastTLS/fastls-ffi:environment:npm' }, { repository_id: 'other' }, { repository_owner_id: 'other' }]) {
    const { request, calls } = stub(changes);
    await assert.rejects(checkTrust(env, request), /identity/);
    assert.equal(calls.length, 1);
  }
});
test('one package rejection stops authorization before any uploads', async () => {
  const { request, calls } = stub({}, 4);
  await assert.rejects(checkTrust(env, request), /OIDC trust rejected.*401/);
  assert.equal(calls.length, 4);
});
test('local runs and foreign identity endpoints cannot send credentials', async () => {
  for (const changes of [{ GITHUB_ACTIONS: 'false' }, { GITHUB_REF: 'refs/heads/other' },
      { ACTIONS_ID_TOKEN_REQUEST_URL: 'https://example.com/token' },
      { ACTIONS_ID_TOKEN_REQUEST_URL: 'http://pipelines.actions.githubusercontent.com/token' }]) {
    const { request, calls } = stub();
    await assert.rejects(checkTrust({ ...env, ...changes }, request));
    assert.equal(calls.length, 0);
  }
});
