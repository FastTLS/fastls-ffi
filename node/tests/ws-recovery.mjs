import test from 'node:test';
import {writeFileSync} from 'node:fs';
import {session,fixture,assert,delay,eventually,resources,root} from './harness.mjs';
const seconds=Number(process.env.FASTLS_NODE_WS_SOAK_SECONDS??60);
assert.ok(Number.isInteger(seconds)&&seconds>=1&&seconds<=300);
test('mixed WebSocket messages, read cancellation, protocol rejection and close recover resources',{timeout:seconds*1000+45000},async t=>{
  const s=session(t),base=fixture.https2.replace(/^http/,'ws')+'/ws',proof={passed:false,node:process.version,platform:process.platform,arch:process.arch,runtime_args:process.execArgv,concurrency:32,requests:0,completed:0,cancelled:0,rejected:0,closed:0,samples:[]};let started;
  await s.get(fixture.https2+'/cookie-set',{params:{name:'login',value:'yes'}});
  proof.session_cookies=true;
  const batch=async()=>{
    const results=await Promise.allSettled(Array.from({length:32},async(_,i)=>{
      if(i%4===2){await assert.rejects(s.websocket(base,{params:{mode:'hidden-compression',set_cookie:'poison=no; Path=/'}}),/./);assert.equal(s.cookies.get('poison',fixture.https2),undefined);proof.rejected++;return;}
      const controller=new AbortController(),ws=await s.websocket(base,{params:{mode:'cookie-auth',set_cookie:'roundtrip=yes; Path=/'},signal:controller.signal,maxMessageBytes:65536});
      assert.equal(s.cookies.get('roundtrip',fixture.https2),'yes');
      try{
        if(i%4===1){const rejected=assert.rejects(ws.recv(),/aborted/);controller.abort();await rejected;proof.cancelled++;}
        else if(i%4===3){await ws.closeAsync(4001,'done');assert.equal(ws.closeCode,4001);proof.closed++;}
        else{const bytes=Buffer.alloc(65536,i);await ws.send(bytes);assert.deepEqual(await ws.recv(),bytes);await ws.closeAsync();proof.completed++;}
      }finally{ws.abort();}
    }));for(const r of results)if(r.status==='rejected')throw r.reason;
    await eventually(()=>s.stats().workers===0);assert.equal(s.stats().open_requests,0);assert.equal(s.stats().buffered_ws_write_bytes,0);proof.requests+=32;
  };
  try{
    for(let i=0;i<3;i++)await batch();global.gc?.();proof.baseline=resources();started=performance.now();
    while(performance.now()-started<seconds*1000){await batch();proof.samples.push(resources());await delay(50);}
    for(const key of ['completed','cancelled','rejected','closed'])assert.equal(proof[key],proof.requests/4);
    await s.closeAsync();proof.final=resources();
    assert.ok(proof.final.goroutines<=proof.baseline.goroutines+16);
    assert.ok(proof.final.descriptors_or_handles<=proof.baseline.descriptors_or_handles+64);
    assert.ok(proof.final.rss_bytes<=proof.baseline.rss_bytes+96*1024**2);
    assert.ok(Math.max(...proof.samples.map(x=>x.rss_bytes))<=proof.baseline.rss_bytes+128*1024**2);
    assert.ok(Math.max(...proof.samples.map(x=>x.heap_inuse_bytes))<=proof.baseline.heap_inuse_bytes+96*1024**2);proof.passed=true;
  }catch(error){proof.error=String(error);throw error;}
  finally{proof.seconds=started===undefined?0:(performance.now()-started)/1000;writeFileSync(`${root}/build/node-ws-recovery-source-${Date.now()}.json`,JSON.stringify(proof,null,2));}
});
