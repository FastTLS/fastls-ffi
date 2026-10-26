import { parity, session, fixture, assert, delay, sdk, healthy, eventually, control, randomUUID, SLAB } from './harness.mjs';
import { wire } from './wire.mjs';
const p=(name,fn)=>parity('test_network.py',name,fn);
p('test_silent_http_and_tls_handshake_timeout',async t=>{
  for(const scheme of ['http','https']){const peer=await wire(t);const s=session(t);
    await assert.rejects(s.get(`${scheme}://${peer.address}`,{timeoutMs:350}),sdk.FastlsTimeoutError);
    assert.ok(peer.accepted);await eventually(()=>peer.disconnected);await healthy(s);}
});
p('test_cancel_stalled_tls_keeps_event_loop_running',async t=>{
  const peer=await wire(t),s=session(t),c=new AbortController();let ticks=0;
  const timer=setInterval(()=>ticks++,10);t.after(()=>clearInterval(timer));
  const rejected=assert.rejects(s.get('https://'+peer.address,{signal:c.signal}),sdk.AbortError);
  await eventually(()=>peer.accepted);await delay(100);c.abort();await rejected;
  await eventually(()=>peer.disconnected);assert.ok(ticks>=5);await healthy(s);
});
p('test_invalid_http_wire_is_never_successful_eof',async t=>{
  for(const response of ['HTTP/1.1 nope\r\n\r\n','HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nZZ\r\nbad\r\n',
    'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\nabc','HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nabc',
    'HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nab','HTTP/1.1 200 OK\r\ninvalid header\r\n\r\n']){
    const peer=await wire(t,Buffer.from(response));const s=session(t);
    await assert.rejects(s.get('http://'+peer.address,{timeoutMs:2000}),sdk.FastlsError);await healthy(s);
  }
});
p('test_fragmented_valid_chunked_binary_with_trailers',async t=>{
  const reply=Buffer.concat([Buffer.from('HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nTrailer: X-Checksum\r\n\r\n4;fixture=yes\r\n'),SLAB.subarray(0,4),Buffer.from('\r\n3\r\nend\r\n0\r\nX-Checksum: fixture\r\n\r\n')]);
  const peer=await wire(t,[...reply].map(byte=>Buffer.from([byte])));
  assert.deepEqual((await session(t).get('http://'+peer.address)).content,Buffer.concat([SLAB.subarray(0,4),Buffer.from('end')]));
});
p('test_aborted_h1_connection_and_h2_stream_recover',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t);for(let i=0;i<8;i++){
    await assert.rejects(s.get(fixture[protocol]+'/abort'),sdk.FastlsError);await healthy(s,protocol);}}
});
p('test_body_deadline_while_waiting_and_consumer_paused',async t=>{
  for(const protocol of ['https1','https2'])for(const paused of [false,true]){
    const s=session(t),r=await s.stream('GET',fixture[protocol]+'/hold?id='+randomUUID(),{timeoutMs:200});
    const reader=r.body[Symbol.asyncIterator]();assert.equal((await reader.next()).value.toString(),'first');
    if(paused)await delay(300);await assert.rejects(reader.next(),error=>error.phase==='total');await healthy(s,protocol);
  }
});
p('test_async_cancel_while_waiting_for_next_body_chunk',async t=>{
  for(const protocol of ['https1','https2']){const id=randomUUID(),s=session(t),c=new AbortController();
    const r=await s.stream('GET',fixture[protocol]+'/hold?id='+id,{signal:c.signal});const reader=r.body[Symbol.asyncIterator]();
    await reader.next();let settled=false;const next=reader.next().finally(()=>settled=true);const rejected=assert.rejects(next,sdk.AbortError);
    await delay(80);assert.equal(settled,false);c.abort();await rejected;await eventually(async()=>(await control('/state',id)).cancelled);await healthy(s,protocol);
  }
});
p('test_redirect_method_and_binary_body_contract',async t=>{
  const payload=Buffer.from([0,128,255,0,65]);
  for(const protocol of ['https1','https2']){const s=session(t);for(const code of [301,302,303,307,308]){
    const result=(await s.post(fixture[protocol]+'/redirect-code?code='+code,{body:payload})).json();const preserve=code>=307;
    assert.equal(result.method,preserve?'POST':'GET');assert.deepEqual(Buffer.from(result.body??'','base64'),preserve?payload:Buffer.alloc(0));
  }}
});
p('test_redirect_disabled_and_redirect_loop_are_bounded',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t);const r=await s.get(fixture[protocol]+'/redirect-code?code=307',{allowRedirects:false});
    assert.equal(r.status,307);assert.equal(r.headers.get('location'),'/inspect');await assert.rejects(s.get(fixture[protocol]+'/redirect-loop',{timeoutMs:3000}));await healthy(s,protocol);}
});
p('test_redirect_to_different_host_strips_sensitive_headers',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),base=fixture[protocol];const headers={Authorization:'Bearer fixture',Cookie:'fixture=private','X-Test-Public':'keep'};
    const original=(await s.get(base+'/inspect',{headers})).json();assert.deepEqual(original.headers.Authorization,[headers.Authorization]);
    const result=(await s.get(base+'/redirect-host',{headers})).json();assert.equal(result.headers.Authorization,undefined);assert.equal(result.headers.Cookie,undefined);assert.deepEqual(result.headers['X-Test-Public'],['keep']);
    assert.equal(result.host,new URL(base.replace('127.0.0.1','localhost')).host);
  }
});
p('test_head_empty_responses_and_error_status',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t);assert.equal((await s.request('HEAD',fixture[protocol]+'/bytes?n=4096')).content.length,0);
    for(const code of [204,304,503]){const r=await s.get(fixture[protocol]+'/status?code='+code);assert.equal(r.status,code);assert.equal(r.content.length,0);}}
});

p('test_request_and_response_trailers_and_metadata',async t=>{for(const [key,protocol] of [['https1','HTTP/1.1'],['https2','HTTP/2.0']]){const s=session(t),url=fixture[key]+'/trailers',r=await s.post(url,{body:Buffer.from('payload'),trailers:{'X-Request-Trailer':['request-value']}});assert.equal(r.text(),'request-value');assert.equal(r.httpVersion,protocol);assert.equal(r.url,url);assert.equal(r.trailers.get('X-Response-Trailer'),'response-value');}});
