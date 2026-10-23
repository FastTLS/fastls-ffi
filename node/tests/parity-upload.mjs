import { parity, session, fixture, assert, delay, sdk, healthy, eventually, control, randomUUID, root, SLAB, sha, repeatedHash, evidence } from './harness.mjs';
import { Readable } from 'node:stream';
import { createReadStream, writeFileSync, unlinkSync } from 'node:fs';
const p=(name,fn,adaptation)=>parity('test_upload.py',name,fn,adaptation);
function* slabs(count){for(let i=0;i<count;i++)yield SLAB;}
const phase=name=>error=>error instanceof sdk.FastlsTimeoutError&&error.phase===name;
const stalled=signal=>(async function*(){yield SLAB;await delay(60000,null,{signal});})();
p('test_64_mib_sync_h1_h2_bounded_queue',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t,{timeoutMs:60000});let peak=0;
    function* source(){for(let i=0;i<1024;i++){peak=Math.max(peak,s.stats().buffered_upload_bytes);yield SLAB;}}
    const result=(await s.post(fixture[protocol]+'/upload',{body:source()})).json();assert.equal(result.bytes,64<<20);assert.equal(result.sha256,repeatedHash());assert.equal(result.length,-1);assert.equal(result.protocol,protocol==='https1'?'HTTP/1.1':'HTTP/2.0');assert.ok(peak<=65536);evidence.measurements['upload_'+protocol]={...result,peak};}
},'Synchronous iterators feed the Promise API; Node has no blocking Session API.');
p('test_64_mib_async_and_event_loop',async t=>{
  const s=session(t,{timeoutMs:60000});let ticks=0;const timer=setInterval(()=>ticks++,1);
  try{async function* source(){yield* slabs(1024);}const result=(await s.post(fixture.https2+'/upload',{body:source()})).json();assert.equal(result.bytes,64<<20);assert.equal(result.sha256,repeatedHash());assert.ok(ticks>5);}finally{clearInterval(timer);}
});
p('test_files_large_bytes_and_unicode',async t=>{
  const s=session(t);for(const body of [Buffer.concat(Array(10).fill(SLAB)),'中文😀'.repeat(100000)]){const result=(await s.post(fixture.https2+'/upload',{body})).json();assert.equal(result.sha256,sha(Buffer.from(body)));}
  const file=root+'/build/node-upload-'+randomUUID()+'.bin';writeFileSync(file,Buffer.concat(Array(20).fill(SLAB)));t.after(()=>unlinkSync(file));
  const result=(await s.post(fixture.https1+'/upload',{body:createReadStream(file)})).json();assert.equal(result.bytes,20*SLAB.length);assert.equal(result.sha256,repeatedHash(20));
},'Node file streams follow Readable ownership and auto-close semantics; Python file objects remain caller-owned.');
p('test_empty_and_oversized_chunks',async t=>{
  const s=session(t);for(const chunks of [[],[Buffer.alloc(0),Uint8Array.of(97),Buffer.from('b'),Buffer.concat(Array(3).fill(SLAB))]]){
    const result=(await s.post(fixture.https2+'/upload',{body:chunks})).json();assert.equal(result.sha256,sha(Buffer.concat(chunks)));}
});
p('test_sync_source_failure_cleans_up',async t=>{const s=session(t);function* source(){yield SLAB;throw Error('producer failed');}await assert.rejects(s.post(fixture.https2+'/upload',{body:source()}),/producer failed/);await healthy(s);});
p('test_async_source_failure_cleans_up',async t=>{const s=session(t);async function* source(){yield SLAB;throw Error('async producer failed');}await assert.rejects(s.post(fixture.https2+'/upload',{body:source()}),/async producer failed/);await healthy(s);});
p('test_async_cancel_waiting_producer_and_h2_sibling',async t=>{
  const s=session(t),c=new AbortController();let finalized=false;
  const body=signal=>(async function*(){try{yield SLAB;await delay(60000,null,{signal});}finally{finalized=true;}})();
  const request=assert.rejects(s.post(fixture.https2+'/upload',{body,signal:c.signal}),sdk.AbortError);await delay(150);await healthy(s);c.abort();await request;await eventually(()=>finalized);
  let cleanup=false;const control=new AbortController();const iterator={next(){return Promise.resolve({done:false,value:SLAB});},return(){cleanup=true;return new Promise(()=>{});},[Symbol.asyncIterator](){return this;}};
  const closing=assert.rejects(s.post(fixture.https2+'/upload-reject',{body:iterator,signal:control.signal}),sdk.AbortError);await eventually(()=>cleanup);control.abort();await closing;
},'Use the upload factory AbortSignal to cancel producer I/O; JavaScript cannot forcibly interrupt an arbitrary awaited Promise.');
p('test_total_timeout_includes_stalled_async_producer',async t=>{const s=session(t);await assert.rejects(s.post(fixture.https2+'/upload',{body:stalled,timeoutMs:200}),phase('total'));});
p('test_headers_timeout_starts_after_upload',async t=>{const s=session(t);async function* body(){yield SLAB;await delay(200);yield SLAB;}const r=await s.post(fixture.https2+'/upload',{body:body(),timeoutMs:{total:5000,headers:150}});assert.equal(r.json().bytes,2*SLAB.length);});
p('test_slow_receiver_backpressure',async t=>{const s=session(t,{timeoutMs:30000});const result=(await s.post(fixture.https2+'/upload-slow',{body:slabs(64)})).json();assert.equal(result.bytes,4<<20);assert.equal(result.sha256,repeatedHash(64));});
p('test_write_timeout_waiting_producer',async t=>{for(const protocol of ['https1','https2'])await assert.rejects(session(t).post(fixture[protocol]+'/upload',{body:stalled,timeoutMs:{total:5000,write:200}}),phase('write'));});
p('test_write_timeout_receiver_not_reading',async t=>{
  for(const protocol of ['https1','https2']){const id=randomUUID();try{await assert.rejects(session(t).post(fixture[protocol]+'/wait?id='+id,{body:slabs(4096),timeoutMs:{total:10000,write:250}}),phase('write'));}finally{await control('/release',id);}}
});
p('test_write_progress_resets_inactivity_clock',async t=>{const s=session(t);async function* body(){for(let i=0;i<8;i++){yield SLAB;await delay(80);}}const r=await s.post(fixture.https2+'/upload',{body:body(),timeoutMs:{total:5000,write:300}});assert.equal(r.json().bytes,SLAB.length*8);});
p('test_redirect_does_not_replay_stream',async t=>{const s=session(t);assert.equal((await s.post(fixture.https2+'/redirect-code?code=307',{body:slabs(1)})).status,307);assert.equal((await s.post(fixture.https2+'/redirect-code?code=302',{body:slabs(128)})).json().method,'GET');async function* forever(){while(true)yield SLAB;}assert.equal((await s.post(fixture.https2+'/redirect-code?code=302',{body:forever()})).json().method,'GET');});
p('test_invalid_chunks_headers_and_async_in_sync',async t=>{
  const s=session(t);for(const body of [['text'],[123]])await assert.rejects(s.post(fixture.https2+'/upload',{body}),TypeError);
  for(const name of ['Content-Length','Transfer-Encoding'])await assert.rejects(s.post(fixture.https2+'/upload',{body:slabs(1),headers:{[name]:'1'}}),sdk.FastlsError);
  async function* body(){yield SLAB;}assert.equal((await s.post(fixture.https2+'/upload',{body:body()})).json().bytes,SLAB.length);
},'Node has one Promise API, so async iterators are accepted rather than rejected by a nonexistent synchronous API.');
p('test_early_response_stops_async_producer',async t=>{
  const s=session(t);let closed=false;const body=signal=>(async function*(){try{yield SLAB;await delay(60000,null,{signal});}finally{closed=true;}})();
  assert.equal((await s.post(fixture.https2+'/upload-reject',{body})).status,413);await eventually(()=>closed);
  let syncClosed=false;function* sync(){try{while(true)yield SLAB;}finally{syncClosed=true;}}
  assert.equal((await s.post(fixture.https2+'/upload-reject',{body:sync()})).status,413);assert.ok(syncClosed);
  await assert.rejects(s.post(fixture.https2+'/upload-early-ok',{body:stalled}),/duplex/);
  const c=new AbortController(),readable=Readable.from(stalled(c.signal));const pending=assert.rejects(s.post(fixture.https2+'/upload',{body:readable,signal:c.signal}),sdk.AbortError);await delay(30);c.abort();await pending;assert.ok(readable.destroyed);
},'Abort-aware factories allow deterministic generator finalization; uncooperative promises remain caller-owned.');
