import test from 'node:test';
import { writeFileSync } from 'node:fs';
import { session, fixture, sdk, assert, delay, eventually, resources, root, randomUUID } from './harness.mjs';
import { ProxyLab } from './proxy-lab.mjs';
import { getHeapSpaceStatistics } from 'node:v8';
import { createConnection } from 'node:net';
import { once } from 'node:events';

const seconds = Number(process.env.FASTLS_NODE_PROXY_SOAK_SECONDS ?? 60);
assert.ok(Number.isInteger(seconds) && seconds >= 1 && seconds <= 300);

test('proxy tunnels recover after shared H2 cancellation, failed auth and cancelled handshakes', { timeout: seconds*1000+45000 }, async t => {
  const normal=[], stalled=[];
  for (const scheme of ['http','https','socks5','socks5h']) {
    for (const [target, mode] of [[normal,'normal'],[stalled,'stall']]) {
      const proxy=await ProxyLab.open(scheme,{auth:true,mode,certFile:fixture.proxy_cert_file,keyFile:fixture.proxy_key_file,connectDelayMs:scheme==='https'&&mode==='stall'?50:0}); target.push(proxy); t.after(()=>proxy.close());
    }
  }
  const s=session(t,{proxy:normal[0].url,proxyCaFile:fixture.proxy_ca_file,timeoutMs:10000});
  const proof={node:process.version,platform:process.platform,arch:process.arch,runtime_args:process.execArgv,concurrency:32,passed:false,
    requests:0,completed:0,cancelled_body:0,rejected:0,cancelled_handshake:0,warmup_requests:100,tls_fixture_connect_delay_ms:50,samples:[]};
  let started;
  const batch=async()=>{
    const initialStalls=stalled.map(p=>p.stallStarted);
    const outcomes=await Promise.allSettled(Array.from({length:32},async(_,index)=>{
      const schemeIndex=Math.floor(index/4)%normal.length,proxy=normal[schemeIndex];
      if (index%4===0) {
        assert.equal((await s.get(fixture.https2+'/bytes?n=1024',{proxy:proxy.url})).content.length,1024);proof.completed++;
      } else if(index%4===1) {
        const response=await s.stream('GET',fixture.https2+'/sse-hold?id='+randomUUID(),{proxy:proxy.url});
        try {await response.body[Symbol.asyncIterator]().next();response.cancel();}finally{await response.closeAsync();}proof.cancelled_body++;
      } else if(index%4===2) {
        await assert.rejects(s.get(fixture.https2,{proxy:proxy.url.replace('s3cr%40t%3Asecret','bad-password')}),error=>error instanceof sdk.FastlsError&&!/bad-password|proxy-user|CredentialMustNotBeEchoed/.test(String(error)));proof.rejected++;
      } else {
        const controller=new AbortController();
        const pending=assert.rejects(s.get(fixture.https2,{proxy:stalled[schemeIndex].url,signal:controller.signal}),sdk.AbortError);
        void pending.catch(()=>{});
        // H2 can share a pending dial between the two requests per scheme.
        // Require an actual stalled handshake for each scheme before aborting
        // its waiters; a timer may fire before TLS starts on a busy runner.
        try{
          await eventually(()=>stalled.every((p,i)=>p.stallStarted>initialStalls[i]));
          controller.abort();await pending;
        }finally{controller.abort();await pending;}proof.cancelled_handshake++;
      }
    }));
    const failure=outcomes.find(result=>result.status==='rejected');if(failure)throw failure.reason;
    await eventually(()=>s.stats().workers===0&&stalled.every(p=>p.sockets.size===0)&&normal.every(p=>p.sockets.size<=2));
    assert.equal(s.stats().open_requests,0);proof.requests+=32;
  };
  try {
    // A raw FIN before ClientHello must not leave the TLS acceptance fixture
    // waiting for a handshake timeout. This makes the cancellation race fixed.
    const https=normal.find(p=>p.scheme==='https');
    const probe=createConnection({host:'127.0.0.1',port:Number(new URL(https.url).port)});
    t.after(()=>probe.destroy());await once(probe,'connect');probe.end();
    await eventually(()=>probe.destroyed&&https.sockets.size===0);
    proof.fixture_eof_probe=true;
    // Seed each H2 connection before concurrent traffic; racing the initial
    // connection dial may legitimately create multiple idle pooled tunnels.
    for(const proxy of normal)await s.get(fixture.https2+'/bytes?n=4',{proxy:proxy.url});
    for(let i=0;i<3;i++)await batch();
    global.gc?.();proof.baseline=resources();proof.baseline_v8=getHeapSpaceStatistics();started=performance.now();
    while(performance.now()-started<seconds*1000){await batch();proof.samples.push(resources());await delay(50);}
    for(const key of ['completed','cancelled_body','rejected','cancelled_handshake'])assert.equal(proof[key],proof.requests/4);
    await s.closeAsync();for(const p of [...normal,...stalled])await p.waitIdle();
    proof.proxy_connections=normal.map((p,i)=>({scheme:p.scheme,accepted:p.accepted,stalled_accepted:stalled[i].accepted,stall_started:stalled[i].stallStarted,cancelled_handshakes:stalled[i].cancelledHandshakes}));
    assert.ok(proof.proxy_connections.every(p=>p.accepted>0&&p.cancelled_handshakes>=proof.requests/32&&p.cancelled_handshakes<=proof.requests/16&&p.stall_started===p.cancelled_handshakes));
    proof.final=resources();proof.final_v8=getHeapSpaceStatistics();
    assert.ok(proof.final.goroutines<=proof.baseline.goroutines+16);
    assert.ok(proof.final.descriptors_or_handles<=proof.baseline.descriptors_or_handles+64);
    assert.ok(proof.final.rss_bytes<=proof.baseline.rss_bytes+96*1024**2);
    assert.ok(Math.max(...proof.samples.map(s=>s.rss_bytes))<=proof.baseline.rss_bytes+128*1024**2);
    assert.ok(Math.max(...proof.samples.map(s=>s.heap_inuse_bytes))<=proof.baseline.heap_inuse_bytes+96*1024**2);
    proof.passed=true;
  }catch(error){
    proof.error=String(error);proof.final=resources();proof.final_v8=getHeapSpaceStatistics();
    proof.proxy_state=[...normal,...stalled].map(p=>({scheme:p.scheme,mode:p.mode,accepted:p.accepted,sockets:[...p.sockets].map(s=>({type:s.constructor.name,destroyed:s.destroyed,readableEnded:s.readableEnded,writableEnded:s.writableEnded,remotePort:s.remotePort}))}));
    throw error;
  }
  finally{
    proof.seconds=started===undefined?0:(performance.now()-started)/1000;
    // Diagnostic only: thresholds above use the pre-GC final sample.
    global.gc?.();proof.after_gc=resources();proof.after_gc_v8=getHeapSpaceStatistics();
    const label=process.env.FASTLS_NODE_PACKAGE?'package':'source';writeFileSync(`${root}/build/node-proxy-recovery-${label}-${Date.now()}.json`,JSON.stringify(proof,null,2));
  }
});
