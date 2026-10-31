import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';

export class RecoveryError extends Error {}
export const publisherIdentity = 'https://github.com/FastTLS/fastls-ffi/.github/workflows/npm-publish.yml@refs/heads/main';
const transient = new Set([408, 429, 500, 502, 503, 504]);

// One budget covers all reads in a publication attempt, including propagation.
export function recovery({ request = fetch, now = Date.now, sleep = ms => new Promise(r => setTimeout(r, ms)),
    budget = 480000, maxAttempts = 5 } = {}) {
  const deadline = now() + budget;
  return async function read(url, { missing = false, waitMissing = false } = {}) {
    for (let attempt = 0; attempt < maxAttempts && now() < deadline; attempt++) {
      let response;
      try {
        response = await request(url, { redirect: 'error', headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
          signal: AbortSignal.timeout(Math.max(1, Math.min(15000, deadline - now()))) });
      } catch (error) {
        if (!(error instanceof TypeError || ['TimeoutError','AbortError'].includes(error.name))) throw error;
      }
      if (response?.ok) return response.json();
      if (response?.status === 404 && missing && !waitMissing) return null;
      if (response && !(transient.has(response.status) || response.status === 404 && waitMissing)) {
        throw new RecoveryError(`Registry read rejected (${response.status})`);
      }
      if (attempt + 1 < maxAttempts) await sleep(Math.max(0, Math.min(1000 * 2 ** attempt, 8000, deadline - now())));
    }
    throw new RecoveryError('Registry propagation/network retry budget exhausted');
  };
}

export async function verifyRegistryProvenance(item, metadata, bytes, read, certifyRevision, verify) {
  const integrity = 'sha512-' + createHash('sha512').update(bytes).digest('base64');
  if (metadata.name !== item.name || metadata.version !== item.version || metadata.dist?.integrity !== integrity) {
    throw new RecoveryError('Existing package name, version or integrity differs');
  }
  const url = new URL(metadata.dist?.attestations?.url ?? 'https://invalid.example/');
  if (url.origin !== 'https://registry.npmjs.org' || !url.pathname.startsWith('/-/npm/v1/attestations/')) {
    throw new RecoveryError('Registry package has no trusted provenance endpoint');
  }
  const attestations = await read(url, { waitMissing: true });
  const candidates = attestations.attestations?.filter(a => a.predicateType === 'https://slsa.dev/provenance/v1') ?? [];
  if (candidates.length !== 1) throw new RecoveryError('Exactly one SLSA provenance statement required');
  const bundle = candidates[0].bundle;
  if (!verify) {
    if (!process.env.npm_execpath) throw new RecoveryError('npm CLI Sigstore verifier required');
    verify = createRequire(process.env.npm_execpath)('sigstore').verify;
  }
  await verify(bundle, { certificateIdentityURI: publisherIdentity,
    certificateIssuer: 'https://token.actions.githubusercontent.com', timeout: 10000, retry: { retries: 1 } });
  const statement = JSON.parse(Buffer.from(bundle.dsseEnvelope.payload, 'base64').toString('utf8'));
  const subject = statement.subject;
  const definition = statement.predicate?.buildDefinition;
  const workflow = definition?.externalParameters?.workflow;
  const github = definition?.internalParameters?.github;
  const dependencies = definition?.resolvedDependencies;
  if (statement.predicateType !== 'https://slsa.dev/provenance/v1' || subject?.length !== 1 ||
      subject[0].name !== `pkg:npm/${item.name.replace('@', '%40')}@${item.version}` ||
      subject[0].digest?.sha512 !== createHash('sha512').update(bytes).digest('hex') ||
      workflow?.repository !== 'https://github.com/FastTLS/fastls-ffi' ||
      workflow?.path !== '.github/workflows/npm-publish.yml' || workflow?.ref !== 'refs/heads/main' ||
      github?.repository_id !== '1430610790' || github?.repository_owner_id !== '250931946' ||
      github?.event_name !== 'workflow_dispatch' || dependencies?.length !== 1 ||
      dependencies[0].uri !== 'git+https://github.com/FastTLS/fastls-ffi@refs/heads/main' ||
      statement.predicate?.runDetails?.builder?.id !== 'https://github.com/actions/runner/github-hosted') {
    throw new RecoveryError('Signed package subject or publishing source identity differs');
  }
  const revision = dependencies[0].digest?.gitCommit;
  if (!/^[0-9a-f]{40}$/.test(revision)) throw new RecoveryError('Invalid signed publisher revision');
  await certifyRevision(revision);
  return { integrity, publisher_revision: revision, workflow: publisherIdentity, verified: true };
}

export async function publishSet(items, { read, verify, publish, record = () => {} }) {
  const pending = [];
  for (const item of items) {
    const metadata = await read(item.url, { missing: true });
    if (metadata === null) pending.push(item);
    else record({ name: item.name, state: 'verified-existing', proof: await verify(item, metadata) });
  }
  for (const item of pending) {
    const result = await publish(item);
    // A conflict/network failure needs proof of exact bytes and trusted source;
    // never blindly retry a mutation or treat 409 as success.
    const metadata = await read(item.url, { waitMissing: true });
    const proof = await verify(item, metadata);
    record({ name: item.name, state: 'verified-published', upload_status: result.status, proof });
  }
}
