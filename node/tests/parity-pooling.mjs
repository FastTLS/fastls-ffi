import { parity, session, fixture, assert, delay, sdk, healthy, eventually, control, randomUUID, readFileSync } from './harness.mjs';
import { wire } from './wire.mjs';
const p=(name,fn)=>parity('test_pooling.py',name,fn);
const connection=r=>{const id=r.headers.get('x-test-connection');assert.ok(id&&id!=='<nil>');return id;};
p('test_sequential_reuse_and_idle_close',async t=>{
  for(const protocol of ['http','https1','https2']){const s=session(t),url=fixture[protocol]+'/bytes?n=4',ids=[];
    for(let i=0;i<6;i++)ids.push(connection(await s.get(url,{headers:{'X-Sequence':String(i)}})));
    assert.equal(new Set(ids).size,1);s.closeIdleConnections();assert.notEqual(connection(await s.get(url)),ids[0]);}
});
p('test_sessions_profiles_and_trust_are_isolated',async t=>{
  const s=session(t),other=session(t),url=fixture.https2+'/bytes?n=4';const a=connection(await s.get(url));assert.notEqual(connection(await other.get(url)),a);
  const profile=s.listProfiles().find(id=>id.startsWith('chrome/')&&id!=='chrome/156/windows');const b=connection(await s.get(url,{profile}));assert.notEqual(a,b);
  assert.equal(connection(await s.get(url)),a);assert.equal(connection(await s.get(url,{profile})),b);
  const roots=s.caPem;s.caPem=readFileSync(fixture.wrong_ca_file,'utf8');await assert.rejects(s.get(url));s.caPem=roots;assert.equal(connection(await s.get(url)),a);
});
p('test_first_request_headers_do_not_become_pool_defaults',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),url=fixture[protocol]+'/inspect';const first=await s.get(url,{headers:{'User-Agent':'first-only','X-Only':'first',Cookie:'manual=first'}});
    const second=await s.get(url),headers=second.json().headers;assert.notDeepEqual(headers['User-Agent'],['first-only']);assert.equal(headers['X-Only'],undefined);assert.equal(headers.Cookie,undefined);assert.equal(connection(first),connection(second));}
});
p('test_h2_multiplex_cancel_and_close_idle_preserve_other_stream',async t=>{
  const s=session(t),base=fixture.https2;const warm=connection(await s.get(base+'/bytes?n=4'));
  const streams=await Promise.all(Array.from({length:24},async()=>{const id=randomUUID(),r=await s.stream('GET',base+'/hold?id='+id);assert.equal(connection(r),warm);const reader=r.body[Symbol.asyncIterator]();assert.equal((await reader.next()).value.toString(),'first');return {id,r,reader};}));
  s.closeIdleConnections();await Promise.all(streams.map(async({id,r,reader},i)=>{if(i%2){r.cancel();await assert.rejects(reader.next());}else{await control('/release',id);assert.equal((await reader.next()).value.toString(),'last!');assert.ok((await reader.next()).done);}}));
  assert.equal(connection(await s.get(base+'/bytes?n=4')),warm);
});
p('test_session_close_cancels_active_body_and_recovers_registry',async t=>{
  const s=session(t),r=await s.stream('GET',fixture.https2+'/hold?id='+randomUUID());const reader=r.body[Symbol.asyncIterator]();await reader.next();s.close();s.close();await assert.rejects(reader.next());await assert.rejects(s.get(fixture.https2));
});
p('test_discovery_waiter_deadline_and_cached_origin_progress',async t=>{
  const s=session(t),peer=await wire(t),url=fixture.https2+'/bytes?n=4',warm=connection(await s.get(url)),c=new AbortController();
  const stalled=assert.rejects(s.get('https://'+peer.address,{signal:c.signal}),sdk.AbortError);await eventually(()=>peer.accepted);
  await assert.rejects(s.get('https://'+peer.address,{timeoutMs:300}),sdk.FastlsTimeoutError);
  assert.equal(connection(await s.get(url)),warm);c.abort();await stalled;await healthy(s);
});
