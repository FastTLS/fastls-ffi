import { parity, session, fixture, assert, delay, sdk, healthy, eventually, control, randomUUID, Native, SLAB, repeatedHash, evidence, resources, monitor, root } from './harness.mjs';
import { writeFileSync, unlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
const p=(name,fn,adaptation)=>parity('test_stability.py',name,fn,adaptation);
p('test_verified_tls_h1_h2_and_ca_isolation',async t=>{
  for(const [protocol,expected] of [['https1','HTTP/1.1'],['https2','HTTP/2.0']]){const s=session(t);const r=await s.get(fixture[protocol]+'/redirect',{headers:{'User-Agent':'parity'}});assert.equal(r.json().protocol,expected);assert.deepEqual(r.json().headers['User-Agent'],['parity']);
    for(const caFile of [undefined,fixture.wrong_ca_file]){const bad=session(t,{caFile});await assert.rejects(bad.get(fixture[protocol]));bad.close();}}
});
p('test_invalid_ca_fails_before_registering_request',async t=>{
  const path=root+'/build/node-invalid-'+randomUUID()+'.pem';writeFileSync(path,'invalid certificate data');t.after(()=>unlinkSync(path));const s=session(t,{caFile:path});await assert.rejects(s.get(fixture.https2),/ca_pem/);assert.equal(s.stats().open_requests,0);assert.equal(s.stats().workers,0);
});
p('test_cancel_discards_unread_final_bytes',async t=>{const s=session(t),r=await s.stream('GET',fixture.https2+'/bytes?n=131072');const reader=r.body[Symbol.asyncIterator]();const first=(await reader.next()).value;assert.deepEqual(first.subarray(0,4),SLAB.subarray(0,4));r.cancel();await assert.rejects(reader.next());});
p('test_tls_streaming_close_isolation_and_cancellation',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),a=randomUUID(),b=randomUUID();const first=await s.stream('GET',fixture[protocol]+'/hold?id='+a),other=await s.stream('GET',fixture[protocol]+'/hold?id='+b);
    const ar=first.body[Symbol.asyncIterator](),br=other.body[Symbol.asyncIterator]();await ar.next();await br.next();first.close();await control('/release',b);assert.equal((await br.next()).value.toString(),'last!');assert.ok((await br.next()).done);await eventually(async()=>(await control('/state',a)).cancelled);
    const c=new AbortController(),id=randomUUID();const rejected=assert.rejects(s.get(fixture[protocol]+'/wait?id='+id,{signal:c.signal}),sdk.AbortError);await eventually(async()=>(await control('/state',id)).started);c.abort();await rejected;
  }
});
p('test_truncated_tls_bodies_are_errors',async t=>{for(const protocol of ['https1','https2'])await assert.rejects(session(t).get(fixture[protocol]+'/truncated'),sdk.FastlsError);});
p('test_large_stream_backpressure_and_buffered_limit',async t=>{
  const s=session(t,{timeoutMs:90000}),id=randomUUID(),r=await s.stream('GET',fixture.https2+'/bytes?n=67108864&id='+id);await delay(150);assert.ok(s.stats().buffered_body_bytes<=3*65536);assert.equal((await control('/state',id)).done,false);
  const hash=createHash('sha256');let bytes=0;for await(const chunk of r.body){hash.update(chunk);bytes+=chunk.length;}assert.equal(bytes,64<<20);assert.equal(hash.digest('hex'),repeatedHash());await assert.rejects(s.get(fixture.http+'/bytes?n=34603008'),/32MiB/);
});
p('test_registry_limit_and_recovery',async t=>{
  const native=Native.load(),handles=[];try{for(let i=0;i<256;i++)handles.push(native.start({url:fixture.http+'/bytes',timeout_ms:5000}));assert.throws(()=>native.start({url:fixture.http}),/too many open requests/);native.call('close',handles.pop());handles.push(native.start({url:fixture.http}));assert.equal(monitor.stats().open_requests,256);}finally{for(const id of handles)native.call('close',id);}await healthy(session(t));
});
p('test_fragmented_sse_and_upload_boundaries',async t=>{
  const s=session(t);assert.equal((await s.get(fixture.https2+'/sse')).text(),'data: 第一条\n\ndata: second\n\n');const bytes=Buffer.alloc(512*1024);for(let i=0;i<bytes.length;i+=2)bytes[i+1]=255;
  assert.deepEqual((await s.post(fixture.https2+'/echo',{body:bytes})).content,bytes);const large=Buffer.concat([bytes,Buffer.from('x')]);assert.equal((await s.post(fixture.https2+'/upload',{body:large})).json().bytes,large.length);assert.throws(()=>Native.load().start({url:fixture.https2+'/upload',body:'x'.repeat(512*1024+1)}),/512KiB/);
});
p('test_concurrent_cancel_and_session_close',async t=>{
  const s=session(t);await Promise.all(Array.from({length:128},async(_,i)=>{if(i%2){const id=randomUUID(),c=new AbortController();const rejected=assert.rejects(s.get(fixture.https2+'/wait?id='+id,{signal:c.signal}),sdk.AbortError);await eventually(async()=>(await control('/state',id)).started);c.abort();await rejected;}else assert.deepEqual((await s.get(fixture.https2+'/bytes?n=32768')).content,SLAB.subarray(0,32768));}));
  const ids=Array.from({length:32},()=>randomUUID()),tasks=ids.map(id=>assert.rejects(s.get(fixture.https2+'/wait?id='+id),sdk.FastlsError));await Promise.all(ids.map(id=>eventually(async()=>(await control('/state',id)).started)));s.close();await Promise.all(tasks);
});
p('test_multithreaded_sync_session',async t=>{const s=session(t);const all=await Promise.all(Array.from({length:96},()=>s.get(fixture.https1+'/bytes?n=4096')));assert.ok(all.every(r=>r.content.equals(SLAB.subarray(0,4096))));},'Adapted to 96 concurrent Promise requests. Shared synchronous sessions and worker_threads are outside the Node API; this is not a thread-safety claim.');
p('test_soak_and_resource_recovery',async t=>{
  const seconds=Number(process.env.FASTLS_NODE_SOAK_SECONDS??30);assert.ok(Number.isInteger(seconds)&&seconds>=1&&seconds<=300);
  const s=session(t);const batch=async()=>{const all=await Promise.all(Array.from({length:32},()=>s.get(fixture.https2+'/bytes?n=4096')));assert.ok(all.every(r=>r.content.equals(SLAB.subarray(0,4096))));};
  for(let i=0;i<3;i++)await batch();global.gc?.();await eventually(()=>monitor.stats().workers===0);const baseline=resources(),samples=[];const started=performance.now();let requests=0;
  while(performance.now()-started<seconds*1000){await batch();requests+=32;for(let i=0;i<32;i++)assert.ok(s.listProfiles().includes('chrome/156/windows'));await eventually(()=>monitor.stats().workers===0);samples.push(resources());await delay(100);}
  evidence.measurements.soak={seconds:(performance.now()-started)/1000,requests,concurrency:32,baseline,samples};
  const last=samples.at(-1);assert.ok(last.goroutines<=baseline.goroutines+16);assert.ok(Math.max(...samples.map(s=>s.heap_inuse_bytes))<=baseline.heap_inuse_bytes+96*1024*1024);
  assert.ok(Math.max(...samples.map(s=>s.rss_bytes))<=baseline.rss_bytes+128*1024*1024);assert.ok(last.rss_bytes<=baseline.rss_bytes+96*1024*1024);assert.ok(last.descriptors_or_handles<=baseline.descriptors_or_handles+64);
},'Node retains its ordinary 30/300-second resource observations and unchanged limits. The optional longer Python certification workload is separate, with its own recorded duration and cadence; this mapping does not claim a one-hour Node run.');
