"""Reject stale or modified build inputs before consumers load native code."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('artifact',Path(__file__).resolve().parents[1]/'scripts/ci_artifact.py')
artifact=importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)

class ArtifactGate(unittest.TestCase):
    def test_rejects_changed_files_sources_platform_and_toolchain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'ffi';(root/'dist').mkdir(parents=True);(root/'build').mkdir();(root/'ci').mkdir()
            (root/'ci/dependencies.json').write_text(json.dumps({k+'_ref':'a'*40 for k in ['fastls','fhttp','imitate']}))
            library='fastls.dll' if artifact.os.name=='nt' else 'libfastls.dylib' if artifact.sys.platform=='darwin' else 'libfastls.so'
            fixture='ffi-fixture.exe' if artifact.os.name=='nt' else 'ffi-fixture'
            for p in ['dist/'+library,'dist/fastls.h','dist/build-info.json','go.work','build/'+fixture]: (root/p).write_bytes(b'fixture')
            def command(args,**kwargs):
                if args[:2]==['git','rev-parse']:return ('b' if kwargs['cwd']==root else 'a')*40+'\n'
                return 'linux\namd64\n' if args[:2]==['go','env'] else 'go version fixture\n'
            with patch.object(artifact,'ROOT',root),patch.object(artifact.subprocess,'check_output',command),patch.dict(artifact.os.environ,{k+'_REF':'a'*40 for k in ['FASTLS','FHTTP','IMITATE']}):
                artifact.check(True);artifact.check()
                path=root/'build/native-artifact.json';original=path.read_text()
                for field in ['target','go','sources','files']:
                    value=json.loads(original);value[field]='tampered';path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):artifact.check()
                path.write_text(original);(root/'dist'/library).write_bytes(b'corrupt')
                with self.assertRaises(ValueError):artifact.check()

if __name__=='__main__':unittest.main(verbosity=2)
