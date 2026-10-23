import { parity, session, fixture, assert, delay, sdk, healthy, eventually, control, randomUUID, readFileSync, root, SLAB, repeatedHash, evidence } from './harness.mjs';
import { createHash } from 'node:crypto';
import { wire } from './wire.mjs';
const p=(name,fn)=>parity('test_decode_timeouts.py',name,fn);
const vectors=JSON.parse(readFileSync(root+'/tests/fixtures/decode-vectors.json','utf8'));
const payload=Buffer.from(vectors.payload_base64,'base64');
const encoded=Object.fromEntries(Object.entries(vectors.cases).map(([name,value])=>[name,Buffer.from(value,'base64')]));
const send=(s,base,name,options={})=>s.post(base+'/encoded-echo?encoding='+encodeURIComponent({'raw-deflate':'deflate','concatenated-gzip':'gzip'}[name]??name),{body:encoded[name],...options});
const phase=name=>error=>error instanceof sdk.FastlsTimeoutError&&error.phase===name;
p('test_independent_codec_vectors_and_wire_headers',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t);for(const name of Object.keys(encoded)){const r=await send(s,fixture[protocol],name);assert.deepEqual(r.content,payload);assert.ok(r.decoded);assert.equal(r.headers.has('content-encoding'),false);assert.equal(r.headers.has('content-length'),false);assert.ok(r.wireHeaders.get('content-encoding'));assert.equal(Number(r.wireHeaders.get('content-length')),encoded[name].length);}}
});
p('test_raw_mode_and_request_override_keep_exact_compressed_bytes',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t,{decodeContent:false});const raw=await send(s,fixture[protocol],'gzip');assert.deepEqual(raw.content,encoded.gzip);assert.equal(raw.decoded,false);assert.equal(raw.headers.get('content-encoding'),'gzip');const decoded=await send(s,fixture[protocol],'gzip',{decodeContent:true});assert.deepEqual(decoded.content,payload);assert.equal(raw.headers.get('x-test-connection'),decoded.wireHeaders.get('x-test-connection'));}
});
p('test_truncation_and_checksum_errors_are_not_eof',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t);for(const [name,bytes] of Object.entries(encoded)){if(name==='gzip, br')continue;await assert.rejects(send(s,fixture[protocol],name,{body:bytes.subarray(0,-3)}),sdk.FastlsDecodeError);}const bad=Buffer.from(encoded.gzip);bad[bad.length-8]^=1;await assert.rejects(send(s,fixture[protocol],'gzip',{body:bad}),sdk.FastlsDecodeError);await healthy(s,protocol);}
});
p('test_decoded_limit_exact_boundary_and_unsupported_encoding',async t=>{
  const s=session(t);for(const name of Object.keys(encoded)){assert.deepEqual((await send(s,fixture.https2,name,{maxDecodedBytes:payload.length})).content,payload);await assert.rejects(send(s,fixture.https2,name,{maxDecodedBytes:payload.length-1}),sdk.FastlsDecodeError);}
  for(const name of ['unknown','gzip, unknown','gzip, gzip, gzip, gzip, gzip'])await assert.rejects(send(s,fixture.https2,name,{body:'test'}),sdk.FastlsDecodeError);
  assert.equal((await send(s,fixture.https2,'unknown',{body:'raw',decodeContent:false})).text(),'raw');
});
p('test_compressed_stream_first_chunk_precedes_server_finish',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),id=randomUUID(),r=await s.stream('GET',fixture[protocol]+'/gzip-hold?id='+id);const reader=r.body[Symbol.asyncIterator]();assert.equal((await reader.next()).value.toString(),'first');assert.equal((await control('/state',id)).done,false);await control('/release',id);assert.equal((await reader.next()).value.toString(),'last!');assert.ok((await reader.next()).done);}
});
p('test_64_mib_decoded_stream_hash_buffer_bound_and_buffered_cap',async t=>{
  const s=session(t,{timeoutMs:90000}),r=await s.stream('GET',fixture.https2+'/gzip-stream?n=67108864');await delay(100);assert.ok(s.stats().buffered_body_bytes<=3*65536);let bytes=0;const hash=createHash('sha256');for await(const chunk of r.body){bytes+=chunk.length;hash.update(chunk);}assert.equal(bytes,64<<20);assert.equal(hash.digest('hex'),repeatedHash());await assert.rejects(s.get(fixture.https2+'/gzip-stream?n=34603008'),/32MiB/);evidence.measurements.decoded_bytes=bytes;
});
p('test_connect_timeout_during_tls_handshake',async t=>{const peer=await wire(t),s=session(t);await assert.rejects(s.get('https://'+peer.address,{timeoutMs:{total:4000,connect:300}}),phase('connect'));assert.ok(peer.accepted);await eventually(()=>peer.disconnected);});
p('test_headers_timeout_h1_h2_and_per_request_policy',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t,{timeoutMs:{total:5000,headers:300}});await healthy(s,protocol);await assert.rejects(s.get(fixture[protocol]+'/wait?id='+randomUUID()),phase('headers'));await healthy(s,protocol);assert.equal((await s.get(fixture[protocol]+'/bytes?n=4',{timeoutMs:2000})).content.length,4);}
});
p('test_redirect_hop_phase_resets_but_total_deadline_spans_hops',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),url=fixture[protocol]+'/delay-redirect?n=2';assert.equal((await s.get(url,{timeoutMs:{total:3000,headers:250}})).text(),'done');await assert.rejects(s.get(url,{timeoutMs:{total:250,headers:300}}),phase('total'));}
});
p('test_read_timeout_h1_h2_and_lazy_gzip_header',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t,{timeoutMs:{total:5000,headers:250,read:400}});for(const path of ['/hold?id='+randomUUID(),'/encoded-wait']){const r=await s.stream('GET',fixture[protocol]+path);await assert.rejects(r.read(),phase('read'));}}
});
p('test_read_timeout_resets_on_progress_total_deadline_does_not',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t);assert.equal((await s.get(fixture[protocol]+'/drip',{timeoutMs:{total:3000,read:250}})).text(),'abcde'.repeat(6));await assert.rejects(s.get(fixture[protocol]+'/drip',{timeoutMs:{total:250,read:200}}),phase('total'));}
});
p('test_slow_consumer_does_not_spend_read_budget',async t=>{
  const s=session(t,{timeoutMs:{total:5000,read:200}}),r=await s.stream('GET',fixture.https2+'/bytes?n=67108864');await eventually(()=>s.stats().buffered_body_bytes>0);await delay(500);assert.ok(s.stats().buffered_body_bytes<=3*65536);const reader=r.body[Symbol.asyncIterator]();assert.deepEqual((await reader.next()).value.subarray(0,4),SLAB.subarray(0,4));r.cancel();
});
p('test_async_read_timeout_preserves_other_h2_stream_and_loop',async t=>{
  const s=session(t,{timeoutMs:{total:5000,read:250}});const warm=await s.get(fixture.https2+'/bytes?n=4');let ticks=0;const timer=setInterval(()=>ticks++,20);
  try{const rejected=assert.rejects(s.get(fixture.https2+'/hold?id='+randomUUID()),phase('read'));const good=s.get(fixture.https2+'/drip');await rejected;assert.ok(ticks>2);const r=await good;assert.equal(r.text(),'abcde'.repeat(6));assert.equal(r.headers.get('x-test-connection'),warm.headers.get('x-test-connection'));}finally{clearInterval(timer);}
});
p('test_head_and_identity_are_not_decoded',async t=>{const s=session(t);const r=await s.request('HEAD',fixture.https2+'/encoded-echo?encoding=gzip');assert.equal(r.content.length,0);assert.equal(r.decoded,false);const plain=await send(s,fixture.https2,'identity',{body:'plain'});assert.equal(plain.text(),'plain');assert.equal(plain.decoded,false);});
p('test_invalid_policy_and_decoded_limit_fail_before_native_registration',async t=>{
  const s=session(t);for(const value of [0,-1,true,NaN,Infinity])await assert.rejects(s.get(fixture.https2,{timeoutMs:{total:5000,read:value}}));for(const value of [0,-1,true,2**41])await assert.rejects(s.get(fixture.https2,{maxDecodedBytes:value}));assert.equal(s.stats().open_requests,0);
});
