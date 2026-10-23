import { parity, session, fixture, assert, delay, sdk, sdkEntry, Native, Notifier, eventually, control, randomUUID, evidence, resources } from './harness.mjs';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url),crypto=require('node:crypto');
const p=(name,fn,adaptation)=>parity('test_notifications.py',name,fn,adaptation);
async function held(t,s=session(t,{notifications:'required'})) {const response=await s.stream('GET',fixture.https2+'/sse-hold?id='+randomUUID()),reader=response.body[Symbol.asyncIterator]();await reader.next();return {s,response,reader};}
async function idleBody(t){const {s,response,reader}=await held(t);let reads=0;const native=s.native,original=native.read;native.read=function(...args){reads++;return original.apply(this,args);};try{const pending=assert.rejects(reader.next(),sdk.AbortError);await delay(50);const before=reads;await delay(250);assert.ok(reads-before<=1);response.cancel();await pending;return {seconds:0.25,read_calls:reads-before,mode:s.notificationMode};}finally{native.read=original;await s.closeAsync();}}
async function brokenSetup(t,action){const original=crypto.timingSafeEqual;crypto.timingSafeEqual=()=>false;try{return await action();}finally{crypto.timingSafeEqual=original;}}

p('test_sync_wait_releases_gil_and_does_not_poll_body',async t=>{evidence.measurements.notification_idle_adapted=await idleBody(t);},'Node has one nonblocking Promise API; idle-read counts and timer progress replace Python GIL and blocking-wait checks.');
p('test_async_idle_body_has_no_timer_polling_or_python_threads',async t=>{evidence.measurements.notification_idle=await idleBody(t);assert.equal(evidence.measurements.notification_idle.mode,'async-events');},'Node attaches its main-thread event loop to the wakeup socket; no JavaScript worker is created. Native Go and Node runtime threads are not Python thread counts.');
p('test_async_idle_headers_do_not_poll_info',async t=>{
 const s=session(t,{notifications:'required'}),id=randomUUID(),c=new AbortController();await s.ready();let infos=0;const native=s.native,original=native.call;native.call=function(name,...args){if(name==='info')infos++;return original.call(this,name,...args);};
 try{const pending=assert.rejects(s.get(fixture.https2+'/wait?id='+id,{signal:c.signal}),sdk.AbortError);await eventually(async()=>(await control('/state',id)).started);await delay(50);const before=infos;await delay(250);assert.ok(infos-before<=1);c.abort();await pending;evidence.measurements.notification_idle_headers={seconds:0.25,info_calls:infos-before};}finally{native.call=original;}
});
p('test_generation_and_cancelled_shared_waiter',async t=>{
 const {s,response}=await held(t),n=s.notifier,h=response.transfer.handle;await delay(30);
 for(let i=0;i<1000;i++){const stamp=n.generation(h);n.pulse(h);await n.wait(h,stamp,new AbortController().signal);}
 const stamp=n.generation(h),c=new AbortController(),first=assert.rejects(n.wait(h,stamp,c.signal),/cancel one/),second=n.wait(h,stamp,new AbortController().signal);c.abort(Error('cancel one'));await first;n.pulse(h);await second;response.close();assert.equal(n.states.size,0);
});
p('test_128_mixed_requests_one_notification_connection',async t=>{
 const s=session(t,{notifications:'required'});await s.get(fixture.https2+'/bytes?n=4');assert.equal(s.stats().notifiers,1);const controllers=Array.from({length:32},()=>new AbortController());
 const held=controllers.map(c=>assert.rejects(s.get(fixture.https2+'/sse-hold?id='+randomUUID(),{signal:c.signal}),sdk.AbortError));const healthy=Array.from({length:96},()=>s.get(fixture.https2+'/bytes?n=128'));await delay(80);for(const c of controllers)c.abort();await Promise.all(held);assert.ok((await Promise.all(healthy)).every(r=>r.content.length===128));assert.equal(s.notifier.states.size,0);assert.equal(s.stats().notifiers,1);evidence.measurements.notification_concurrency={requests:128,cancelled:32,healthy:96,notifiers:1};
});
p('test_independent_sessions_own_independent_notifiers',async t=>{
 const a=session(t,{notifications:'required'}),b=session(t,{notifications:'required'});await Promise.all([a.get(fixture.https2),b.get(fixture.https2)]);assert.equal(a.stats().notifiers,2);assert.notEqual(a.notifier,b.notifier);await a.closeAsync();assert.equal(b.stats().notifiers,1);assert.equal((await b.get(fixture.https2)).status,200);
});
p('test_cancel_during_setup_closes_candidates_and_can_retry',async t=>{
 const s=session(t,{notifications:'required'}),c=new AbortController(),original=Notifier.prototype.accept;let seen;
 Notifier.prototype.accept=function(socket){seen=this;original.call(this,socket);c.abort();};
 try{await assert.rejects(s.get(fixture.https2,{signal:c.signal}),sdk.AbortError);}finally{Notifier.prototype.accept=original;}
 await eventually(()=>seen.sockets.size===0&&s.stats().notifiers===0);assert.equal(s.stats().open_requests,0);assert.equal((await s.get(fixture.https2)).status,200);
 const shared=session(t,{notifications:'required'}),cancel=new AbortController();let first=true;
 Notifier.prototype.accept=function(socket){original.call(this,socket);if(first){first=false;cancel.abort();}};
 try{const cancelled=assert.rejects(shared.get(fixture.https2,{signal:cancel.signal}),sdk.AbortError);const other=shared.get(fixture.https2);await cancelled;assert.equal((await other).status,200);assert.equal(shared.stats().notifiers,2);}finally{Notifier.prototype.accept=original;}
 const manual=session(t,{notifications:'required'}),exchange=manual.duplex('POST',fixture.https2+'/duplex');exchange.close();await assert.rejects(exchange.response,/closed/);assert.equal(manual.active.size,0);
});
p('test_response_close_from_other_thread_wakes_reader',async t=>{const {s,response,reader}=await held(t);const pending=assert.rejects(reader.next(),sdk.FastlsError);await delay(30);response.close();await pending;assert.equal((await s.get(fixture.https2)).status,200);},'Node sessions are main-thread-only; close from another event-loop callback replaces Python cross-thread close.');
p('test_session_close_from_other_thread_wakes_all',async t=>{const {s,reader}=await held(t);const pending=assert.rejects(reader.next(),sdk.FastlsError);await delay(30);await s.closeAsync();await pending;assert.equal(s.notifier.states.size,0);},'Node sessions are main-thread-only; asynchronous session close replaces Python cross-thread close.');
p('test_broken_notification_channel_fails_waiters',async t=>{
 const {s,reader}=await held(t),pending=assert.rejects(reader.next(),/notification/);await delay(30);s.notifier.writer.destroy();await pending;await assert.rejects(s.get(fixture.https2),/notification/);assert.ok(s.notifier.failure);
});
p('test_explicit_poll_mode_sync_async_and_sse',async t=>{
 const s=session(t,{notifications:'poll'});assert.equal(s.notificationMode,'poll');assert.equal((await s.get(fixture.https2)).status,200);const events=await s.sse('GET',fixture.https2+'/sse-vectors');let count=0;for await(const event of events)count++;assert.equal(count,5);assert.equal(s.notifier,undefined);
},'The same Promise API covers the synchronous and asynchronous Python poll-mode cases.');
p('test_old_capability_auto_fallback_and_required_gate',async t=>{
 const native=Native.load(),original=native.call;native.call=function(name,...args){const value=original.call(this,name,...args);return name==='features'?value.filter(x=>x!=='session_notifications'):value;};
 try{const s=session(t);assert.equal(s.notificationMode,'poll');assert.equal((await s.get(fixture.https2)).status,200);assert.throws(()=>session(t,{notifications:'required'}),/newer/);}finally{native.call=original;}
});
p('test_invalid_options_and_native_wait_arguments',async t=>{
 for(const notifications of [true,false,'invalid',null])assert.throws(()=>session(t,{notifications}),TypeError);const s=session(t,{notifications:'required'}),native=s.native;assert.equal(native.functions.wait(0n,1,0),-2);const {response}=await held(t,s);const h=response.transfer.handle;for(const [mask,timeout] of [[0,0],[8,0],[2,60001]])assert.equal(native.functions.wait(h,mask,timeout),-2);assert.equal(native.functions.wait(h,1,0),0);response.close();
});
p('test_watch_registration_validation_and_late_registration',async t=>{
 const s=session(t,{notifications:'poll'}),n=s.native;for(const [port,token,length] of [[0,Buffer.alloc(32),32],[1,null,32],[1,Buffer.alloc(1),1]])assert.throws(()=>n.call('session_watch',s.handle,port,token,length),sdk.FastlsError);assert.throws(()=>n.call('session_events',s.handle),sdk.FastlsError);const {response}=await held(t,s);assert.throws(()=>n.call('session_watch',s.handle,1,Buffer.alloc(32),32),/before/);response.close();
});
p('test_repeated_session_resource_recovery',async t=>{
 for(let i=0;i<25;i++){const s=session(t,{notifications:'required'});await s.get(fixture.https2+'/bytes?n=4');const n=s.notifier;await s.closeAsync();assert.equal(n.sockets.size,0);assert.equal(n.states.size,0);}await eventually(()=>Native.load().call('stats').notifiers===0);evidence.measurements.notification_recovery={sessions:25,final:resources()};
});
p('test_concurrent_first_requests_initialize_once',async t=>{const s=session(t,{notifications:'required'});const responses=await Promise.all(Array.from({length:32},()=>s.get(fixture.https2+'/bytes?n=4')));assert.equal(responses.length,32);assert.equal(s.stats().notifiers,1);assert.equal(s.notifier.sockets.size,1);});
p('test_overflow_hint_rescans_all_owned_handles',async t=>{
 const s=session(t,{notifications:'required'}),ids=[randomUUID(),randomUUID()],responses=await Promise.all(ids.map(id=>s.stream('GET',fixture.https2+'/sse-hold?id='+id))),readers=responses.map(r=>r.body[Symbol.asyncIterator]());await Promise.all(readers.map(r=>r.next()));const original=s.native.call;
 s.native.call=function(name,...args){const result=original.call(this,name,...args);return name==='session_events'?{handles:[],overflow:true}:result;};try{const pending=Promise.all(readers.map(r=>r.next()));await delay(30);await Promise.all(ids.map(id=>control('/release',id)));const values=await pending;assert.ok(values.every(r=>r.value.includes('last')));}finally{s.native.call=original;for(const r of responses)r.close();}
});
p('test_wrong_handshake_fails_cleanly_then_retries',async t=>{const s=session(t,{notifications:'required'});await brokenSetup(t,()=>assert.rejects(s.get(fixture.https2),/notification/));assert.equal(s.stats().notifiers,0);assert.equal(s.stats().open_requests,0);assert.equal((await s.get(fixture.https2)).status,200);});
p('test_context_initializes_before_body_and_cleans_failed_entry',async t=>{
 const s=await sdk.Session.open({caFile:fixture.ca_file,notifications:'required'});try{assert.ok(s.notifier.isReady);assert.equal(s.stats().notifiers,1);assert.equal(s.stats().open_requests,0);}finally{await s.closeAsync();}
 await brokenSetup(t,()=>assert.rejects(sdk.Session.open({caFile:fixture.ca_file,notifications:'required'}),/notification/));await eventually(()=>Native.load().call('stats').notifiers===0);assert.equal(Native.load().call('stats').open_sessions,1);
},'Session.open() initializes before returning and closes on failed initialization, replacing Python async-context entry.');
p('test_different_event_loop_rejected_and_sync_close_allowed',async t=>{
 const {Worker}=await import('node:worker_threads');const s=session(t,{notifications:'required'});await s.get(fixture.https2);const worker=new Worker(`const {parentPort}=require('node:worker_threads');try{new(require(${JSON.stringify(sdkEntry)}).Session)();parentPort.postMessage('unexpected')}catch(e){parentPort.postMessage(e.message)}`,{eval:true});const message=await new Promise((resolve,reject)=>{worker.once('message',resolve);worker.once('error',reject);});await worker.terminate();assert.match(message,/main thread/);s.close();assert.equal(Native.load().call('stats').notifiers,0);
},'Node has one main-thread event loop per supported process. A worker is rejected at library load instead of testing Python cross-loop reuse.');
