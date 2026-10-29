import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { platforms } from './npm-platforms.mjs';

class TrustError extends Error {}

// Exercise the real package-scoped trust policy before the first package upload.
// https://api-docs.npmjs.com/ documents the OIDC exchange endpoint.
export async function checkTrust(env = process.env, request = fetch) {
  if (env.GITHUB_ACTIONS !== 'true' || env.GITHUB_REPOSITORY !== 'FastTLS/fastls-ffi' ||
      env.GITHUB_REF !== 'refs/heads/main' || !env.ACTIONS_ID_TOKEN_REQUEST_TOKEN) {
    throw new TrustError('Trusted publishing requires the main GitHub Actions workflow');
  }
  const url = new URL(env.ACTIONS_ID_TOKEN_REQUEST_URL);
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.actions.githubusercontent.com')) {
    throw new TrustError('Unexpected GitHub identity endpoint');
  }
  url.searchParams.set('audience', 'npm:registry.npmjs.org');
  const identity = await request(url, { redirect: 'error', signal: AbortSignal.timeout(30000),
    headers: { Authorization: `Bearer ${env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}`, Accept: 'application/json' } });
  if (!identity.ok) throw new TrustError(`GitHub identity request failed (${identity.status})`);
  const { value } = await identity.json();
  if (typeof value !== 'string') throw new TrustError('Missing GitHub identity token');
  const claims = JSON.parse(Buffer.from(value.split('.')[1], 'base64url').toString('utf8'));
  const expected = { iss: 'https://token.actions.githubusercontent.com', aud: 'npm:registry.npmjs.org',
    repository: env.GITHUB_REPOSITORY, ref: env.GITHUB_REF, sha: env.GITHUB_SHA, environment: 'npm',
    repository_id: '1430610790', repository_owner_id: '250931946',
    sub: 'repo:FastTLS@250931946/fastls-ffi@1430610790:environment:npm',
    workflow_ref: 'FastTLS/fastls-ffi/.github/workflows/npm-publish.yml@refs/heads/main' };
  const mismatches = Object.keys(expected).filter(key => claims[key] !== expected[key]);
  if (mismatches.length) {
    throw new TrustError('Unexpected trusted publisher identity: ' + mismatches.join(', '));
  }
  const packages = [];
  for (const name of [...platforms.map(p => p.name), '@fastls/fastls']) {
    const response = await request(`https://registry.npmjs.org/-/npm/v1/oidc/token/exchange/package/${name.replace('/', '%2f')}`,
      { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
        headers: { Authorization: `Bearer ${value}`, Accept: 'application/json' } });
    // Never log response bodies: successful bodies contain short-lived tokens.
    if (response.status !== 201) throw new TrustError(`OIDC trust rejected for ${name} (${response.status}); no package was uploaded`);
    const exchanged = await response.json();
    // Match npm CLI's exchange contract: success plus a nonempty token. The
    // registry does not consistently include the optional example metadata.
    if (typeof exchanged.token !== 'string' || !exchanged.token) {
      throw new TrustError(`Invalid OIDC exchange response for ${name}`);
    }
    const expires = typeof exchanged.expires === 'string' && Number.isFinite(Date.parse(exchanged.expires)) ? exchanged.expires : null;
    packages.push({ name, authorized: true, expires });
  }
  return { schema: 1, revision: env.GITHUB_SHA, workflow: claims.workflow_ref,
    environment: claims.environment, subject: claims.sub, packages,
    scope: 'Live package-scoped OIDC exchanges; no package uploads or local npm credentials.' };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const report = await checkTrust();
    writeFileSync('npm-trust-verification.json', JSON.stringify(report, null, 2) + '\n');
    console.log('OIDC trust verified for all six packages at the expected workflow/environment.');
  } catch (error) {
    // Network exception causes may carry request details; print only our message.
    console.error(error instanceof TrustError ? error.message : `OIDC verification failed (${error.name})`);
    process.exitCode = 1;
  }
}
