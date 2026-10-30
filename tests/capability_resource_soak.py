"""One bounded mixed-protocol resource check for the frozen native candidate."""
import json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from fastls import Session,ProfileSpec
from test_stability import resources,SLAB

duration=os.environ.get('FASTLS_EXTENDED_SOAK_SECONDS','300')
if not duration.isascii() or not duration.isdigit() or not 300<=int(duration)<=7200:
    raise ValueError('resource observation requires 300..7200 seconds')
duration=int(duration)
# Reuse the existing short-regression RSS/handle growth budgets; no forced GC.
contract=dict(seconds=duration,peak_rss_growth=128*1024*1024,final_rss_growth=96*1024*1024,final_handles_growth=64,forced_gc=False)
fixture=subprocess.Popen([str(ROOT/'build'/('ffi-fixture.exe' if os.name=='nt' else 'ffi-fixture'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
monitor=Session();clients=[];samples=[];requests=0
output=ROOT/'build'/('capability-resource-'+str(time.time_ns())+'.json')
output.write_text(json.dumps(dict(success=False,state='starting',contract=contract))+'\n')
def create(lab):
    value=json.loads((ROOT/'tests/fixtures/profile_spec.json').read_text())
    value['http2'].update(first_stream_id=3,stream_window_increment=12451840)
    value['http2']['settings']=[{'id':2,'value':0},{'id':4,'value':131072}]
    value['capabilities']['required_features']=list(value['capabilities'].get('required_features') or [])+['http2.stream.v1']
    spec=ProfileSpec(value)
    return [Session(address_policy={'allow_cidrs':['127.0.0.0/8']},notifications='poll'),
            Session(profile_spec=spec,ca_file=lab['ca_file'],notifications='poll'),
            Session(http_version='h3',address_policy={'allow_cidrs':['127.0.0.0/8']},ca_file=lab['ca_file'],notifications='poll')]
def sample():return dict(resources(),native=monitor.stats())
try:
    lab=json.loads(fixture.stdout.readline());clients=create(lab)
    urls=[lab['http'],lab['https2'],lab['https3']]
    def cycle():
        global requests
        for index,(client,url) in enumerate(zip(clients,urls)):
            size=524288 if index==1 else 65536
            response=client.get(url+'/?n='+str(size))
            assert response.content==SLAB*(size//len(SLAB))
            requests+=1
    for _ in range(30):cycle()
    baseline=sample();started=time.monotonic();next_sample=started;rounds=0
    while time.monotonic()-started<duration:
        tick=time.monotonic();cycle();rounds+=1
        if rounds%24==0:
            for client in clients:client.close()
            clients=create(lab)
        if tick>=next_sample:
            samples.append(dict(elapsed=tick-started,**sample()));next_sample=tick+1
            if len(samples)%10==0:
                output.write_text(json.dumps(dict(success=False,state='running',contract=contract,requests=requests,baseline=baseline,samples=samples),indent=2)+'\n')
        time.sleep(max(0,.02-(time.monotonic()-tick)))
    for client in clients:client.close()
    clients=[]
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        native=monitor.stats()
        if native['open_requests']==0 and native['workers']==0 and native['open_sessions']==1 and native['session_pools']==0:break
        time.sleep(.02)
    final=sample();peak=max(s['rss_bytes'] or 0 for s in samples)
    record=dict(success=False,state='checking',contract=contract,elapsed_seconds=time.monotonic()-started,requests=requests,baseline=baseline,final=final,peak_rss=peak,samples=samples)
    output.write_text(json.dumps(record,indent=2)+'\n')
    assert native['open_requests']==0 and native['workers']==0 and native['open_sessions']==1 and native['session_pools']==0,native
    if baseline['rss_bytes'] is not None:
        assert peak-baseline['rss_bytes']<=contract['peak_rss_growth']
        assert final['rss_bytes']-baseline['rss_bytes']<=contract['final_rss_growth']
    if baseline['descriptors_or_handles'] is not None:
        assert final['descriptors_or_handles']-baseline['descriptors_or_handles']<=contract['final_handles_growth']
    record.update(success=True,state='complete');output.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(dict(success=True,requests=requests,seconds=record['elapsed_seconds'],evidence=str(output))))
finally:
    for client in clients:client.close()
    monitor.close();fixture.stdin.close()
    try:fixture.wait(timeout=10)
    finally:
        if fixture.poll() is None:fixture.kill();fixture.wait()
        fixture.stdout.close();fixture.stderr.close()
