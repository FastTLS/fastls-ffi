import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname, basename } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const npmCLI = process.env.npm_execpath;
if (!npmCLI) throw new Error('Run this script with npm run test:package');
const temporary = mkdtempSync(resolve(tmpdir(), 'fastls-node-package-'));
function run(args, cwd = root, env = process.env) {
  const processResult = spawnSync(process.execPath, args, {
    cwd, env, encoding: 'utf8', windowsHide: true, timeout: 180000,
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
  const [{ filename }] = JSON.parse(output);
  writeFileSync(resolve(temporary, 'package.json'), JSON.stringify({ name: 'fastls-install-test', private: true }));
  run([npmCLI, 'install', '--no-audit', '--no-fund', resolve(temporary, filename)], temporary);
  const name = process.platform === 'win32' ? 'fastls.dll' : process.platform === 'darwin' ? 'libfastls.dylib' : 'libfastls.so';
  const env = { ...process.env, FASTLS_NODE_PACKAGE: temporary,
    FASTLS_LIBRARY: process.env.FASTLS_LIBRARY ?? resolve(root, '../dist', name) };
  for (const file of ['types.mts', 'types.cts']) copyFileSync(resolve(root, 'tests', file), resolve(temporary, file));
  run([resolve(root, 'node_modules/typescript/bin/tsc'), '--ignoreConfig', '--noEmit', '--strict',
    '--module', 'NodeNext', '--target', 'ES2022', '--types', 'node',
    '--typeRoots', resolve(root, 'node_modules/@types'), 'types.mts', 'types.cts'], temporary, env);
  process.stdout.write(run(['--test', '--test-concurrency=1', ...[
    'native.test.mjs', 'sdk.test.mjs',
  ].map(file => resolve(root, 'tests', file))], temporary, env));
  // Exercise package exports by name, not just by absolute entry filenames.
  process.stdout.write(run(['--input-type=module', '-e',
    "import {Session} from '@fasttls/fastls'; import {createRequire} from 'node:module'; const cjs=createRequire(import.meta.url)('@fasttls/fastls'); if(Session!==cjs.Session)throw Error('entry mismatch'); const s=new Session(); try {if(!s.listProfiles().includes('chrome/156/windows'))throw Error('missing profile')}finally{s.close()}"], temporary, env));
} finally {
  if (dirname(temporary) !== resolve(tmpdir()) || !basename(temporary).startsWith('fastls-node-package-')) {
    throw new Error('Refusing to remove a directory outside the temporary package test');
  }
  rmSync(temporary, { recursive: true, force: true });
}
