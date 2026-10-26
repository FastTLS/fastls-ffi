import { parity, session, fixture, assert, sdk, SLAB, repeatedHash, resources, evidence, eventually, Native, delay } from './harness.mjs';
const p=(name,fn,adaptation)=>parity('test_request_options.py',name,fn,adaptation);
const unpack=r=>Buffer.from(r.json().body??'', 'base64');
class FileSource {
  name='large.bin'; reads=0; maximum=0; closed=false;
  constructor(size,{fail=false,async=false,sample=false}={}){this.size=this.left=size;this.fail=fail;this.async=async;this.peak=sample?resources().rss_bytes:undefined;}
  read(size){assert.ok(size>0&&size<=65536);this.maximum=Math.max(this.maximum,size);this.reads++;if(this.peak!==undefined&&this.reads%32===0)this.peak=Math.max(this.peak,resources().rss_bytes);if(this.fail&&this.reads===3){const error=Error('fixture file read failed');if(this.async)return Promise.reject(error);throw error;}const take=Math.min(this.left,size);this.left-=take;return this.async?Promise.resolve(SLAB.subarray(0,take)):SLAB.subarray(0,take);}
  close(){this.closed=true;}
}
const adaptation='Node keeps raw body and uses explicit form instead of Python data inference; multipart uses caller-owned read(maxBytes) readers, including Promise readers. The single Promise API replaces both Python entry points.';

p('test_query_preserves_existing_encoding_repeated_keys_and_unicode',async t=>{
  const s=session(t);
  for(const key of ['http','https1','https2']){
    const r=await s.get(fixture[key]+'/inspect?old=a%20b&tag=old#fragment',{params:[['tag',['中文','a b']],['none',null],['empty',''],['yes',true]]}),info=r.json();
    assert.deepEqual(info.query,{old:['a b'],tag:['old','中文','a b'],empty:[''],yes:['True']});assert.ok(info.raw_query.startsWith('old=a%20b&tag=old&'));assert.ok(!info.raw_query.includes('fragment'));
    assert.deepEqual((await s.get(fixture[key]+'/inspect',{params:'x=a%2Bb&x=2'})).json().query,{x:['a+b','2']});
    assert.deepEqual((await s.get(fixture[key]+'/inspect',{params:new Map([['bytes',Buffer.from([0,255])]])})).json().raw_query,'bytes=%00%FF');
  }
});
p('test_json_null_empty_values_and_explicit_content_type',async t=>{
  const s=session(t,{headers:{'CONTENT-TYPE':'application/custom+json'}});
  for(const [json,expected] of [[null,'null'],[{},'{}'],[[],'[]'],['','""'],[{'中文':true},'{"中文":true}']]){const r=await s.post(fixture.https2+'/inspect',{json});assert.equal(unpack(r).toString(),expected);assert.deepEqual(r.json().headers['Content-Type'],['application/custom+json']);}
  const r=await s.post(fixture.https1+'/inspect',{json:false,headers:{'content-type':'application/json'}});assert.equal(unpack(r).toString(),'false');assert.deepEqual(r.json().headers['Content-Type'],['application/json']);
  const plain=session(t);assert.deepEqual((await plain.post(fixture.http+'/inspect',{json:0})).json().headers['Content-Type'],['application/json']);assert.equal(unpack(await plain.post(fixture.http+'/inspect')).length,0);
});
p('test_form_encoding_and_raw_body_compatibility',async t=>{
  const s=session(t);for(const key of ['https1','https2']){
    const r=await s.post(fixture[key]+'/inspect',{form:[['tag',['中文','a b']],['nil',null],['empty','']]});assert.equal(unpack(r).toString(),'tag=%E4%B8%AD%E6%96%87&tag=a+b&empty=');assert.deepEqual(r.json().headers['Content-Type'],['application/x-www-form-urlencoded']);
    for(const body of [Buffer.from('raw'),'raw',[Buffer.from('ra'),Buffer.from('w')],(function*(){yield Buffer.from('raw');})()])assert.equal(unpack(await s.post(fixture[key]+'/inspect',{body})).toString(),'raw');
    assert.equal(unpack(await s.post(fixture[key]+'/inspect',{form:[]})).length,0);
  }
},adaptation);
p('test_invalid_options_fail_without_native_handle_or_file_read',async t=>{
  const source=new FileSource(1024),s=session(t),cycle={};cycle.self=cycle;
  const cases=[{json:{},body:Buffer.alloc(0)},{json:null,files:{}},{json:NaN},{json:undefined},{json:Infinity},{json:{x:undefined}},{json:[,]},{json:cycle},{json:new Date()},{params:123},{params:'x=#fragment'},{params:'中文'},{form:{x:{}}},{params:[[1,'x']]},{files:{file:['bad\r\nname',source]}},{files:{file:['a',[Buffer.from('x')]]}},{files:{file:source},headers:{'CONTENT-TYPE':'multipart/form-data'}},{files:{file:source},body:'raw'},{files:{file:source},duplex:true}];
  for(const header of ['Content-Length','Transfer-Encoding'])for(const option of [{json:{}},{form:{}},{files:{file:source}}])cases.push({...option,headers:{[header]:'0'}});
  for(const options of cases){await assert.rejects(s.post(fixture.https2+'/inspect',options),TypeError);assert.equal(s.stats().open_requests,0);}
  assert.equal(source.reads,0);assert.equal(source.closed,false);
  for(const options of [{json:null},{form:{}},{files:{}},{body:Buffer.alloc(0)}])assert.throws(()=>s.duplex('POST',fixture.https2,options),TypeError);
},adaptation);
p('test_multipart_fields_repeated_files_unicode_and_empty_body',async t=>{
  const s=session(t);for(const key of ['http','https1','https2']){
    let offset=5;const owned={closed:false,read(n){const block=Buffer.from('skip-content').subarray(offset,offset+n);offset+=block.length;return block;},close(){this.closed=true;}};
    const parts=(await s.post(fixture[key]+'/multipart',{form:[['tag',['中文','']]],files:[['file',['附件.bin',owned]],['file',['empty.txt',Buffer.alloc(0),'text/plain']]]})).json().parts;
    assert.deepEqual(parts.map(p=>p.name),['tag','tag','file','file']);assert.deepEqual(parts.map(p=>p.bytes),[6,0,7,0]);assert.equal(parts[2].filename,'附件.bin');assert.equal(parts[2].sha256,(await import('node:crypto')).createHash('sha256').update('content').digest('hex'));assert.equal(parts[3].type,'text/plain');assert.equal(owned.closed,false);
    assert.deepEqual((await s.post(fixture[key]+'/multipart',{files:{}})).json().parts,[]);
    assert.equal((await s.post(fixture[key]+'/multipart',{files:{f:[null,Buffer.from('x')]}})).json().parts[0].filename,'');
  }
},adaptation);
p('test_multipart_immediate_response_after_terminal_boundary',async t=>{
  const s=session(t),n=Native.load(),original=n.write,sleeper=new Int32Array(new SharedArrayBuffer(4));let terminalWrites=0;
  n.write=function(handle,data,end){const code=original.call(this,handle,data,end);if(code===0&&data.toString().startsWith('--fastls-')&&data.toString().endsWith('--\r\n')){assert.equal(end,true);terminalWrites++;const deadline=performance.now()+5000;while(n.call('info',handle).state==='pending'){assert.ok(performance.now()<deadline);Atomics.wait(sleeper,0,0,1);}}return code;};
  try{for(let i=0;i<20;i++)assert.equal((await s.post(fixture.https2+'/multipart',{files:{file:['a.bin',Buffer.from('content')]}})).json().parts[0].bytes,7);assert.equal(terminalWrites,20);}finally{n.write=original;}
});
async function large(t,async){
  const s=session(t,{timeoutMs:45000}),before=resources().rss_bytes,transfers=[];
  for(const key of ['https1','https2']){const source=new FileSource(64*1024*1024,{async,sample:true}),result=(await s.post(fixture[key]+'/multipart-slow',{files:{file:source}})).json(),part=result.parts[0];assert.equal(part.bytes,source.size);assert.equal(part.sha256,repeatedHash());assert.equal(source.closed,false);assert.equal(source.maximum,65536);assert.ok(source.reads>=1024);assert.ok(source.peak-before<48*1024*1024,`multipart RSS growth ${source.peak-before}`);transfers.push({protocol:result.protocol,bytes:part.bytes,sha256:part.sha256,max_read:source.maximum,reads:source.reads,peak_rss:source.peak});}
  const after=resources().rss_bytes;assert.ok(after-before<48*1024*1024,`final multipart RSS growth ${after-before}`);evidence.measurements[async?'async_multipart_large':'multipart_large']={transfers,rss_before:before,rss_after:after};
}
p('test_multipart_64mib_slow_consumer_bounded_reads_and_memory',t=>large(t,false),adaptation);
p('test_multipart_read_failure_keeps_file_open_and_cleans_request',async t=>{
  const s=session(t);for(const key of ['https1','https2']){const source=new FileSource(1024*1024,{fail:true});await assert.rejects(s.post(fixture[key]+'/multipart',{files:{file:source}}),/fixture file read failed/);assert.equal(source.closed,false);assert.equal(s.stats().open_requests,0);assert.equal((await s.get(fixture[key]+'/status?code=204')).status,204);}
},adaptation);
p('test_redirects_buffered_body_replay_and_one_shot_multipart',async t=>{
  const s=session(t);for(const key of ['https1','https2']){
    const base=fixture[key]+'/redirect-code?code=';
    for(const [options,expected] of [[{json:{x:1}},'{"x":1}'],[{form:{x:1}},'x=1']]){const r=await s.post(base+'307',options);assert.equal(r.status,200);assert.equal(unpack(r).toString(),expected);}
    for(const code of [307,308]){const source=new FileSource(4*1024*1024);assert.equal((await s.post(base+code,{files:{file:source}})).status,code);assert.equal(source.closed,false);}
    const source=new FileSource(4*1024*1024);assert.equal((await s.post(base+'302',{files:{file:source}})).json().method,'GET');assert.equal(source.closed,false);
  }
});
p('test_response_charset_override_and_status_error',async t=>{
  const s=session(t);for(const key of ['https1','https2']){
    const r=await s.get(fixture[key]+'/charset');assert.equal(r.encoding,'iso-8859-1');assert.equal(r.text(),'café');r.encoding='utf-8';assert.equal(r.text(),'caf�');r.encoding='not-a-codec';assert.throws(()=>r.text(),RangeError);
    for(const code of [204,304,400,503]){const response=await s.get(fixture[key]+'/status?code='+code,{allowRedirects:false});assert.equal(response.ok,code<400);if(code>=400)assert.throws(()=>response.raiseForStatus(),e=>e instanceof sdk.FastlsHTTPError&&e.response===response&&e.status===code&&e.message===`HTTP ${code}`);else assert.equal(response.raiseForStatus(),undefined);}
  }
  assert.equal(new sdk.Response(200,new sdk.Headers(),{},Buffer.from([48,0])).json(),0);
  assert.equal(new sdk.Response(200,new sdk.Headers(),{},Buffer.from('\ufefftext')).text(),'\ufefftext');
  for(const bytes of [Buffer.from('\ufeff{"x":1}'),Buffer.from('\ufeff{"x":1}','utf16le'),Buffer.from([0,0,0,123,0,0,0,125])]){const r=new sdk.Response(200,new sdk.Headers(),{},bytes);r.encoding='not-a-codec';assert.deepEqual(r.json(),bytes.length===8?{}:{x:1});}
});
p('test_async_query_json_form_and_charset',async t=>{
  const s=session(t);for(const key of ['https1','https2']){const r=await s.post(fixture[key]+'/inspect',{params:{q:['a','中文']},json:null});assert.deepEqual(r.json().query,{q:['a','中文']});assert.equal(unpack(r).toString(),'null');assert.equal(unpack(await s.post(fixture[key]+'/inspect',{form:{q:'中文'}})).toString(),'q=%E4%B8%AD%E6%96%87');assert.equal((await s.get(fixture[key]+'/charset')).text(),'café');const status=await s.get(fixture[key]+'/status?code=503');assert.throws(()=>status.raiseForStatus(),sdk.FastlsHTTPError);}
},adaptation);
p('test_async_multipart_file_failure_and_success',async t=>{
  const s=session(t);for(const key of ['https1','https2'])for(const fail of [true,false]){const source=new FileSource(2*1024*1024,{fail,async:true});if(fail)await assert.rejects(s.post(fixture[key]+'/multipart',{files:{file:source}}),/fixture file read failed/);else assert.equal((await s.post(fixture[key]+'/multipart',{files:{file:source},form:{tag:'value'}})).json().parts[1].bytes,source.size);assert.equal(source.closed,false);}
},adaptation);
p('test_async_64mib_multipart_bounded_stream',t=>large(t,true),adaptation);
p('test_large_json_and_form_switch_to_stream_without_corruption',async t=>{
  const s=session(t),text='中文'.repeat(100000);for(const key of ['https1','https2']){assert.deepEqual(JSON.parse(unpack(await s.post(fixture[key]+'/inspect',{json:{value:text}})).toString()),{value:text});assert.equal(new URLSearchParams(unpack(await s.post(fixture[key]+'/inspect',{form:{value:text}})).toString()).get('value'),text);}
});
p('test_async_conflicts_fail_before_upload_and_status_check_before_read',async t=>{
  const s=session(t),source=new FileSource(1024);for(const options of [{json:null,body:Buffer.alloc(0)},{files:{f:source},duplex:true},{json:Infinity}]){await assert.rejects(s.post(fixture.https2+'/inspect',options),TypeError);assert.equal(s.stats().open_requests,0);}
  const r=await s.stream('GET',fixture.https2+'/status?code=503');assert.throws(()=>r.raiseForStatus(),sdk.FastlsHTTPError);assert.equal(r.consumed,false);await r.closeAsync();assert.equal(source.reads,0);assert.equal(source.closed,false);
});
p('test_async_cancel_slow_multipart_keeps_file_and_session_usable',async t=>{
  const s=session(t,{timeoutMs:60000});for(const key of ['https1','https2']){const source=new FileSource(1024*1024*1024,{async:true}),controller=new AbortController();const rejected=assert.rejects(s.post(fixture[key]+'/multipart-slow',{files:{file:source},signal:controller.signal}),sdk.AbortError);await eventually(()=>source.reads>4);controller.abort();await rejected;assert.equal(source.closed,false);assert.ok(source.left>0);assert.equal(s.stats().open_requests,0);assert.equal((await s.get(fixture[key]+'/status?code=204')).status,204);}
  let waiting=false,aborted=false,closed=false;
  const controller=new AbortController(),reader={read(_maximum,signal){waiting=true;return new Promise((_resolve,reject)=>{signal.addEventListener('abort',()=>{aborted=true;reject(signal.reason);},{once:true});});},close(){closed=true;}};
  const rejected=assert.rejects(s.post(fixture.https2+'/multipart',{files:{file:reader},signal:controller.signal}),sdk.AbortError);
  await eventually(()=>waiting);controller.abort();await rejected;await eventually(()=>aborted);await s.closeAsync();assert.equal(closed,false);
},adaptation);
