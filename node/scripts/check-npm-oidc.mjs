import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { platforms } from './npm-platforms.mjs';

// Exercise the real package-scoped trust policy before the first package upload.
// https://api-docs.npmjs.com/ documents the OIDC exchange endpoint.
export async function checkTrust(env = process.env, request = fetch) {
  if (env.GITHUB_ACTIONS !== 'true' || env.GITHUB_REPOSITORY !== 'FastTLS/fastls-ffi' ||
      env.GITHUB_REF !== 'refs/heads/main' || !env.ACTIONS_ID_TOKEN_REQUEST_TOKEN) {
    throw Error('Trusted publishing requires the main GitHub Actions workflow');
  }
  const url = new URL(env.ACTIONS_ID_TOKEN_REQUEST_URL);
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.actions.githubusercontent.com')) {
    throw Error('Unexpected GitHub identity endpoint');
  }
  url.searchParams.set('audience', 'npm:registry.npmjs.org');
  const identity = await request(url, { redirect: 'error', signal: AbortSignal.timeout(30000),
    headers: { Authorization: `Bearer ${env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}`, Accept: 'application/json' } });
  if (!identity.ok) throw Error(`GitHub identity request failed (${identity.status})`);
  const { value } = await identity.json();
  if (typeof value !== 'string') throw Error('Missing GitHub identity token');
  const claims = JSON.parse(Buffer.from(value.split('.')[1], 'base64url').toString('utf8'));
  if (claims.iss !== 'https://token.actions.githubusercontent.com' || claims.aud !== 'npm:registry.npmjs.org' ||
      claims.repository !== env.GITHUB_REPOSITORY || claims.ref !== env.GITHUB_REF || claims.sha !== env.GITHUB_SHA ||
      claims.environment !== 'npm' || claims.sub !== 'repo:FastTLS/fastls-ffi:environment:npm' ||
      claims.workflow_ref !== 'FastTLS/fastls-ffi/.github/workflows/npm-publish.yml@refs/heads/main') {
    throw Error('Unexpected trusted publisher identity');
  }
  const packages = [];
  for (const name of [...platforms.map(p => p.name), '@fastls/fastls']) {
    const response = await request(`https://registry.npmjs.org/-/npm/v1/oidc/token/exchange/package/${name.replace('/', '%2f')}`,
      { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
        headers: { Authorization: `Bearer ${value}`, Accept: 'application/json' } });
    // Never log response bodies: successful bodies contain short-lived tokens.
    if (response.status !== 201) throw Error(`OIDC trust rejected for ${name} (${response.status}); no package was uploaded`);
    const exchanged = await response.json();
    if (exchanged.token_type !== 'oidc' || typeof exchanged.token !== 'string' || !exchanged.token ||
        !(Date.parse(exchanged.expires) > Date.now())) {
      throw Error(`Invalid OIDC exchange response for ${name}`);
    }
    packages.push({ name, authorized: true, expires: exchanged.expires });
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
    console.error(error.message);
    process.exitCode = 1;
  }
}
