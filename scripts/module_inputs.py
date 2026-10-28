"""Capture checksum-verified formal modules, never sibling replacements."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

OWN = {'github.com/FastTLS/' + name: name for name in ('fastls', 'ftransport', 'imitate')}


def json_stream(text):
    decoder = json.JSONDecoder(); offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace(): offset += 1
        if offset == len(text): return
        value, offset = decoder.raw_decode(text, offset)
        yield value


def validate(selected, downloaded, reviewed):
    """Bind the compiler's module selection to version, sums and reviewed refs."""
    modules = []
    for module in selected:
        if module.get('Replace'): raise RuntimeError('formal module graph contains a replacement: ' + module['Path'])
        if module.get('Main'): continue
        path = module['Path']; version = module.get('Version')
        if not version or path == 'github.com/FastTLS/fhttp': raise RuntimeError('invalid formal module: ' + path)
        source = downloaded.get((path, version), {})
        if source.get('Error') or not all(source.get(k) for k in ('Sum', 'GoModSum')):
            raise RuntimeError('missing verified module checksums: ' + path)
        if any(module.get(key) and module[key] != source[key] for key in ('Sum', 'GoModSum')):
            raise RuntimeError('module selection/checksum mismatch: ' + path)
        record = {k: source[k] for k in ('Path', 'Version', 'Sum', 'GoModSum')}
        if source.get('Origin'): record['Origin'] = source['Origin']
        if source.get('VersionTag'): record['VersionTag'] = source['VersionTag']
        if path in OWN:
            origin = source.get('Origin', {}); name = OWN[path]
            if origin.get('VCS') != 'git' or origin.get('Hash') != reviewed.get(name + '_ref') or not re.fullmatch(r'[0-9a-f]{40}', origin.get('Hash', '')):
                raise RuntimeError('formal module differs from reviewed source: ' + path)
            if origin.get('URL', '').removesuffix('.git') != 'https://github.com/FastTLS/' + name or source.get('VersionTag') != dict(ref='refs/tags/' + version,commit=origin['Hash']):
                raise RuntimeError('formal module origin/tag mismatch: ' + path)
            directory = Path(source['Dir'])
            record['license_files'] = {p.name: hashlib.sha256(p.read_text(encoding='utf-8').replace('\r\n', '\n').encode()).hexdigest()
                for p in sorted(directory.iterdir()) if p.is_file() and re.match(r'^(LICENSE|COPYING|NOTICE|PATENTS)(?:[.-].*)?$', p.name, re.I)}
            if not any(p.upper().startswith(('LICENSE', 'COPYING')) for p in record['license_files']):
                raise RuntimeError('formal module has no license: ' + path)
        modules.append(record)
    if not set(OWN).issubset(m['Path'] for m in modules): raise RuntimeError('required formal FastTLS module missing')
    return sorted(modules, key=lambda m: m['Path'])


def capture(root, env):
    if env.get('GOWORK') != 'off': raise RuntimeError('formal native build requires GOWORK=off')
    def run(*args):return subprocess.check_output(args, cwd=root, env=env, text=True, encoding='utf-8')
    if run('git', 'status', '--porcelain').strip(): raise RuntimeError('commit formal native sources before building')
    downloaded = list(json_stream(run('go', 'mod', 'download', '-json', 'all')))
    # Bulk download can omit Origin. Individual cached metadata may retain a
    # branch used for the original commit lookup, so preserve it and separately
    # verify the actual immutable version tag rather than relabeling Origin.Ref.
    for index, module in enumerate(downloaded):
        if module['Path'].startswith('github.com/FastTLS/'):
            source = json.loads(run('go','mod','download','-json',module['Path']+'@'+module['Version']))
            origin=source.get('Origin',{})
            if not origin.get('URL') or not origin.get('Hash'):
                raise RuntimeError('formal module source origin missing: '+module['Path'])
            ref='refs/tags/'+(origin['Subdir']+'/' if origin.get('Subdir') else '')+module['Version']
            refs=dict(line.split()[::-1] for line in run('git','ls-remote','--tags',origin['URL'],ref,ref+'^{}').splitlines())
            commit=refs.get(ref+'^{}',refs.get(ref))
            if commit!=origin['Hash']:raise RuntimeError('formal version tag/source mismatch: '+module['Path'])
            source['VersionTag']=dict(ref=ref,commit=commit);downloaded[index]=source
    run('go', 'mod', 'verify')
    selected = list(json_stream(run('go', 'list', '-m', '-json', 'all')))
    reviewed = json.loads((root / 'ci/dependencies.json').read_text(encoding='utf-8'))
    return validate(selected, {(m['Path'], m.get('Version')): m for m in downloaded}, reviewed)


def source_commits(modules):
    return {OWN[m['Path']]: m['Origin']['Hash'] for m in modules if m['Path'] in OWN}
