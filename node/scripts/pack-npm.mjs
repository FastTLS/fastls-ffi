import { cpSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { run } from './pack-native.mjs';
import { platforms } from './npm-platforms.mjs';

const root = resolve(import.meta.dirname, '..');
export const sha256 = path => createHash('sha256').update(readFileSync(path)).digest('hex');

// Consume the exact candidate already exercised by test:package; never rebuild it.
export function packNpm(destination, npmCLI = process.env.npm_execpath) {
  if (!npmCLI) throw Error('Run with npm run pack:npm');
  const candidate = JSON.parse(readFileSync(resolve(root, '../dist/node-package-info.json'), 'utf8'));
  const pkg = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
  const archive = resolve(root, '../dist', candidate.package.file);
  if (candidate.package.file !== candidate.package.file.split(/[\\/]/).pop() || sha256(archive) !== candidate.package.sha256 || candidate.package.version !== pkg.version || candidate.package.name !== pkg.name || !Object.values(candidate.checks).every(Boolean)) {
    throw Error('Unverified native candidate; run npm run test:package first');
  }
  const target = platforms.find(p => p.os[0] === candidate.platform && p.cpu[0] === candidate.arch);
  if (!target) throw Error('Unsupported npm platform');
  mkdirSync(destination, { recursive: true });
  const temporary = mkdtempSync(resolve(tmpdir(), 'fastls-npm-stage-'));
  try {
    const nativeStage = resolve(temporary, 'native-package');
    const unpacked = JSON.parse(run([resolve(root, 'scripts/unpack-native.py'), archive, resolve(nativeStage, 'native')], root, process.env, process.env.PYTHON ?? 'python'));
    if (JSON.stringify(unpacked.native) !== JSON.stringify(candidate.native.manifest)) throw Error('Candidate manifest mismatch');
    const common = { version: pkg.version, license: pkg.license, repository: pkg.repository, publishConfig: pkg.publishConfig, engines: pkg.engines };
    const nativePkg = { ...common, ...target, description: `Fastls native library for ${target.name}`, files: ['native', 'LICENSE', 'README.md'] };
    writeFileSync(resolve(nativeStage, 'package.json'), JSON.stringify(nativePkg, null, 2) + '\n');
    copyFileSync(resolve(root, 'LICENSE'), resolve(nativeStage, 'LICENSE'));
    writeFileSync(resolve(nativeStage, 'README.md'), `# ${target.name}\n\nNative dependency of @fastls/fastls. Install the main package.\n\nSee native/THIRD_PARTY_NOTICES.txt and native/manifest.json for dependency licenses and system requirements.\n`);
    const mainStage = resolve(temporary, 'main-package'); mkdirSync(mainStage);
    const mainPkg = { ...pkg, optionalDependencies: Object.fromEntries(platforms.map(p => [p.name, pkg.version])) };
    delete mainPkg.scripts; delete mainPkg.devDependencies;
    writeFileSync(resolve(mainStage, 'package.json'), JSON.stringify(mainPkg, null, 2) + '\n');
    cpSync(resolve(root, 'dist'), resolve(mainStage, 'dist'), { recursive: true });
    for (const file of ['LICENSE', 'README.md']) copyFileSync(resolve(root, file), resolve(mainStage, file));
    const pack = directory => {
      const [packed] = JSON.parse(run([npmCLI, 'pack', '--ignore-scripts', '--json', '--pack-destination', destination], directory));
      return { name: packed.name, version: packed.version, file: packed.filename, sha256: sha256(resolve(destination, packed.filename)), metadata: JSON.parse(readFileSync(resolve(directory, 'package.json'), 'utf8')) };
    };
    const record = { schema: 1, revision: candidate.revision, platform: candidate.platform, arch: candidate.arch,
      main: pack(mainStage), native: pack(nativeStage), nativeManifest: unpacked.native, checks: {} };
    writeFileSync(resolve(destination, 'npm-package-info.json'), JSON.stringify(record, null, 2) + '\n');
    return record;
  } finally { rmSync(temporary, { recursive: true, force: true }); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  console.log(JSON.stringify(packNpm(resolve(root, '../dist/npm')), null, 2));
}
