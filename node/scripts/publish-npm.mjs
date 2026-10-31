import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, basename } from 'node:path';
import { spawnSync } from 'node:child_process';
import { platforms } from './npm-platforms.mjs';
import { checkTrust } from './check-npm-oidc.mjs';
import { recovery, verifyRegistryProvenance, publishSet } from './npm-recovery.mjs';

const directory = resolve(process.argv[2] ?? resolve(import.meta.dirname, '../../npm-release'));
const report = JSON.parse(readFileSync(resolve(directory, 'npm-release-manifest.json'), 'utf8'));
const npmCLI = process.env.npm_execpath;
if (!npmCLI) throw Error('Use npm run publish:npm');
if (report.license_review_required.length) throw Error('Resolve dependency license review before publishing');
if (process.env.GITHUB_ACTIONS !== 'true' || process.env.GITHUB_REPOSITORY !== 'FastTLS/fastls-ffi' ||
    process.env.GITHUB_REF !== 'refs/heads/main' || process.env.GITHUB_WORKFLOW_REF !==
    'FastTLS/fastls-ffi/.github/workflows/npm-publish.yml@refs/heads/main') throw Error('GitHub Actions trusted publishing required');
if ((report.publisher_revision ?? report.revision) !== process.env.GITHUB_SHA) throw Error('Publication revision must match main workflow');
function certifyRevision(revision) {
  const proof = spawnSync(process.env.PYTHON ?? 'python', [resolve(import.meta.dirname, 'promotion_source.py'), revision, process.env.GITHUB_SHA],
    { encoding: 'utf8', windowsHide: true, timeout: 30000 });
  if (proof.status !== 0) throw Error('Producer/publisher source verification failed');
  return JSON.parse(proof.stdout);
}
if (report.revision !== process.env.GITHUB_SHA &&
    JSON.stringify(certifyRevision(report.revision)) !== JSON.stringify(report.source_verification)) throw Error('Producer verification differs from manifest');
const expected = [...platforms.map(p => p.name).sort(), '@fastls/fastls'];
const names = report.publish_order.map(p => p.name);
if (names.at(-1) !== '@fastls/fastls' || JSON.stringify([...names.slice(0, -1)].sort().concat(names.at(-1))) !== JSON.stringify(expected)) throw Error('Expected five native packages followed by the main package');
const items = report.publish_order.map(item => {
  if (basename(item.file) !== item.file || item.version !== report.version) throw Error('Invalid release manifest');
  const file = resolve(directory, item.file), bytes = readFileSync(file);
  if (createHash('sha256').update(bytes).digest('hex') !== item.sha256) throw Error(`Archive changed: ${item.name}`);
  return { ...item, file, bytes, url: `https://registry.npmjs.org/${encodeURIComponent(item.name)}/${encodeURIComponent(item.version)}` };
});
await checkTrust();
const read = recovery();
const progress = { schema: 1, run_id: process.env.GITHUB_RUN_ID, attempt: process.env.GITHUB_RUN_ATTEMPT,
  producer_revision: report.revision, publisher_revision: process.env.GITHUB_SHA, packages: [] };
const progressFile = resolve(directory, `publication-${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT}.json`);
const save = () => writeFileSync(progressFile, JSON.stringify(progress, null, 2) + '\n');
save();
try {
  await publishSet(items, { read,
    verify: (item, metadata) => verifyRegistryProvenance(item, metadata, item.bytes, read, certifyRevision),
    record: record => { progress.packages.push(record); save(); },
    publish: item => {
      const result = spawnSync(process.execPath, [npmCLI, 'publish', item.file, '--access', 'public', '--tag',
        report.version.includes('-') ? 'next' : 'latest', '--registry', 'https://registry.npmjs.org/',
        '--ignore-scripts', '--provenance'], { encoding: 'utf8', windowsHide: true, timeout: 60000 });
      const output = (result.stdout ?? '') + (result.stderr ?? '');
      const code = [...output.matchAll(/\b(E[A-Z0-9]+)\b/g)].map(m => m[1]);
      if (code.some(c => ['E401','E403','EOTP','ENEEDAUTH'].includes(c))) throw Error('Publication identity or permission rejected');
      if (result.status !== 0 && !result.error &&
          !code.some(c => ['E409','EPUBLISHCONFLICT','E404','E408','E429','E500','E502','E503','E504','ETIMEDOUT','ECONNRESET','EAI_AGAIN'].includes(c))) {
        throw Error(`Package upload rejected: ${item.name} (${code.at(-1) ?? result.status})`);
      }
      if (result.error && result.error.code !== 'ETIMEDOUT') throw result.error;
      progress.packages.push({ name: item.name, state: 'upload-result', status: result.status, codes: code }); save();
      return result;
    } });
  progress.status = 'success';
} catch (error) {
  progress.status = 'failure'; progress.error = error.name; throw error;
} finally { save(); }
