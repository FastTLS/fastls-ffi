import { parity, session, fixture, assert, delay, sdk, eventually, control, randomUUID, evidence, SSEDecoder } from './harness.mjs';
import { createHash } from 'node:crypto';
import { setImmediate as turn } from 'node:timers/promises';
const p=(name,fn,adaptation)=>parity('test_sse.py',name,fn,adaptation);
const E=(data,event='message',id='',retry)=>new sdk.ServerSentEvent(data,event,id,retry);
const collect=async events=>{const list=[];for await(const event of events)list.push(event);return list;};
function decode(parts,maximum=1<<20){const decoder=new SSEDecoder(maximum),events=[];for(const part of parts)events.push(...decoder.feed(part));decoder.finish();return {events,decoder};}
const expected=()=>[E('第一条\nsecond','update','41',30),E('','message','41'),E(' final'),E('{"ok":true}'),E('[DONE]')];
const waitDone=async id=>eventually(async()=>(await control('/state',id)).done);

p('test_parser_every_split_and_single_bytes',async()=>{
  const wire=Buffer.from('\ufeff: ping\r\nid: 7\r\n\r\nevent: change\rdata: 中文😀\rdata: second\r\rdata\n\nid:\ndata:  tail\n\ndata: unfinished');
  const expected=[E('中文😀\nsecond','change','7'),E('','message','7'),E(' tail')];
  for(let i=0;i<=wire.length;i++){const result=decode([wire.subarray(0,i),Buffer.alloc(0),wire.subarray(i)]);assert.deepEqual(result.events,expected);assert.equal(result.decoder.lastEventId,'');}
  assert.deepEqual(decode([...wire].map(byte=>Buffer.from([byte]))).events,expected);
});
p('test_parser_endings_spaces_and_unknown_fields',async()=>{
  const cases=[['data:x\n\n',[E('x')]],['data:x\r\r',[E('x')]],['data:x\r\n\r\n',[E('x')]],['data\ndata\n\n',[E('\n')]],['data:  x\ndata:\tx\n\n',[E(' x\n\tx')]],['Data:ignored\nunknown:yes\n:ping\n\n',[]],['event: ignored\n\ndata:one\n\n',[E('one')]],['data:a\n\ndata:b\n',[E('a')]],['data:a\n\ndata:b',[E('a')]]];
  for(const [text,expected] of cases)for(const width of [1,2,7,65536]){const wire=Buffer.from(text),parts=[];for(let i=0;i<wire.length;i+=width)parts.push(wire.subarray(i,i+width));assert.deepEqual(decode(parts).events,expected);}
});
p('test_parser_utf8_replacement_and_only_initial_bom',async()=>{
  const wire=Buffer.concat([Buffer.from('\ufeffdata: '),Buffer.from([255,237,160,128]),Buffer.from('\n\ndata: \ufeffend\n\n\ufeffdata: ignored\n\n')]);
  assert.deepEqual(decode([...wire].map(byte=>Buffer.from([byte]))).events,[E('\ufffd'.repeat(4)),E('\ufeffend')]);
});
p('test_parser_id_control_blocks_null_and_incomplete_eof',async()=>{
  const decoder=new SSEDecoder(1024);assert.deepEqual([...decoder.feed(Buffer.from('id: one\n\n'))],[]);assert.equal(decoder.lastEventId,'one');
  assert.deepEqual([...decoder.feed(Buffer.from('id: bad\0id\ndata:first\n\nid\n\ndata:second\n\nid: discarded\n'))],[E('first','message','one'),E('second')]);decoder.finish();assert.equal(decoder.lastEventId,'');
});
p('test_parser_retry_digits_control_and_clamp',async()=>{
  const wire=Buffer.from('retry: 0015\n\nretry: +3\nretry: -2\nretry:\nretry: ٢\ndata:one\n\nretry: 0\ndata:two\n\nretry: '+'9'.repeat(5000)+'\ndata:three\n\n');
  const {events,decoder}=decode([wire]);assert.deepEqual(events,[E('one'),E('two','message','',0),E('three','message','',2147483647)]);assert.equal(decoder.retry,2147483647);
  assert.equal(decode([Buffer.from('retry: '+'0'.repeat(5000)+'9\n\ndata:last\n\n')]).decoder.retry,9);
});
p('test_parser_line_and_multiline_size_boundaries',async()=>{
  assert.deepEqual(decode([Buffer.from(':'+'x'.repeat(15)+'\n\n')],16).events,[]);assert.throws(()=>decode([Buffer.from(':'+'x'.repeat(16))],16),/line/);
  const exact='data:'+'a'.repeat(20)+'\ndata:'+'b'.repeat(11)+'\n\n';assert.equal(decode([Buffer.from(exact)],32).events[0].data,'a'.repeat(20)+'\n'+'b'.repeat(11));
  assert.throws(()=>decode([Buffer.from(exact.replace('b'.repeat(11),'b'.repeat(12)))],32),/event/);
  const decoder=new SSEDecoder(32);for(let i=0;i<1000;i++)assert.deepEqual([...decoder.feed(Buffer.from(': ping\n'))],[]);assert.equal(decoder.size,0);
  assert.throws(()=>decode([Buffer.from('data: '+String.fromCharCode(255).repeat(20))],32),/line/);
});
p('test_parser_does_not_parse_ahead_of_consumer',async()=>{
  const decoder=new SSEDecoder(16),pending=decoder.feed(Buffer.from('data:first\n\n'+'x'.repeat(17)));assert.deepEqual(pending.next().value,E('first'));assert.throws(()=>pending.next(),sdk.FastlsError);
});
p('test_event_json_and_immutability',async()=>{
  const event=E('{"value":1}');assert.deepEqual(event.json(),{value:1});assert.ok(Object.isFrozen(event));assert.throws(()=>event.data='changed',TypeError);assert.throws(()=>E('[DONE]').json(),SyntaxError);
});
p('test_sync_real_h1_h2_fragmented_vectors',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),events=await s.sse('GET',fixture[protocol]+'/sse-vectors?fragment=1',{lastEventId:'resume'});assert.deepEqual(await collect(events),expected());assert.equal(events.lastEventId,'');assert.equal(events.retry,30);assert.equal(events.response.headers.get('x-test-accept'),'text/event-stream');assert.equal(events.response.headers.get('x-test-last-event-id'),'resume');}
},'Node uses Promise requests and async iteration for both H1 and H2; it has no blocking synchronous SSE API.');
p('test_async_real_h2_vectors_and_loop_progress',async t=>{
  const s=session(t),events=await s.sse('GET',fixture.https2+'/sse-vectors?fragment=1');assert.deepEqual(await collect(events),expected());assert.equal(events.retry,30);
  const many=await s.sse('GET',fixture.https2+'/sse-many?n=2000&size=1');let ticks=0,done=false;
  const ticking=(async()=>{while(!done){ticks++;await turn();}})();
  try{const iterator=many[Symbol.asyncIterator]();await iterator.next();const first=ticks;let count=0;for(let part=await iterator.next();!part.done;part=await iterator.next())count++;assert.equal(count,1999);assert.ok(ticks-first>1000);}finally{done=true;await ticking;await many.closeAsync();}
});
p('test_gzip_decoding_and_raw_mode_rejection',async t=>{
  const s=session(t);for(const query of ['gzip=1','gzip=1&encoding=mixed']){const events=await s.sse('GET',fixture.https2+'/sse-vectors?'+query);assert.ok(events.response.decoded);assert.deepEqual(await collect(events),expected());await assert.rejects(s.sse('GET',fixture.https2+'/sse-vectors?'+query,{decodeContent:false}),/decoded/);}
  for(const encoding of ['empty','identity-list'])assert.deepEqual(await collect(await s.sse('GET',fixture.https2+'/sse-vectors?encoding='+encoding)),expected());
});
p('test_content_type_status_and_204',async t=>{
  const s=session(t);for(const path of ['/sse-bad?type=application/json','/sse-bad?type=text/event-streaming','/sse-bad?type=%C2%A0text/event-stream','/sse-bad?duplicate=1&type=text/event-stream','/status?code=503'])await assert.rejects(s.sse('GET',fixture.https2+path),sdk.FastlsError);
  const empty=await s.sse('GET',fixture.https2+'/status?code=204');assert.deepEqual(await collect(empty),[]);assert.equal(empty.response.status,204);
});
p('test_post_headers_and_manual_resume',async t=>{
  const s=session(t,{headers:{Accept:'application/json','X-Custom':'kept'}}),events=await s.sse('POST',fixture.https2+'/sse-post',{body:'payload',lastEventId:'41'});
  for await(const event of events){assert.deepEqual(event.json(),{body:'payload',accept:'text/event-stream',last_id:'41'});assert.equal(event.id,'41');break;}
  const custom=await s.sse('POST',fixture.https2+'/sse-post',{headers:{Accept:'custom'},body:'second'});for await(const event of custom){assert.equal(event.json().accept,'custom');break;}
  const reset=await s.sse('POST',fixture.https2+'/sse-post',{lastEventId:'',headers:{'Last-Event-ID':'ignored'}});for await(const event of reset){assert.equal(event.json().last_id,'');assert.equal(event.id,'');break;}
});
p('test_first_event_and_cancel_preserve_active_h2_sibling',async t=>{
  const s=session(t),id=randomUUID(),controller=new AbortController(),events=await s.sse('GET',fixture.https2+'/sse-hold?id='+id,{signal:controller.signal}),iterator=events[Symbol.asyncIterator]();assert.equal((await iterator.next()).value.data,'first');assert.equal((await control('/state',id)).done,false);
  const sibling=await s.stream('GET',fixture.https2+'/drip'),parts=sibling.body[Symbol.asyncIterator]();const first=(await parts.next()).value;assert.equal(first.toString(),'abcde');assert.equal(sibling.headers.get('x-test-connection'),events.response.headers.get('x-test-connection'));
  const pending=assert.rejects(iterator.next(),sdk.AbortError);await delay(30);controller.abort();await pending;const rest=[first];for(let part=await parts.next();!part.done;part=await parts.next())rest.push(part.value);assert.equal(Buffer.concat(rest).toString(),'abcde'.repeat(6));
  await waitDone(id);assert.ok((await control('/state',id)).cancelled);
},'AbortSignal cancels the pending event read; Node has no asyncio task cancellation. The active sibling stays on the same H2 connection.');
p('test_sync_early_break_closes_network',async t=>{
  const s=session(t),id=randomUUID(),events=await s.sse('GET',fixture.https2+'/sse-hold?id='+id);for await(const event of events){assert.equal(event.data,'first');break;}await waitDone(id);assert.ok((await control('/state',id)).cancelled);
},'Early break in for-await closes the event iterator and native response; Node has no synchronous context manager.');
p('test_async_session_close_unblocks_event_reader',async t=>{
  const s=session(t),events=await s.sse('GET',fixture.https2+'/sse-hold?id='+randomUUID()),iterator=events[Symbol.asyncIterator]();await iterator.next();const pending=assert.rejects(iterator.next(),sdk.FastlsError);await delay(30);await s.closeAsync();await pending;
});
p('test_read_timeout_and_heartbeat_total_deadline',async t=>{
  const s=session(t);for(const heartbeat of [false,true])await assert.rejects((async()=>{const events=await s.sse('GET',fixture.https2+'/sse-hold?id='+randomUUID()+(heartbeat?'&heartbeat=1':''),{timeoutMs:{total:heartbeat?400:3000,read:200}});await collect(events);})(),error=>error instanceof sdk.FastlsTimeoutError&&error.phase===(heartbeat?'total':'read'));
});
p('test_large_stream_backpressure_and_no_buffered_body_cap',async t=>{
  const s=session(t,{timeoutMs:60000}),id=randomUUID(),count=640,size=65536,events=await s.sse('GET',fixture.https2+`/sse-many?n=${count}&size=${size}&id=${id}`);await delay(200);assert.ok((await control('/state',id)).bytes<count*(size+8));assert.ok(s.stats().buffered_body_bytes<=3*65536);
  const hash=createHash('sha256');let seen=0,peak=0;for await(const event of events){assert.equal(event.data.length,size);hash.update(event.data);seen++;peak=Math.max(peak,s.stats().buffered_body_bytes);}const expected=createHash('sha256');for(let i=0;i<count;i++)expected.update('x'.repeat(size));
  assert.equal(seen,count);const digest=hash.digest('hex');assert.equal(digest,expected.digest('hex'));assert.ok(peak<=3*65536);evidence.measurements.large_sse={bytes:count*size,events:seen,sha256:digest,peak_download:peak};
});
p('test_invalid_options_fail_before_native_allocation',async t=>{
  const s=session(t);for(const value of [true,0,-1,1.5,64*1024*1024+1,null])await assert.rejects(s.sse('GET',fixture.https2,{maxEventBytes:value}),RangeError);
  for(const value of ['bad\0id','bad\r\nid',3,null])await assert.rejects(s.sse('GET',fixture.https2,{lastEventId:value}),TypeError);
  await assert.rejects(s.sse('GET',fixture.https2,{maxEventBytes:3,lastEventId:'中文'}),RangeError);assert.equal(s.stats().open_requests,0);
});
p('test_single_consumer_close_and_buffered_events',async t=>{
  const s=session(t);for(const cancel of [false,true]){const events=await s.sse('GET',fixture.https2+'/sse-vectors');assert.throws(()=>[...events],TypeError);const iterator=events[Symbol.asyncIterator]();await iterator.next();assert.throws(()=>events[Symbol.asyncIterator](),/one consumer/);if(cancel)events.cancel();else events.close();await assert.rejects(iterator.next(),sdk.FastlsError);}
  const unstarted=await s.sse('GET',fixture.https2+'/sse-hold?id='+randomUUID());await unstarted.closeAsync();assert.throws(()=>unstarted[Symbol.asyncIterator](),/closed/);
});
p('test_async_sse_over_duplex_upload',async t=>{
  const s=session(t),ready=Promise.withResolvers(),ack=Promise.withResolvers();async function* body(){await ready.promise;yield Buffer.from('alpha');await ack.promise;yield Buffer.from('beta');}
  const events=await s.sse('POST',fixture.https2+'/sse-duplex',{duplex:true,body:body()}),iterator=events[Symbol.asyncIterator]();assert.equal((await iterator.next()).value.data,'ready');ready.resolve();assert.equal((await iterator.next()).value.data,'alpha');ack.resolve();const rest=[];for(let part=await iterator.next();!part.done;part=await iterator.next())rest.push(part.value.data);assert.deepEqual(rest,['beta']);await events.response.waitUpload();
});
p('test_network_event_limit_closes_request',async t=>{
  const s=session(t);await assert.rejects((async()=>collect(await s.sse('GET',fixture.https2+'/sse-many?n=3&size=2048',{maxEventBytes:1024})))(),/maxEventBytes/);assert.equal(s.stats().open_requests,0);
});
p('test_compressed_error_cannot_become_clean_sse_eof',async t=>{
  const s=session(t);for(let i=0;i<2;i++)await assert.rejects((async()=>collect(await s.sse('GET',fixture.https2+'/sse-vectors?corrupt=1')))(),sdk.FastlsDecodeError);
});
