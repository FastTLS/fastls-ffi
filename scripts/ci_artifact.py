"""Manifest for transferring a native build to independent consumer jobs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def revision(path):
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path, text=True).strip()

def check(write=False):
    target = '-'.join(subprocess.check_output(['go','env','GOHOSTOS','GOHOSTARCH'],text=True).split())
    name = 'fastls.dll' if os.name=='nt' else 'libfastls.dylib' if sys.platform=='darwin' else 'libfastls.so'
    paths = [f'dist/{name}', 'dist/fastls.h', 'dist/build-info.json', 'go.work', 'build/ffi-fixture.exe' if os.name=='nt' else 'build/ffi-fixture']
    refs = json.loads((ROOT/'ci/dependencies.json').read_text())
    sources = {'ffi': revision(ROOT)}
    for name in ['fastls','fhttp','imitate']:
        sources[name] = revision(ROOT.parent/name)
        if sources[name] != (os.environ.get(name.upper()+'_REF') or refs[name+'_ref']): raise ValueError('checkout differs from dependency lock: '+name)
    data = {'schema':1,'target':target,'sources':sources,'go':subprocess.check_output(['go','version'],text=True).strip(),
            'files':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}}
    manifest = ROOT/'build/native-artifact.json'
    if write: manifest.write_text(json.dumps(data,indent=2)+'\n')
    elif data != json.loads(manifest.read_text()): raise ValueError('native artifact source, platform, toolchain or file hash mismatch')
    print(json.dumps({'native_artifact':'created' if write else 'verified','target':target,'revision':sources['ffi']}),flush=True)

if __name__=='__main__': check('--write' in sys.argv)
