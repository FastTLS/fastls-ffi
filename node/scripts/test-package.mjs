import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync, rmSync, renameSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { scenarioCount } from './check-parity.mjs';
import { tmpdir } from 'node:os';
import { resolve, dirname, basename } from 'node:path';
import { packNative } from './pack-native.mjs';

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
  const packed = packNative(temporary, npmCLI);
  const { filename } = packed;
  if (basename(filename) !== filename) throw new Error('npm returned an unsafe package filename');
  const entries = new Set(packed.files.map(file => file.path));
  for (const required of ['package.json', 'README.md', 'dist/index.js', 'dist/index.mjs', 'dist/index.d.ts']) {
    if (!entries.has(required)) throw new Error(`Package is missing ${required}`);
  }
  for (const entry of entries) {
    if (entry !== 'package.json' && entry !== 'README.md' && !/^dist\/[\w-]+\.(?:js|mjs|d\.ts)$/.test(entry) &&
        !['native/manifest.json', 'native/THIRD_PARTY_NOTICES.txt', `native/${packed.native.file}`].includes(entry)) {
      throw new Error(`Unexpected package content: ${entry}`);
    }
  }
  for (const file of ['manifest.json', 'THIRD_PARTY_NOTICES.txt', packed.native.file]) {
    if (!entries.has(`native/${file}`)) throw Error(`Missing native candidate file: ${file}`);
  }
  writeFileSync(resolve(temporary, 'package.json'), JSON.stringify({ name: 'fastls-install-test', private: true }));
  const env = { ...process.env, FASTLS_NODE_PACKAGE: temporary };
  delete env.FASTLS_COOKIE_COMPAT_LIBRARIES;
  delete env.FASTLS_LIBRARY;
  // The installed suite starts only an already-built local fixture by absolute
  // path. An empty search path makes accidental compiler/source fallback fail.
  env.PATH = resolve(temporary, 'no-tools');
  delete env.Path;
  // Koffi's matching optional prebuild must suffice; no lifecycle hook may
  // compile or download a Fastls library while installing this candidate.
  run([npmCLI, 'install', '--ignore-scripts', '--no-audit', '--no-fund', resolve(temporary, filename)], temporary, env);
  const incompatible = resolve(temporary, 'incompatible'); mkdirSync(incompatible);
  writeFileSync(resolve(incompatible, 'package.json'), '{"private":true}');
  // npm 11 applies --cpu overrides only to optional dependencies. Instead,
  // verify the required package gate using a copy of our actual metadata with
  // a deliberately incompatible CPU declaration.
  const incompatibleSource = resolve(incompatible, 'source'); mkdirSync(incompatibleSource);
  const targetMetadata = JSON.parse(readFileSync(resolve(temporary, 'node_modules/@fasttls/fastls/package.json'), 'utf8'));
  const wrongMetadata = { ...targetMetadata, cpu: ['wrong-architecture'] }; delete wrongMetadata.dependencies;
  writeFileSync(resolve(incompatibleSource, 'package.json'), JSON.stringify(wrongMetadata));
  const [wrongPackage] = JSON.parse(run([npmCLI, 'pack', '--json', '--ignore-scripts', '--pack-destination', incompatible], incompatibleSource, env));
  const rejected = spawnSync(process.execPath, [npmCLI, 'install', '--ignore-scripts', '--no-audit', '--no-fund',
    resolve(incompatible, wrongPackage.filename)], { cwd: incompatible, env, encoding: 'utf8', windowsHide: true, timeout: 60000 });
  if (rejected.status === 0 || !/EBADPLATFORM/.test((rejected.stdout ?? '') + (rejected.stderr ?? ''))) {
    process.stderr.write(rejected.stderr ?? '');
    throw rejected.error ?? Error(`npm did not reject an incompatible candidate (exit ${rejected.status})`);
  }
  const installed = resolve(temporary, 'node_modules/@fasttls/fastls');
  const nativePath = resolve(installed, 'native', packed.native.file);
  const nativeHash = sha256(nativePath);
  // Resolve from the consumer; resolving inside this package uses self-reference
  // even when require.resolve receives a different paths array.
  run(['-e', `const assert=require('node:assert/strict');const {createRequire}=require('node:module');const path=require('node:path');const req=createRequire(path.join(process.cwd(),'package.json'));const entry=req.resolve('@fasttls/fastls');assert.equal(entry,require('node:fs').realpathSync(process.argv[1]));const {Native}=req(path.join(path.dirname(entry),'native.js'));assert.equal(Native.load().path,require('node:fs').realpathSync(process.argv[2]));`, resolve(installed,'dist/index.js'), nativePath], temporary, env);
  if (nativeHash !== packed.native.sha256) throw Error('Bundled native library differs from manifest');
  for (const file of ['types.mts', 'types.cts']) copyFileSync(resolve(root, 'tests', file), resolve(temporary, file));
  run([resolve(root, 'node_modules/typescript/bin/tsc'), '--ignoreConfig', '--noEmit', '--strict',
    '--module', 'NodeNext', '--target', 'ES2022', '--types', 'node',
    '--typeRoots', resolve(root, 'node_modules/@types'), 'types.mts', 'types.cts'], temporary, env);
  const evidenceDirectory = resolve(root, '../build');
  process.stdout.write(run([resolve(root, 'scripts/test-cookie-compat.mjs')], temporary, env));
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
    "import {Session,ServerSentEvent,SSEStream} from '@fasttls/fastls'; import {createRequire} from 'node:module'; const cjs=createRequire(import.meta.url)('@fasttls/fastls'); if(Session!==cjs.Session||ServerSentEvent!==cjs.ServerSentEvent||SSEStream!==cjs.SSEStream)throw Error('entry mismatch'); if(new ServerSentEvent('{\"ok\":true}').json().ok!==true)throw Error('event JSON'); const s=new Session(); try {if(!s.listProfiles().includes('chrome/156/windows'))throw Error('missing profile')}finally{s.close()}"], temporary, env));
  // Validate candidate failures in fresh processes, before the library is loaded.
  const manifestPath = resolve(installed, 'native/manifest.json');
  const originalManifest = readFileSync(manifestPath, 'utf8');
  const installedPackage = JSON.parse(readFileSync(resolve(installed, 'package.json'), 'utf8'));
  if (!installedPackage.private || installedPackage.scripts || installedPackage.os[0] !== process.platform || installedPackage.cpu[0] !== process.arch) throw Error('Candidate package metadata is unsafe or incorrect');
  const rejectCandidate = pattern => run(['-e', `const assert=require('node:assert/strict');const {Session}=require('@fasttls/fastls');assert.throws(()=>new Session(),e=>${pattern}.test(String(e.cause)));`], temporary, env);
  try {
    writeFileSync(manifestPath, JSON.stringify({ ...packed.native, arch: 'wrong-architecture' }));
    rejectCandidate('/platform mismatch/');
    // Explicit options and environment overrides must still work when the
    // embedded candidate cannot be used; both load the actual installed DLL.
    run(['-e', `const {Session}=require('@fasttls/fastls');const s=new Session({library:process.argv[1]});s.close();` , nativePath], temporary, env);
    run(['-e', `const {Session}=require('@fasttls/fastls');const s=new Session();s.close();`], temporary, { ...env, FASTLS_LIBRARY: nativePath });
    writeFileSync(manifestPath, JSON.stringify({ ...packed.native, sha256: '0'.repeat(64) }));
    rejectCandidate('/SHA-256 mismatch/');
    if (process.platform === 'linux') {
      writeFileSync(manifestPath, JSON.stringify({ ...packed.native, requirements: { glibc: '999.0' } }));
      rejectCandidate('/requires glibc/');
    }
  } finally { writeFileSync(manifestPath, originalManifest); }
  renameSync(manifestPath, manifestPath + '.hidden');
  try { rejectCandidate('/no native manifest/'); } finally { renameSync(manifestPath + '.hidden', manifestPath); }
  renameSync(nativePath, nativePath + '.hidden');
  try { rejectCandidate('/ENOENT/'); } finally { renameSync(nativePath + '.hidden', nativePath); }
  // Preserve the exact tested candidate for CI artifacts.
  const destination = resolve(root, '../dist');
  mkdirSync(destination, { recursive: true });
  if (sha256(nativePath) !== nativeHash) throw new Error('Native library changed during package validation');
  const manifest = {
    schema: 1, node: process.version, platform: process.platform, arch: process.arch,
    revision: process.env.GITHUB_SHA ?? null,
    package: { name: packed.name, version: packed.version, file: filename, sha256: sha256(resolve(temporary, filename)) },
    native: { file: packed.native.file, sha256: nativeHash, abi: 1, embedded: true, manifest: packed.native },
    checks: { installedSuite: true, types: true, esm: true, commonjs: true,
      noLibraryEnvironment: true, noCompilerPath: true, installScriptsDisabled: true,
      platformGate: true, npmPlatformGate: true, integrityGate: true, externalOverrides: true, missingNativeGate: true,
      installedEntryAndNativePath: true },
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
