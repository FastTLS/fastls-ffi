import { parity, session as baseSession, fixture, assert, delay, sdk, Native, eventually, randomUUID, evidence, monitor, SLAB, repeatedHash } from './harness.mjs';
import { createHash } from 'node:crypto';
import { ProxyLab } from './proxy-lab.mjs';
import { mkdtempSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname, basename } from 'node:path';
const p = (name, fn, adaptation) => parity('test_proxy.py', name, fn, adaptation);
const schemes = ['http', 'https', 'socks5', 'socks5h'];
const session=(t,options)=>baseSession(t,{proxyCaFile:fixture.proxy_ca_file,...options});
async function lab(t, scheme = 'http', options) { const p = await ProxyLab.open(scheme, {certFile:fixture.proxy_cert_file,keyFile:fixture.proxy_key_file,...options}); t.after(() => p.close()); return p; }
function redacted(error) {
  assert.ok(error instanceof sdk.FastlsError);
  assert.doesNotMatch(String(error), /secret|proxy-user|bad-password|CredentialMustNotBeEchoed/);
  return true;
}

p('test_protocols_authentication_and_target_header_isolation', async t => {
  for (const scheme of schemes) for (const auth of [false,true]) {
    const proxy = await lab(t,scheme,{auth}), s = session(t,{proxy:proxy.url});
    try { for (const key of ['http','https1','https2']) {
      const r = await s.get(fixture[key]+'/inspect'); assert.equal(r.status,200);
      const headers = r.json().headers;
      assert.ok(!('Proxy-Authorization' in headers)); assert.ok(!('Proxy-Connection' in headers));
      assert.equal(r.headers.get('x-test-protocol'),key==='https2'?'HTTP/2.0':'HTTP/1.1');
    } } finally { await s.closeAsync(); }
    await proxy.waitIdle(); assert.equal(proxy.targets.length,3);
    if(scheme==='https')assert.deepEqual(proxy.tlsProtocols,['http/1.1','http/1.1','http/1.1']);
  }
});

p('test_request_override_direct_and_pool_credential_isolation', async t => {
  const first = await lab(t,'http',{auth:true}), second = await lab(t,'socks5h',{auth:true}), s = session(t,{proxy:first.url});
  const url = fixture.https2+'/bytes?n=4', connection = async options => (await s.get(url,options)).headers.get('x-test-connection');
  try {
    const a = await connection(); assert.equal(a,await connection({proxy:undefined}));
    const b = await connection({proxy:second.url}), direct = await connection({proxy:null});
    assert.equal(new Set([a,b,direct]).size,3); assert.equal(direct,await connection({proxy:''})); assert.equal(a,await connection());
    await assert.rejects(s.get(url,{proxy:first.url.replace('s3cr%40t%3Asecret','wrong')}),sdk.FastlsError);
    assert.equal(a,await connection());
    // SSE and manual duplex must retain request-level proxy overrides too.
    s.closeIdleConnections(); await first.waitIdle(); await second.waitIdle();
    const beforeFirst=first.accepted,beforeSecond=second.accepted;
    const events = await s.sse('GET',fixture.https2+'/sse-vectors',{proxy:second.url}); let count=0;
    for await (const event of events) count++; assert.equal(count,5);
    assert.equal(first.accepted,beforeFirst); assert.equal(second.accepted,beforeSecond+1);
    s.closeIdleConnections(); await second.waitIdle();
    const exchange = s.duplex('POST',fixture.https2+'/duplex',{proxy:second.url});
    try { const response = await exchange.response; await exchange.send(Buffer.from('override')); await exchange.finishUpload(); assert.equal((await response.read()).toString(),'ready\noverride'); }
    finally { await exchange.closeAsync(); }
    assert.equal(first.accepted,beforeFirst); assert.equal(second.accepted,beforeSecond+2);
  } finally { await s.closeAsync(); }
  await first.waitIdle(); await second.waitIdle();
});

p('test_socks_dns_modes', async t => {
  for (const [scheme,expected] of [['socks5',1],['socks5h',3]]) {
    const proxy=await lab(t,scheme),s=session(t,{proxy:proxy.url});
    try {
      await s.get(fixture.http.replace('127.0.0.1','localhost')+'/bytes?n=4'); assert.deepEqual(proxy.methods,[expected]);
      const url=fixture.http.replace('127.0.0.1','proxy-only.invalid')+'/bytes?n=4';
      if (scheme==='socks5h') assert.equal((await s.get(url)).status,200); else await assert.rejects(s.get(url),sdk.FastlsError);
    } finally { await s.closeAsync(); }
    await proxy.waitIdle();
  }
});

p('test_invalid_urls_and_credentials_are_not_exposed', async t => {
  const urls=['socks4://proxy-user:secret@127.0.0.1:1','http://proxy-user:secret@host:bad','http://proxy-user:secret@host:0','http://proxy-user:secret@host/path','http://proxy-user:secret@host?query=secret','http://proxy-user:sec%ZZret@host','http://:secret@host'];
  const s=session(t);
  for (const proxy of urls) await assert.rejects(s.get(fixture.http,{proxy}),redacted);
  for (const name of ['Proxy-Authorization','proxy-connection']) await assert.rejects(s.get(fixture.http,{headers:{[name]:'secret'}}),redacted);
  for (const proxy of [false,42,{},[]]) {
    assert.throws(()=>new sdk.Session({proxy}),TypeError);
    await assert.rejects(s.get(fixture.http,{proxy}),TypeError);
  }
  assert.equal(s.stats().open_requests,0);
  const native=Native.load(), original=native.call;
  native.call=function(name,...args) { const value=original.call(this,name,...args); return name==='features'?value.filter(x=>x!=='proxy_tunnels'):value; };
  try {
    await assert.rejects(s.get(fixture.http,{proxy:'http://127.0.0.1:1'}),/newer Fastls native library/);
    const defaultProxy=session(t,{proxy:'http://127.0.0.1:1'});
    await assert.rejects(defaultProxy.get(fixture.http),/newer Fastls native library/);
    assert.equal((await defaultProxy.get(fixture.http,{proxy:null})).status,200);
    assert.equal((await s.get(fixture.http)).status,200);
    assert.equal(s.stats().open_requests,0);
  } finally { native.call=original; }
  native.call=function(name,...args){const value=original.call(this,name,...args);return name==='features'?value.filter(x=>x!=='https_proxy_tls'):value;};
  try{await assert.rejects(s.get(fixture.http,{proxy:'https://127.0.0.1:1'}),/HTTPS proxy support requires a newer/);assert.equal(s.stats().open_requests,0);}
  finally{native.call=original;}
});

p('test_rejection_and_wrong_auth_do_not_retry_or_leak_secrets', async t => {
  for (const scheme of schemes) for (const mode of ['deny','normal']) {
    const proxy=await lab(t,scheme,{auth:true,mode}),s=session(t,{proxy:proxy.url.replace('s3cr%40t%3Asecret','bad-password')});
    try { await assert.rejects(s.get(fixture.https2),redacted); } finally { await s.closeAsync(); }
    await proxy.waitIdle(); assert.equal(proxy.accepted,1);
  }
});

p('test_connect_timeout_total_timeout_and_explicit_cancellation', async t => {
  for (const [scheme,mode] of [['http','stall'],['https','stall-tls'],['https','stall'],['socks5','stall'],['socks5h','stall-auth'],['socks5h','stall-connect']]) {
    const proxy=await lab(t,scheme,{auth:true,mode}),s=session(t,{proxy:proxy.url});
    try {
      for (const [timeoutMs,phase] of [[{total:3000,connect:150},'connect'],[150,'total']]) {
        const start=performance.now();
        await assert.rejects(s.get(fixture.https2,{timeoutMs}),error=>error instanceof sdk.FastlsTimeoutError && error.phase===phase);
        assert.ok(performance.now()-start<2000); await proxy.waitIdle();
      }
      const controller=new AbortController(),pending=assert.rejects(s.get(fixture.https2,{signal:controller.signal}),sdk.AbortError);
      await eventually(()=>proxy.accepted>=3); controller.abort(); await pending; await proxy.waitIdle();
    } finally { await s.closeAsync(); }
    assert.equal(proxy.accepted,3); assert.ok(proxy.cancelledHandshakes>=2);
  }
});

p('test_wrong_target_ca_and_environment_proxy_is_ignored', async t => {
  const proxy=await lab(t),s=session(t,{proxy:proxy.url,caFile:fixture.wrong_ca_file});
  try { await assert.rejects(s.get(fixture.https2),sdk.FastlsError); } finally { await s.closeAsync(); }
  const names=['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy'];
  const previous=names.map(name=>process.env[name]);
  try {
    for (const name of names) process.env[name]=name.toLowerCase()==='no_proxy'?'':proxy.url;
    const before=proxy.accepted,direct=session(t); try { assert.equal((await direct.get(fixture.https2)).status,200); } finally { await direct.closeAsync(); }
    assert.equal(proxy.accepted,before);
  } finally { names.forEach((name,i)=>{if(previous[i]===undefined)delete process.env[name];else process.env[name]=previous[i];}); }
  await proxy.waitIdle();
});

p('test_plain_http_handshake_deadline_releases_socket', async t => {
  for (const scheme of schemes) {
    const proxy=await lab(t,scheme,{mode:'stall'}),s=session(t,{proxy:proxy.url});
    try { await assert.rejects(s.get(fixture.http,{timeoutMs:{total:3000,connect:150}}),sdk.FastlsTimeoutError); await proxy.waitIdle(); }
    finally { await s.closeAsync(); }
  }
});

p('test_large_upload_download_sse_and_duplex', async t => {
  const expected=repeatedHash(); evidence.measurements.proxy_large=[];
  for (const scheme of schemes) {
    const proxy=await lab(t,scheme,{auth:true}),s=session(t,{proxy:proxy.url,timeoutMs:60000});
    let peak=0;
    try {
      for (const slow of [false,true]) {
        const digest=createHash('sha256'); let size=0;
        const response=await s.stream('GET',fixture.https2+'/bytes?n=67108864');
        try { for await (const part of response.body) {
          digest.update(part); size+=part.length;
          if (slow && size===part.length) { await delay(50); peak=s.stats().buffered_body_bytes; assert.ok(peak<=3*65536); }
        } } finally { await response.closeAsync(); }
        assert.equal(size,64*1024*1024); assert.equal(digest.digest('hex'),expected);
      }
      const sync = { *[Symbol.iterator]() { for(let i=0;i<1024;i++)yield SLAB; } };
      const asyncSource = async function* () { for(let i=0;i<1024;i++)yield SLAB; };
      for (const body of [sync,asyncSource]) {
        const upload=(await s.post(fixture.https2+'/upload',{body})).json();
        assert.equal(upload.bytes,64*1024*1024); assert.equal(upload.sha256,expected);
      }
      const events=await s.sse('GET',fixture.https2+'/sse-vectors'); let count=0; for await(const event of events)count++;assert.equal(count,5);
      const response=await s.stream('POST',fixture.https2+'/duplex',{body:async function*(){yield Buffer.from('proxy-duplex');},duplex:true});
      try { assert.equal((await response.read()).toString(),'ready\nproxy-duplex'); } finally { await response.closeAsync(); }
      evidence.measurements.proxy_large.push({scheme,bytes:64*1024*1024,sha256:expected,peak_download:peak,sse_events:count,duplex:true,sync_upload:true,async_upload:true});
    } finally { await s.closeAsync(); }
    await proxy.waitIdle();
  }
},'Node uses one Promise API; synchronous and asynchronous producers plus immediate/slow streaming consumers cover the corresponding Python sync/async combinations.');

p('test_cancel_one_h2_stream_keeps_shared_tunnel', async t => {
  for (const scheme of schemes) {
    const proxy=await lab(t,scheme),s=session(t,{proxy:proxy.url}),url=fixture.https2;
    try {
      const initial=(await s.get(url+'/bytes?n=4')).headers.get('x-test-connection');
      const response=await s.stream('GET',url+'/sse-hold?id='+randomUUID()),reader=response.body[Symbol.asyncIterator]();
      await reader.next(); response.cancel(); await assert.rejects(reader.next(),sdk.FastlsError); await response.closeAsync();
      const healthy=await s.get(url+'/bytes?n=4'); assert.equal(healthy.headers.get('x-test-connection'),initial); assert.equal(proxy.accepted,1);
    } finally { await s.closeAsync(); }
    await proxy.waitIdle();
  }
  await eventually(()=>monitor.stats().open_requests===0);
});

p('test_https_proxy_trust_is_independent_and_pool_isolated',async t=>{
 const proxy=await lab(t,'https',{auth:true}),url=fixture.https2+'/bytes?n=4',s=session(t,{proxy:proxy.url});
 try{
  const original=(await s.get(url)).headers.get('x-test-connection');
  for(const proxyCaFile of [fixture.ca_file,fixture.wrong_ca_file,null]){
   await assert.rejects(s.get(url,{proxyCaFile}),sdk.FastlsError);
   assert.equal((await s.get(url)).headers.get('x-test-connection'),original);
  }
  assert.notEqual((await s.get(url,{proxy:null})).headers.get('x-test-connection'),original);
  s.caPem=readFileSync(fixture.proxy_ca_file,'utf8');
  await assert.rejects(s.get(url),sdk.FastlsError);
 }finally{await s.closeAsync();}
 await proxy.waitIdle();
 const override=session(t,{proxyCaFile:null});
 try{assert.equal((await override.get(url,{proxy:proxy.url,proxyCaFile:fixture.proxy_ca_file})).status,200);}
 finally{await override.closeAsync();}
 await proxy.waitIdle();
});

p('test_https_proxy_hostname_is_verified',async t=>{
 const proxy=await lab(t,'https',{certFile:fixture.proxy_wrong_host_cert_file,keyFile:fixture.proxy_wrong_host_key_file});
 const s=session(t,{proxy:proxy.url,proxyCaFile:fixture.proxy_wrong_host_ca_file});
 try{await assert.rejects(s.get(fixture.https2),/IP SAN|valid for|hostname/);}finally{await s.closeAsync();}
 await proxy.waitIdle();assert.deepEqual(proxy.targets,[]);
});

p('test_https_proxy_does_not_fallback_to_cleartext',async t=>{
 const proxy=await lab(t),s=session(t,{proxy:proxy.url.replace('http://','https://')});
 try{await assert.rejects(s.get(fixture.https2),sdk.FastlsError);}finally{await s.closeAsync();}
 await proxy.waitIdle();assert.equal(proxy.accepted,1);assert.deepEqual(proxy.targets,[]);
});

p('test_https_proxy_invalid_ca_rejected_without_request_leak',async t=>{
 const directory=mkdtempSync(resolve(tmpdir(),'fastls-proxy-ca-')),file=resolve(directory,'ca.pem');
 t.after(()=>{assert.equal(dirname(directory),resolve(tmpdir()));assert.ok(basename(directory).startsWith('fastls-proxy-ca-'));rmSync(directory,{recursive:true,force:true});});
 const proxy=await lab(t,'https');writeFileSync(file,'');
 assert.throws(()=>session(t,{proxy:proxy.url,proxyCaFile:file}),TypeError);
 const s=session(t,{proxy:proxy.url});
 try{
  await assert.rejects(s.get(fixture.https2,{proxyCaFile:file}),TypeError);
  writeFileSync(file,'not a certificate');await assert.rejects(s.get(fixture.https2,{proxyCaFile:file}),sdk.FastlsError);
  assert.equal(s.stats().open_requests,0);assert.equal(proxy.accepted,0);
  // HTTP/SOCKS/direct overrides ignore the default proxy CA and unused file overrides.
  assert.equal((await s.get(fixture.http,{proxy:null,proxyCaFile:'/unused-file'})).status,200);
 }finally{await s.closeAsync();}
});
