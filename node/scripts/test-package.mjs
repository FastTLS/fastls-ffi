import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { scenarioCount } from './check-parity.mjs';
import { tmpdir } from 'node:os';
import { resolve, dirname, basename } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const npmCLI = process.env.npm_execpath;
if (!npmCLI) throw new Error('Run this script with npm run test:package');
const temporary = mkdtempSync(resolve(tmpdir(), 'fastls-node-package-'));
const sha256 = path => createHash('sha256').update(readFileSync(path)).digest('hex');
function run(args, cwd = root, env = process.env) {
  const processResult = spawnSync(process.execPath, args, {
    cwd, env, encoding: 'utf8', windowsHide: true, timeout: 600000,
  });
  if (processResult.status !== 0) {
    process.stdout.write(processResult.stdout ?? '');
    process.stderr.write(processResult.stderr ?? '');
    throw processResult.error ?? new Error(`command failed with exit ${processResult.status}`);
  }
  return processResult.stdout;
}
try {
  const output = run([npmCLI, 'pack', '--json', '--pack-destination', temporary]);
  const [packed] = JSON.parse(output);
  const { filename } = packed;
  if (basename(filename) !== filename) throw new Error('npm returned an unsafe package filename');
  const entries = new Set(packed.files.map(file => file.path));
  for (const required of ['package.json', 'README.md', 'dist/index.js', 'dist/index.mjs', 'dist/index.d.ts']) {
    if (!entries.has(required)) throw new Error(`Package is missing ${required}`);
  }
  for (const entry of entries) {
    if (entry !== 'package.json' && entry !== 'README.md' && !/^dist\/[\w-]+\.(?:js|mjs|d\.ts)$/.test(entry)) {
      throw new Error(`Unexpected package content: ${entry}`);
    }
  }
  writeFileSync(resolve(temporary, 'package.json'), JSON.stringify({ name: 'fastls-install-test', private: true }));
  run([npmCLI, 'install', '--no-audit', '--no-fund', resolve(temporary, filename)], temporary);
  const name = process.platform === 'win32' ? 'fastls.dll' : process.platform === 'darwin' ? 'libfastls.dylib' : 'libfastls.so';
  const env = { ...process.env, FASTLS_NODE_PACKAGE: temporary,
    FASTLS_LIBRARY: process.env.FASTLS_LIBRARY ?? resolve(root, '../dist', name) };
  const nativeHash = sha256(env.FASTLS_LIBRARY);
  for (const file of ['types.mts', 'types.cts']) copyFileSync(resolve(root, 'tests', file), resolve(temporary, file));
  run([resolve(root, 'node_modules/typescript/bin/tsc'), '--ignoreConfig', '--noEmit', '--strict',
    '--module', 'NodeNext', '--target', 'ES2022', '--types', 'node',
    '--typeRoots', resolve(root, 'node_modules/@types'), 'types.mts', 'types.cts'], temporary, env);
  const evidenceDirectory = resolve(root, '../build');
  const previous = new Set(readdirSync(evidenceDirectory));
  process.stdout.write(run(['--expose-gc', '--test', '--test-concurrency=1', ...[
    'native.test.mjs', 'sdk.test.mjs', 'parity.test.mjs',
  ].map(file => resolve(root, 'tests', file))], temporary, env));
  const records = readdirSync(evidenceDirectory).filter(file => file.startsWith('node-parity-package-') && file.endsWith('.json') && !previous.has(file));
  if (records.length !== 1) throw new Error('Missing or ambiguous installed parity evidence');
  const parity = JSON.parse(readFileSync(resolve(evidenceDirectory, records[0]), 'utf8'));
  if (parity.cases.length !== scenarioCount || !parity.cases.every(test => test.passed)) throw new Error('Incomplete Python parity suite');
  // Exercise package exports by name, not just by absolute entry filenames.
  process.stdout.write(run(['--input-type=module', '-e',
    "import {Session} from '@fasttls/fastls'; import {createRequire} from 'node:module'; const cjs=createRequire(import.meta.url)('@fasttls/fastls'); if(Session!==cjs.Session)throw Error('entry mismatch'); const s=new Session(); try {if(!s.listProfiles().includes('chrome/156/windows'))throw Error('missing profile')}finally{s.close()}"], temporary, env));
  // Preserve the exact tested tarball for CI artifacts; it still requires the separate Go library.
  const destination = resolve(root, '../dist');
  mkdirSync(destination, { recursive: true });
  if (sha256(env.FASTLS_LIBRARY) !== nativeHash) throw new Error('Native library changed during package validation');
  const manifest = {
    schema: 1, node: process.version, platform: process.platform, arch: process.arch,
    revision: process.env.GITHUB_SHA ?? null,
    package: { name: packed.name, version: packed.version, file: filename, sha256: sha256(resolve(temporary, filename)) },
    native: { file: basename(env.FASTLS_LIBRARY), sha256: nativeHash, abi: 1, embedded: false },
    checks: { installedSuite: true, types: true, esm: true, commonjs: true },
    parity: { scenarios: scenarioCount, adaptations: parity.cases.filter(test => test.adaptation).length, evidence: records[0] },
  };
  copyFileSync(resolve(temporary, filename), resolve(destination, filename));
  writeFileSync(resolve(destination, 'node-package-info.json'), JSON.stringify(manifest, null, 2) + '\n');
  writeFileSync(resolve(destination, filename + '.sha256'), `${manifest.package.sha256}  ${filename}\n`);
  console.log(`Verified package saved to ${resolve(destination, filename)}`);
} finally {
  if (dirname(temporary) !== resolve(tmpdir()) || !basename(temporary).startsWith('fastls-node-package-')) {
    throw new Error('Refusing to remove a directory outside the temporary package test');
  }
  rmSync(temporary, { recursive: true, force: true });
}
