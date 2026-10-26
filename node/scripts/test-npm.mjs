import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { packNpm } from './pack-npm.mjs';
import { platforms } from './npm-platforms.mjs';

const root = resolve(import.meta.dirname, '..');
const destination = resolve(root, '../dist/npm');
const npmCLI = process.env.npm_execpath;
const record = packNpm(destination, npmCLI);
const temporary = mkdtempSync(resolve(tmpdir(), 'fastls-npm-install-'));
const requested = new Set();
let origin;
// Serve our unpublished scope locally. All other dependencies use the public
// registry; npm itself resolves and filters optional packages by OS/CPU/libc.
const server = createServer((req, res) => {
  const name = decodeURIComponent(req.url.slice(1));
  const target = platforms.find(p => p.name === name);
  res.setHeader('Content-Type', 'application/json');
  if (target) {
    const metadata = { ...target, version: record.main.version,
      dist: { tarball: `${origin}/native.tgz`, integrity: 'sha512-' + createHash('sha512').update(readFileSync(resolve(destination, record.native.file))).digest('base64') } };
    res.end(JSON.stringify({ name, 'dist-tags': { latest: metadata.version }, versions: { [metadata.version]: metadata } }));
  } else if (req.url === '/native.tgz') {
    requested.add('native'); res.end(readFileSync(resolve(destination, record.native.file)));
  } else { res.statusCode = 404; res.end('{}'); }
});
const run = (args, cwd = temporary, env = process.env) => new Promise((resolvePromise, reject) => {
  const child = spawn(process.execPath, args, { cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  child.stdout.on('data', data => { output += data; }); child.stderr.on('data', data => { output += data; });
  const timer = setTimeout(() => child.kill(), 600000);
  child.on('error', reject);
  child.on('close', code => { clearTimeout(timer); if (code === 0) resolvePromise(output); else reject(Error(`command failed (${code}): ${output}`)); });
});
try {
  await new Promise(resolvePromise => server.listen(0, '127.0.0.1', resolvePromise));
  origin = `http://127.0.0.1:${server.address().port}`;
  writeFileSync(resolve(temporary, 'package.json'), '{"private":true}');
  const env = { ...process.env, PATH: resolve(temporary, 'no-tools') };
  delete env.Path; delete env.FASTLS_LIBRARY; delete env.FASTLS_NODE_PACKAGE;
  await run([npmCLI, 'install', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', resolve(temporary, 'cache'),
    `--@fastls:registry=${origin}`, resolve(destination, record.main.file)], temporary, env);
  assert.ok(requested.has('native'), 'npm failed to download optional native package');
  const probe = `const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const {createRequire}=require('node:module');const req=createRequire(path.join(process.cwd(),'package.json'));const entry=req.resolve('@fastls/fastls');const {Native}=req(path.join(path.dirname(entry),'native.js'));const n=Native.load();assert.ok(n.path.includes(path.join('@fastls',${JSON.stringify(record.native.name.split('/')[1])},'native')));const {Session}=req('@fastls/fastls');const s=new Session();try{assert.ok(s.listProfiles().length>0)}finally{s.close()}`;
  await run(['-e', probe], temporary, env);
  await run(['--input-type=module', '-e', "import {Session} from '@fastls/fastls';const s=new Session();s.close();"], temporary, env);
  for (const file of ['types.mts', 'types.cts']) copyFileSync(resolve(root, 'tests', file), resolve(temporary, file));
  await run([resolve(root, 'node_modules/typescript/bin/tsc'), '--ignoreConfig', '--noEmit', '--strict',
    '--module', 'NodeNext', '--target', 'ES2022', '--types', 'node', '--typeRoots', resolve(root, 'node_modules/@types'),
    'types.mts', 'types.cts'], temporary, env);
  const installedEnv = { ...env, FASTLS_NODE_PACKAGE: temporary };
  process.stdout.write(await run([resolve(root, 'scripts/test-cookie-compat.mjs')], temporary, installedEnv));
  const build = resolve(root, '../build'), previous = new Set(readdirSync(build));
  process.stdout.write(await run(['--expose-gc', '--test', '--test-concurrency=1',
    ...['native.test.mjs', 'sdk.test.mjs', 'fetch.test.mjs', 'parity.test.mjs'].map(file => resolve(root, 'tests', file))], temporary, installedEnv));
  // Keep the split-package evidence separate from the embedded candidate's
  // evidence, so GitHub candidate signing still validates exactly one suite.
  const evidence = readdirSync(build).filter(file => !previous.has(file) && /^node-(?:parity-)?package-.*\.json$/.test(file));
  assert.equal(evidence.length, 2, 'Expected SDK and parity evidence');
  for (const file of evidence) {
    copyFileSync(resolve(build, file), resolve(destination, file));
    rmSync(resolve(build, file));
  }
  const metadataPath = resolve(temporary, 'node_modules', record.native.name, 'package.json');
  const original = readFileSync(metadataPath, 'utf8');
  writeFileSync(metadataPath, JSON.stringify({ ...JSON.parse(original), version: '0.0.0-mismatch' }));
  await run(['-e', "const assert=require('node:assert/strict');const {Session}=require('@fastls/fastls');assert.throws(()=>new Session(),e=>/version mismatch/.test(String(e.cause)))"], temporary, env);
  writeFileSync(metadataPath, original);
  rmSync(resolve(temporary, 'node_modules', record.native.name), { recursive: true });
  await run(['-e', "const assert=require('node:assert/strict');const {Session}=require('@fastls/fastls');assert.throws(()=>new Session(),e=>/optional package/.test(String(e.cause)))"], temporary, env);
  const nativeDirectory = resolve(temporary, 'node_modules/@fastls');
  for (const platform of platforms.filter(p => p.name !== record.native.name)) {
    assert.throws(() => readFileSync(resolve(nativeDirectory, platform.name.split('/')[1], 'package.json')), /ENOENT/);
  }
  record.checks = { automaticPlatformInstall: true, noCompilerPath: true, installScriptsDisabled: true,
    commonjs: true, esm: true, types: true, installedSuite: true, cookieCompatibility: true,
    nativeVersionGate: true, missingOptionalGate: true, otherPlatformsOmitted: true };
  record.evidence = evidence;
  writeFileSync(resolve(destination, 'npm-package-info.json'), JSON.stringify(record, null, 2) + '\n');
  console.log(`Verified npm optional-dependency install: ${record.native.name}`);
} finally {
  server.closeAllConnections(); await new Promise(resolvePromise => server.close(resolvePromise));
  rmSync(temporary, { recursive: true, force: true });
}
