import { parity, session, fixture, assert, delay, sdk, healthy, eventually, SLAB, repeatedHash, evidence } from './harness.mjs';
import { createHash } from 'node:crypto';
const p=(name,fn,adaptation)=>parity('test_duplex.py',name,fn,adaptation);
const phase=name=>error=>error instanceof sdk.FastlsTimeoutError&&error.phase===name;
const paused=signal=>(async function*(){await delay(60000,null,{signal});yield SLAB;})();
function gate(promise,signal){return new Promise((resolve,reject)=>{const abort=()=>reject(signal.reason);signal.addEventListener('abort',abort,{once:true});promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));if(signal.aborted)abort();});}

p('test_async_first_reply_before_upload_and_half_close',async t=>{
  const s=session(t),ready=Promise.withResolvers(),echoed=Promise.withResolvers();
  const body=signal=>(async function*(){await gate(ready.promise,signal);yield Buffer.from('first');await gate(echoed.promise,signal);yield Buffer.from('last');})();
  const response=await s.stream('POST',fixture.https2+'/duplex',{duplex:true,body});const reader=response.body[Symbol.asyncIterator]();
  assert.equal((await reader.next()).value.toString(),'ready\n');ready.resolve();assert.equal((await reader.next()).value.toString(),'first');echoed.resolve();
  const rest=[];for(let item=await reader.next();!item.done;item=await reader.next())rest.push(item.value);assert.equal(Buffer.concat(rest).toString(),'last');await response.waitUpload();
});
p('test_sync_manual_exchange_no_worker_threads',async t=>{
  const s=session(t),exchange=s.duplex('POST',fixture.https2+'/duplex');t.after(()=>exchange.close());
  const response=await exchange.response,reader=response.body[Symbol.asyncIterator]();assert.equal((await reader.next()).value.toString(),'ready\n');
  await exchange.send(Buffer.from('first'));assert.equal((await reader.next()).value.toString(),'first');await exchange.finishUpload();await exchange.finishUpload();
  assert.ok((await reader.next()).done);await response.waitUpload();await assert.rejects(exchange.send(Buffer.from('late')),/closed/);
  const beforeHeaders=s.duplex('POST',fixture.https2+'/upload');await beforeHeaders.send(SLAB);await beforeHeaders.finishUpload();assert.equal((await (await beforeHeaders.response).json()).bytes,SLAB.length);
  const bounded=s.duplex('POST',fixture.https2+'/duplex'),reading=(await bounded.response).read();const sending=bounded.send(Buffer.concat(Array(32).fill(SLAB)));
  await assert.rejects(bounded.send(SLAB),/another upload write is pending/);await sending;await bounded.finishUpload();assert.equal((await reading).length,SLAB.length*32+6);
},'Manual send/receive uses Promises on the main thread; Node has no blocking synchronous exchange API.');
p('test_64_mib_bidirectional_hash_and_bounds',async t=>{
  const s=session(t,{timeoutMs:90000});async function* body(){for(let i=0;i<1024;i++)yield SLAB;}
  const response=await s.stream('POST',fixture.https2+'/duplex',{duplex:true,body:body()});const hash=createHash('sha256');let bytes=0,peakUp=0,peakDown=0,marker=Buffer.alloc(0);
  for await(let part of response.body){if(marker.length<6){const count=Math.min(6-marker.length,part.length);marker=Buffer.concat([marker,part.subarray(0,count)]);part=part.subarray(count);}bytes+=part.length;hash.update(part);const stats=s.stats();peakUp=Math.max(peakUp,stats.buffered_upload_bytes);peakDown=Math.max(peakDown,stats.buffered_body_bytes);}
  await response.waitUpload();assert.equal(marker.toString(),'ready\n');assert.equal(bytes,64<<20);assert.equal(hash.digest('hex'),repeatedHash());assert.ok(peakUp<=65536);assert.ok(peakDown<=3*65536);
  evidence.measurements.duplex={bytes,sha256:repeatedHash(),peak_upload:peakUp,peak_download:peakDown};
});
p('test_slow_response_consumer_applies_backpressure',async t=>{
  const s=session(t,{timeoutMs:60000});let produced=0;async function* body(){for(let i=0;i<512;i++){produced++;yield SLAB;}}
  const response=await s.stream('POST',fixture.https2+'/duplex',{duplex:true,body:body()});await delay(250);const before=produced;await delay(250);
  assert.ok(produced<512);assert.ok(s.stats().buffered_upload_bytes<=65536);assert.ok(s.stats().buffered_body_bytes<=3*65536);
  let size=0;for await(const part of response.body)size+=part.length;assert.equal(size,(32<<20)+6);evidence.measurements.duplex_backpressure={produced_at_pause:before,total:produced};
});
p('test_producer_failure_after_headers_is_propagated',async t=>{
  const s=session(t),fail=Promise.withResolvers();async function* body(){await fail.promise;throw Error('late producer failure');yield SLAB;}
  const response=await s.stream('POST',fixture.https2+'/duplex',{duplex:true,body:body()}),reader=response.body[Symbol.asyncIterator]();assert.equal((await reader.next()).value.toString(),'ready\n');
  const rejected=assert.rejects(reader.next(),/late producer failure/);fail.resolve();await rejected;await assert.rejects(response.waitUpload(),/late producer failure/);await healthy(s);
});
p('test_cancel_read_and_h2_sibling_survives',async t=>{
  const s=session(t),warm=await s.get(fixture.https2+'/bytes?n=4'),c=new AbortController();let finalized=false;
  const body=signal=>(async function*(){try{await delay(60000,null,{signal});yield SLAB;}finally{finalized=true;}})();
  const response=await s.stream('POST',fixture.https2+'/duplex',{duplex:true,body,signal:c.signal}),reader=response.body[Symbol.asyncIterator]();await reader.next();
  const rejected=assert.rejects(reader.next(),sdk.AbortError);await delay(50);const sibling=await s.get(fixture.https2+'/bytes?n=4');assert.deepEqual(sibling.content,SLAB.subarray(0,4));assert.equal(sibling.headers.get('x-test-connection'),warm.headers.get('x-test-connection'));
  c.abort();await rejected;await response.closeAsync();assert.ok(finalized);await healthy(s);
},'AbortSignal cancels a pending read and cooperative upload factory; arbitrary JavaScript promises cannot be forcibly interrupted.');
p('test_close_session_cleans_paused_producer',async t=>{
  const s=session(t);let finalized=false;const body=signal=>(async function*(){try{await delay(60000,null,{signal});yield SLAB;}finally{await delay(20);finalized=true;}})();
  const response=await s.stream('POST',fixture.https2+'/duplex',{duplex:true,body});response.cancel();await s.closeAsync();assert.ok(finalized);assert.equal(s.producers.size,0);
  const other=session(t);const broken=signal=>(async function*(){try{await delay(60000,null,{signal});yield SLAB;}finally{await delay(20);throw Error('cleanup failed');}})();
  await other.stream('POST',fixture.https2+'/duplex',{duplex:true,body:broken});await assert.rejects(other.closeAsync(),/cleanup failed/);
},'close() releases native handles synchronously; closeAsync() additionally waits for cooperative producer finalization.');
p('test_write_timeout_after_response_headers',async t=>{const s=session(t);await assert.rejects(s.post(fixture.https2+'/duplex',{duplex:true,body:paused,timeoutMs:{total:5000,write:250}}),phase('write'));});
p('test_read_timeout_while_upload_keeps_progressing',async t=>{
  const s=session(t);const body=signal=>(async function*(){while(true){yield SLAB;await delay(40,null,{signal});}})();
  await assert.rejects(s.post(fixture.https2+'/duplex-silent',{duplex:true,body,timeoutMs:{total:5000,write:1000,read:200}}),phase('read'));
});
p('test_total_timeout_and_cancel_paused_consumer',async t=>{
  const s=session(t);await assert.rejects(s.post(fixture.https2+'/duplex',{duplex:true,body:paused,timeoutMs:200}),phase('total'));
  const response=await s.stream('POST',fixture.https2+'/duplex',{duplex:true,body:paused});response.cancel();await assert.rejects(response.read(),sdk.AbortError);await response.closeAsync();
});
p('test_gzip_duplex_and_buffered_convenience_request',async t=>{
  const s=session(t);async function* body(){yield SLAB;await delay(50);yield SLAB;}
  const response=await s.post(fixture.https2+'/duplex-gzip',{duplex:true,body:body()});assert.ok(response.decoded);assert.deepEqual(response.content,Buffer.concat([Buffer.from('ready\n'),SLAB,SLAB]));
  assert.equal((await s.post(fixture.https2+'/duplex',{duplex:true})).text(),'ready\n');
});
p('test_reject_redirect_and_response_eof_stops_upload',async t=>{
  for(const [path,status] of [['/upload-reject',413],['/redirect-code?code=302',302],['/upload-early-ok',200]]){
    const s=session(t);let finalized=false,started=false,returned=false,aborted=false;
    const body=signal=>{signal.addEventListener('abort',()=>aborted=true,{once:true});const iterator=(async function*(){started=true;try{yield SLAB;await delay(60000,null,{signal});}finally{finalized=true;}})();return {next:()=>iterator.next(),return(){returned=true;return iterator.return();},[Symbol.asyncIterator](){return this;}};};
    assert.equal((await s.post(fixture.https2+path,{duplex:true,allowRedirects:true,body})).status,status);assert.ok(returned&&aborted);assert.ok(!started||finalized);await healthy(s);
  }
},'Response EOF cancels the factory and calls iterator.return(); a generator closed before its first next() has not entered its finally.');
p('test_h1_and_invalid_options_fail',async t=>{
  const s=session(t);for(const protocol of ['http','https1'])await assert.rejects(s.post(fixture[protocol]+'/upload',{duplex:true,body:[Buffer.from('data')]}),/HTTP\/2/);
  await assert.rejects(s.post(fixture.https2,{duplex:'yes'}),TypeError);assert.throws(()=>s.duplex('POST',fixture.https2,{body:Buffer.from('wrong')}),TypeError);assert.throws(()=>s.duplex('POST',fixture.https2,{duplex:true}),TypeError);
  const exchange=s.duplex('POST',fixture.https2+'/duplex');await assert.rejects(exchange.send('wrong'),TypeError);exchange.close();await assert.rejects(exchange.response,/closed/);
},'Node uses one Promise API and accepts automatic duplex producers; the manual exchange separately rejects body/duplex options.');
