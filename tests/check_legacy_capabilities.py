"""Check new SDK behavior against an actual, explicitly selected legacy ABI."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from fastls import Session,ProfileSpec,FastlsError

p=argparse.ArgumentParser();p.add_argument('library');args=p.parse_args()
fixture=subprocess.Popen([str(ROOT/'build'/('ffi-fixture.exe' if sys.platform=='win32' else 'ffi-fixture'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
    lab=json.loads(fixture.stdout.readline())
    spec=ProfileSpec(json.loads((ROOT/'tests/fixtures/profile_spec.json').read_text()))
    with Session(library=args.library,ca_file=lab['ca_file']) as session:
        assert session.get(lab['https2']+'/status?code=204').status_code==204
        baseline=session.stats()['open_sessions']
        for config in ({'address_policy':{}},{'profile_spec':spec},{'http_version':'h3'}):
            try:Session(library=args.library,**config)
            except FastlsError as error:assert error.code=='unsupported_capability'
            else:raise AssertionError('Legacy library accepted new capability')
        for config in ({'profile_spec':spec},{'http_version':'h3'}):
            try:session.get(lab['https2'],**config)
            except FastlsError as error:assert error.code=='unsupported_capability'
            else:raise AssertionError('Legacy request ignored new capability')
        assert session.stats()['open_sessions']==baseline
        assert session.stats()['open_requests']==0
    print(json.dumps(dict(success=True,legacy_library=str(Path(args.library).resolve()),new_capabilities_rejected=5,default_request_passed=True)))
finally:
    fixture.stdin.close()
    try:fixture.wait(timeout=10)
    finally:
        if fixture.poll() is None:fixture.kill();fixture.wait()
        fixture.stdout.close();fixture.stderr.close()
