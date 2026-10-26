import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve, basename } from 'node:path';
import { spawnSync } from 'node:child_process';
import { platforms } from './npm-platforms.mjs';

const directory = resolve(process.argv[2] ?? resolve(import.meta.dirname, '../../npm-release'));
const report = JSON.parse(readFileSync(resolve(directory, 'npm-release-manifest.json'), 'utf8'));
const npmCLI = process.env.npm_execpath;
if (!npmCLI) throw Error('Use npm run publish:npm');
if (report.license_review_required.length) throw Error('Resolve dependency license review before publishing');
if (process.env.GITHUB_ACTIONS && (process.env.GITHUB_REF !== 'refs/heads/main' || report.revision !== process.env.GITHUB_SHA)) throw Error('Release revision must match main workflow');
const expected = [...platforms.map(p => p.name).sort(), '@fastls/fastls'];
const names = report.publish_order.map(p => p.name);
if (names.at(-1) !== '@fastls/fastls' || JSON.stringify([...names.slice(0, -1)].sort().concat(names.at(-1))) !== JSON.stringify(expected)) throw Error('Expected five native packages followed by the main package');
const pending = [];
// Check every archive and existing registry version before the first mutation.
for (const item of report.publish_order) {
  if (basename(item.file) !== item.file || item.version !== report.version) throw Error('Invalid release manifest');
  const file = resolve(directory, item.file), bytes = readFileSync(file);
  if (createHash('sha256').update(bytes).digest('hex') !== item.sha256) throw Error(`Archive changed: ${item.name}`);
  const integrity = 'sha512-' + createHash('sha512').update(bytes).digest('base64');
  const response = await fetch(`https://registry.npmjs.org/${encodeURIComponent(item.name)}/${encodeURIComponent(item.version)}`, { signal: AbortSignal.timeout(30000) });
  if (response.status === 404) pending.push({ ...item, file });
  else if (response.ok && (await response.json()).dist?.integrity === integrity) console.log(`Already published with matching integrity: ${item.name}@${item.version}`);
  else throw Error(`Registry version exists with different contents or cannot be checked: ${item.name} (${response.status})`);
}
const tag = report.version.includes('-') ? 'next' : 'latest';
for (const item of pending) {
  console.log(`Publishing ${item.name}@${item.version} with tag ${tag}`);
  const result = spawnSync(process.execPath, [npmCLI, 'publish', item.file, '--access', 'public', '--tag', tag,
    '--registry', 'https://registry.npmjs.org/', '--ignore-scripts', ...(process.env.GITHUB_ACTIONS ? ['--provenance'] : [])],
    { stdio: 'inherit', windowsHide: true, timeout: 600000 });
  if (result.status !== 0) throw result.error ?? Error(`Publishing failed (${result.status}); matching published archives can be resumed`);
}
