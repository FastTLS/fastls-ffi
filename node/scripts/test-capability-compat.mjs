import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {createRequire} from 'node:module';
const root=resolve(import.meta.dirname,'../..'),require=createRequire(import.meta.url);
const {Session,ProfileSpec}=require(resolve(root,'node/dist/index.js'));
const library=process.argv[2];if(!library)throw Error('An actual legacy library path is required');
const child=spawn(resolve(root,'build',process.platform==='win32'?'ffi-fixture.exe':'ffi-fixture'),[],{stdio:['pipe','pipe','pipe'],windowsHide:true});
child.stderr.resume();const exit=once(child,'exit');let s;
try{
 const lines=createInterface({input:child.stdout});const [line]=await once(lines,'line');lines.close();const fixture=JSON.parse(line);
 const profileSpec=new ProfileSpec(JSON.parse(readFileSync(resolve(root,'tests/fixtures/profile_spec.json'),'utf8')));
 s=new Session({library,caFile:fixture.ca_file});assert.equal((await s.get(fixture.https2+'/status?code=204')).status,204);
 const baseline=s.stats().open_sessions;
 for(const option of [{addressPolicy:{}},{profileSpec},{httpVersion:'h3'}])assert.throws(()=>new Session({library,...option}),e=>e.code==='unsupported_capability');
 for(const option of [{profileSpec},{httpVersion:'h3'}])await assert.rejects(s.get(fixture.https2,option),e=>e.code==='unsupported_capability');
 assert.equal(s.stats().open_sessions,baseline);assert.equal(s.stats().open_requests,0);
 console.log(JSON.stringify({success:true,legacy_library:resolve(library),new_capabilities_rejected:5,default_request_passed:true}));
}finally{await s?.closeAsync();child.stdin.end();const timer=setTimeout(()=>child.kill(),10000);try{await exit;}finally{clearTimeout(timer);}}
