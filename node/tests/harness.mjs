import assert from 'node:assert/strict';
import { test, before, after, afterEach } from 'node:test';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { resolve, dirname } from 'node:path';
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import { randomUUID, createHash } from 'node:crypto';
export { assert, delay, readFileSync, randomUUID };
export const root = resolve(import.meta.dirname, '../..');
const require = createRequire(import.meta.url);
const entry = process.env.FASTLS_NODE_PACKAGE ? require.resolve('@fasttls/fastls', { paths: [process.env.FASTLS_NODE_PACKAGE] }) : require.resolve('..');
export const sdk = require(entry);
export const { Native } = require(resolve(dirname(entry), 'native.js'));
export const SLAB = Buffer.alloc(65536);
for (let i=0;i<SLAB.length;i+=4) SLAB.set([0,1,128,255],i);
export const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export const repeatedHash = (count=1024) => { const hash=createHash('sha256'); for(let i=0;i<count;i++)hash.update(SLAB); return hash.digest('hex'); };
export let fixture, monitor;
let child, childExit;
const sessions = new Set();
export const evidence = { node:process.version, platform:process.platform, arch:process.arch,
  label:process.env.FASTLS_NODE_PACKAGE?'package':'source', pythonBaseline:JSON.parse(readFileSync(resolve(root,'node/tests/python-baseline.json'),'utf8')), cases:[], measurements:{} };
export function session(t, options={}) {
  const s=new sdk.Session({caFile:fixture.ca_file,timeoutMs:15000,...options});
  sessions.add(s); t.after(()=>s.close()); return s;
}
export async function eventually(probe, message='condition was not reached', timeout=5000) {
  const end=performance.now()+timeout;
  while(performance.now()<end){if(await probe())return;await delay(20);} assert.fail(message);
}
export async function control(path,id) { const r=await fetch(`${fixture.http}${path}?id=${id}`,{signal:AbortSignal.timeout(3000)});return path==='/state'?r.json():r.text(); }
export const healthy = async(s,protocol='https2') => assert.deepEqual((await s.get(fixture[protocol]+'/bytes?n=4')).content,SLAB.subarray(0,4));
export function parity(file,name,fn,adaptation) {
  test(`${file}::${name}`,{timeout: Math.max(90000, Number(process.env.FASTLS_NODE_SOAK_SECONDS??30)*1000+30000)},async t=>{
    const record={python:`${file}::${name}`,passed:false,adaptation};evidence.cases.push(record);
    const started=performance.now();
    try{await fn(t);assert.equal(monitor.stats().open_requests,0,'request handle retained');record.passed=true;}
    catch(error){record.error=String(error);throw error;}finally{record.ms=performance.now()-started;}
  });
}
let processHandles;
export function resources() {
  let descriptors;
  if(process.platform==='win32'){
    if(!processHandles){const koffi=require('koffi'),lib=koffi.load('kernel32.dll');processHandles={lib,get:lib.func('bool GetProcessHandleCount(void *process, _Out_ uint32_t *count)'),process:lib.func('void *GetCurrentProcess(void)')()};}
    const out=[0];assert.ok(processHandles.get(processHandles.process,out));descriptors=out[0];
  }else descriptors=readdirSync(process.platform==='linux'?'/proc/self/fd':'/dev/fd').length;
  const memory=process.memoryUsage();
  return {...monitor.stats(),rss_bytes:memory.rss,js_heap_used_bytes:memory.heapUsed,js_external_bytes:memory.external,js_array_buffer_bytes:memory.arrayBuffers,descriptors_or_handles:descriptors};
}
before(async()=>{
  child=spawn(resolve(root,'build',process.platform==='win32'?'ffi-fixture.exe':'ffi-fixture'),[],{stdio:['pipe','pipe','pipe'],windowsHide:true});
  child.stderr.resume();childExit=once(child,'exit');
  const lines=createInterface({input:child.stdout});
  try{const [line]=await Promise.race([once(lines,'line'),childExit.then(()=>{throw Error('fixture exited');}),delay(10000,null,{ref:false}).then(()=>{throw Error('fixture startup timeout');})]);fixture=JSON.parse(line);}finally{lines.close();}
  monitor=new sdk.Session();
});
afterEach(async()=>{
  for(const s of sessions)s.close();sessions.clear();
  if(monitor)await eventually(()=>{const s=monitor.stats();return s.open_requests===0&&s.workers===0&&s.open_sessions===1;},'native resource leak');
});
after(async()=>{
  monitor?.close();
  if(child){child.stdin.end();const timer=setTimeout(()=>child.kill(),10000);try{await childExit;}finally{clearTimeout(timer);}}
  if(evidence.cases.length)writeFileSync(resolve(root,'build',`node-parity-${evidence.label}-${Date.now()}.json`),JSON.stringify(evidence,null,2));
});
