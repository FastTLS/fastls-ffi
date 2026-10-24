import { spawnSync } from 'node:child_process';
import { cpSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(import.meta.dirname, '..');
export function run(args, cwd = root, env = process.env, executable = process.execPath) {
  const result = spawnSync(executable, args, { cwd, env, encoding: 'utf8', windowsHide: true, timeout: 600000 });
  if (result.status !== 0) {
    process.stdout.write(result.stdout ?? ''); process.stderr.write(result.stderr ?? '');
    throw result.error ?? new Error(`command failed with exit ${result.status}`);
  }
  return result.stdout;
}

export function packNative(destination, npmCLI = process.env.npm_execpath) {
  if (!npmCLI) throw Error('Run with npm run pack:native or npm run test:package');
  if (!['win32/x64', 'linux/x64', 'linux/arm64', 'darwin/x64', 'darwin/arm64'].includes(`${process.platform}/${process.arch}`)) throw Error('Unsupported native candidate platform');
  const temporary = mkdtempSync(resolve(tmpdir(), 'fastls-native-stage-'));
  try {
    run([npmCLI, 'run', 'build']);
    const stage = resolve(temporary, 'package'); mkdirSync(stage);
    const pkg = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
    // Never copy build/install hooks or development sources into the candidate.
    delete pkg.scripts; delete pkg.devDependencies;
    pkg.files = ['dist', 'native', 'README.md']; pkg.os = [process.platform]; pkg.cpu = [process.arch];
    if (process.platform === 'linux') pkg.libc = ['glibc'];
    pkg.private = true;
    writeFileSync(resolve(stage, 'package.json'), JSON.stringify(pkg, null, 2) + '\n');
    cpSync(resolve(root, 'dist'), resolve(stage, 'dist'), { recursive: true });
    copyFileSync(resolve(root, 'README.md'), resolve(stage, 'README.md'));
    const name = process.platform === 'win32' ? 'fastls.dll' : process.platform === 'darwin' ? 'libfastls.dylib' : 'libfastls.so';
    const native = resolve(stage, 'native'); mkdirSync(native);
    const library = resolve(root, '../dist', name);
    run([resolve(root, 'scripts/native-metadata.py'), native, process.platform, process.arch, library], root,
      { ...process.env, PYTHONUTF8: '1' }, process.env.PYTHON ?? 'python');
    copyFileSync(library, resolve(native, name));
    mkdirSync(destination, { recursive: true });
    const [packed] = JSON.parse(run([npmCLI, 'pack', '--json', '--ignore-scripts', '--pack-destination', destination], stage));
    if (basename(packed.filename) !== packed.filename) throw Error('Unsafe npm filename');
    const filename = packed.filename.replace(/\.tgz$/, `-${process.platform}-${process.arch}${process.platform === 'linux' ? '-glibc' : ''}.tgz`);
    renameSync(resolve(destination, packed.filename), resolve(destination, filename));
    return { ...packed, filename, native: JSON.parse(readFileSync(resolve(native, 'manifest.json'), 'utf8')) };
  } finally {
    if (dirname(temporary) !== resolve(tmpdir()) || !basename(temporary).startsWith('fastls-native-stage-')) throw Error('Unsafe staging cleanup');
    rmSync(temporary, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  console.log(JSON.stringify(packNative(resolve(root, '../dist')), null, 2));
}
