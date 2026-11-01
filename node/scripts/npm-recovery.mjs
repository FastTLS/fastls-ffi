import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';

export class RecoveryError extends Error {
  constructor(message, code = 'verification-rejected') { super(message); this.name = 'RecoveryError'; this.code = code; }
}
export const publisherIdentity = 'https://github.com/FastTLS/fastls-ffi/.github/workflows/npm-publish.yml@refs/heads/main';
const transient = new Set([408, 429, 500, 502, 503, 504]);
export const publicationDeadline = state => state.continuation_window?.deadline ?? state.deadline;

// One budget covers all reads in a publication attempt, including propagation.
export function recovery({ request = fetch, now = Date.now, sleep = ms => new Promise(r => setTimeout(r, ms)),
    budget = 480000, maxAttempts = 5, state, checkpoint = () => {} } = {}) {
  const deadline = state ? publicationDeadline(state) : now() + budget;
  return async function read(url, { missing = false, waitMissing = false, packageName, packageVersion } = {}) {
    let last = 'no response';
    const key = JSON.stringify([String(url), packageVersion ?? '', waitMissing ? 'readback' : 'preflight']);
    for (let attempt = 0; attempt < maxAttempts && now() < deadline; attempt++) {
      if (state) {
        // At most three publication attempts, each with five reads. Keep the
        // absolute deadline and counters across jobs and explicit resumes.
        if ((state.read_counts[key] ?? 0) >= 15) break;
        state.read_counts[key] = (state.read_counts[key] ?? 0) + 1;
        checkpoint();
      }
      let response;
      try {
        let target = url;
        // Preflight can cache a missing version. Never read that negative cache
        // after upload; signatures and exact archive digests are still mandatory.
        if (waitMissing && String(url).startsWith('https://registry.npmjs.org/')) {
          target = new URL(url);
          target.searchParams.set('fastls_readback', `${now()}-${attempt}`);
        }
        response = await request(target, { redirect: 'error', headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
          signal: AbortSignal.timeout(Math.max(1, Math.min(15000, deadline - now()))) });
      } catch (error) {
        if (!(error instanceof TypeError || ['TimeoutError','AbortError'].includes(error.name))) throw error;
        last = error.name + (error.cause?.code ? `/${error.cause.code}` : '');
      }
      if (response?.ok) {
        const value = await response.json();
        if (packageVersion === undefined) return value;
        // Read the canonical package document used by npm itself. A separate
        // /package/version lookup can retain a pre-upload negative response.
        if (value?.name !== packageName || !value.versions || typeof value.versions !== 'object' || Array.isArray(value.versions)) {
          throw new RecoveryError('Registry package document rejected');
        }
        if (Object.hasOwn(value.versions, packageVersion)) {
          const metadata = value.versions[packageVersion];
          if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata) ||
              metadata.name !== packageName || metadata.version !== packageVersion) {
            throw new RecoveryError('Registry version document rejected');
          }
          return metadata;
        }
        if (missing && !waitMissing) return null;
        if (!waitMissing) throw new RecoveryError('Registry version absent');
        last = 'version absent in package document';
        response = undefined;
      }
      if (response) {
        last = `HTTP ${response.status}`;
        await response.body?.cancel();
      }
      if (response?.status === 404 && missing && !waitMissing) return null;
      if (response && !(transient.has(response.status) || response.status === 404 && waitMissing)) {
        throw new RecoveryError(`Registry read rejected (${response.status})`);
      }
      // A newly uploaded version can remain 404 longer than a transient read
      // failure. Use the existing budget and attempt cap instead of spending
      // all five readback attempts within a few seconds of a successful upload.
      const interval = waitMissing ? Math.min(5000 * 2 ** attempt, 30000) : Math.min(1000 * 2 ** attempt, 8000);
      if (attempt + 1 < maxAttempts) await sleep(Math.max(0, Math.min(interval, deadline - now())));
    }
    throw new RecoveryError(`Registry propagation/network retry budget exhausted (${waitMissing ? 'readback' : 'preflight'}; ${last})`,
      waitMissing ? 'registry-readback-pending' : 'registry-preflight-unavailable');
  };
}

export function publicationState(items, identity, previous, { now = Date.now, attempt = 'local', continueAfterDeadline = false } = {}) {
  const set = items.map(({ name, version, sha256 }) => ({ name, version, sha256 }));
  if (new Set(set.map(i => i.name)).size !== set.length) throw new RecoveryError('Duplicate publication item');
  let state;
  if (previous) {
    if (previous.schema !== 2 || JSON.stringify(previous.identity) !== JSON.stringify(identity) ||
        JSON.stringify(previous.items?.map(({ name, version, sha256 }) => ({ name, version, sha256 }))) !== JSON.stringify(set) ||
        !Number.isSafeInteger(previous.started_at) || !Number.isSafeInteger(previous.deadline) ||
        previous.deadline !== previous.started_at + 480000 || previous.started_at > now() ||
        !Array.isArray(previous.attempts) || previous.attempts.length > 3 ||
        !previous.read_counts || typeof previous.read_counts !== 'object' || Array.isArray(previous.read_counts) ||
        Object.values(previous.read_counts).some(n => !Number.isSafeInteger(n) || n < 0 || n > 15) ||
        previous.items.some(i => !['new', 'upload-started', 'uploaded-pending', 'upload-uncertain', 'confirmation-failed', 'verified'].includes(i.state))) {
      throw new RecoveryError('Publication resume identity or budget rejected');
    }
    state = structuredClone(previous);
    const window = state.continuation_window;
    if (window && (!Number.isSafeInteger(window.started_at) || !Number.isSafeInteger(window.deadline) ||
        window.started_at < state.deadline || window.started_at > now() ||
        window.deadline !== window.started_at + 480000 ||
        typeof window.authorized_by !== 'string' || !state.attempts.includes(window.authorized_by))) {
      throw new RecoveryError('Publication continuation window rejected');
    }
  } else {
    if (continueAfterDeadline) throw new RecoveryError('Continuation requires previous publication progress');
    const start = now();
    state = { schema: 2, identity, started_at: start, deadline: start + 480000, attempts: [], read_counts: {},
      items: set.map(i => ({ ...i, state: 'new' })), packages: [] };
  }
  // A job cannot restart its counters by reusing an old attempt identifier.
  if (state.attempts.includes(attempt) || state.attempts.length >= 3) throw new RecoveryError('Publication attempt budget exhausted');
  if (continueAfterDeadline) {
    if (state.continuation_window || now() < state.deadline) throw new RecoveryError('Continuation is allowed once, after the original deadline');
    const start = now();
    state.continuation_window = { started_at: start, deadline: start + 480000, authorized_by: attempt };
  }
  if (now() >= publicationDeadline(state)) throw new RecoveryError('Publication absolute deadline exhausted', 'deadline-exhausted');
  state.attempts.push(attempt);
  return state;
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

export async function publishSet(items, { read, verify, publish, record = () => {}, state, checkpoint = () => {}, confirmOnly = false, now = Date.now }) {
  const transition = (item, next) => {
    if (!state) return;
    const target = state.items.find(i => i.name === item.name);
    Object.assign(target, next);
    checkpoint();
  };
  const pending = [];
  const confirmations = [];
  for (const item of items) {
    const prior = state?.items.find(i => i.name === item.name);
    // Inspect every package before any mutation. A pending upload is never a
    // new upload, even if registry metadata is still propagating.
    const metadata = await read(item.url, { missing: true, packageName: item.name, packageVersion: item.version });
    if (metadata === null) {
      if (confirmOnly || prior && prior.state !== 'new') confirmations.push(item);
      else pending.push(item);
    }
    else {
      const proof = await verify(item, metadata);
      transition(item, { state: 'verified' });
      record({ name: item.name, state: 'verified-existing', proof });
    }
  }
  const upload = async item => {
    if (confirmOnly) throw new RecoveryError('Read-only confirmation cannot upload a missing package');
    if (state && now() >= publicationDeadline(state)) throw new RecoveryError('Publication absolute deadline exhausted before upload', 'deadline-exhausted');
    transition(item, { state: 'upload-started' });
    const result = await publish(item);
    transition(item, { state: result.status === 0 ? 'uploaded-pending' : 'upload-uncertain', upload_status: result.status });
    confirmations.push(item);
  };
  const confirm = async group => {
    // Parallelize propagation waits. Crypto verification stays sequential to
    // avoid sharing mutable Sigstore/TUF caches across verification calls.
    const results = await Promise.allSettled(group.map(item => read(item.url,
      { waitMissing: true, packageName: item.name, packageVersion: item.version })));
    const failures = [];
    for (let index = 0; index < group.length; index++) {
      const item = group[index], result = results[index];
    // A conflict/network failure needs proof of exact bytes and trusted source;
    // never blindly retry a mutation or treat 409 as success.
    try {
      if (result.status === 'rejected') throw result.reason;
      const proof = await verify(item, result.value);
      transition(item, { state: 'verified' });
      record({ name: item.name, state: 'verified-published', proof });
    } catch (error) {
      if (error.code !== 'registry-readback-pending') transition(item, { state: 'confirmation-failed' });
      failures.push(error);
    }
    }
    // Identity failures take precedence over a simultaneous propagation delay.
    if (failures.length) throw failures.find(e => e.code !== 'registry-readback-pending') ?? failures[0];
  };
  const main = items.at(-1);
  for (const item of pending.filter(i => i !== main)) await upload(item);
  await confirm(confirmations.filter(i => i !== main));
  // The main package becomes visible only after all five native proofs pass.
  if (pending.includes(main)) await upload(main);
  await confirm(confirmations.filter(i => i === main));
}
