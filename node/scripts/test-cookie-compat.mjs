import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {createRequire} from 'node:module';
import {resolve,dirname} from 'node:path';
import {writeFileSync} from 'node:fs';
const root=resolve(import.meta.dirname,'../..'),require=createRequire(import.meta.url);
const entry=process.env.FASTLS_NODE_PACKAGE?createRequire(resolve(process.env.FASTLS_NODE_PACKAGE,'package.json')).resolve('@fastls/fastls'):resolve(root,'node/dist/index.js');
if(!process.argv[2]){
  const defaultLibrary=require(resolve(dirname(entry),'library-path.js')).libraryPath();
  const libraries=process.env.FASTLS_COOKIE_COMPAT_LIBRARIES?JSON.parse(process.env.FASTLS_COOKIE_COMPAT_LIBRARIES):[{library:defaultLibrary,kind:'full'}];
  const results=[];
  for(const spec of libraries){
    assert.ok(['full','legacy','automatic-only'].includes(spec.kind));
    const p=spawnSync(process.execPath,[import.meta.filename,JSON.stringify(spec)],{env:process.env,encoding:'utf8',windowsHide:true,timeout:30000});
    if(p.status!==0)throw Error(p.stderr||p.stdout||String(p.error));
    results.push(JSON.parse(p.stdout.trim().split(/\r?\n/).at(-1)));
  }
  const label=process.env.FASTLS_NODE_PACKAGE?'package':'source';
  writeFileSync(resolve(root,'build',`node-cookie-compat-${label}-${Date.now()}.json`),JSON.stringify({passed:true,results},null,2));
  console.log(`Cookie compatibility: ${results.length} real native libraries passed (${label})`);
}else{
  const spec=JSON.parse(process.argv[2]),{Session}=require(entry);
  const child=spawn(resolve(root,'build',process.platform==='win32'?'ffi-fixture.exe':'ffi-fixture'),[],{stdio:['pipe','pipe','pipe'],windowsHide:true});child.stderr.resume();const exit=once(child,'exit');
  let s;
  try{
    const lines=createInterface({input:child.stdout});
    const [line]=await Promise.race([once(lines,'line'),exit.then(()=>{throw Error('fixture exited');})]);lines.close();const f=JSON.parse(line),url=f.https2.replace(/^http/,'ws')+'/ws';
    s=new Session({library:spec.library,caFile:f.ca_file});s.cookies.set('seed','private',{url:f.https2,path:'/'});
    const inspect=async(options={})=>{const ws=await s.websocket(url,{...options,params:{mode:'inspect',set_cookie:'returned=yes; Path=/'}});try{await ws.send('inspect');return JSON.parse(await ws.recv()).headers.Cookie??[];}finally{await ws.closeAsync();}};
    if(spec.kind==='full'){
      assert.deepEqual(await inspect({cookieMode:'manual'}),[]);assert.equal(s.cookies.get('returned',f.https2),undefined);
      assert.deepEqual(await inspect({cookieMode:'manual',headers:{Cookie:'explicit=yes'}}),['explicit=yes']);assert.equal(s.cookies.get('returned',f.https2),undefined);
      assert.deepEqual(await inspect(),['seed=private']);assert.equal(s.cookies.get('returned',f.https2),'yes');
      await assert.rejects(s.websocket(url,{params:{mode:'bad-accept',set_cookie:'poison=no; Path=/'}}));assert.equal(s.cookies.get('poison',f.https2),undefined);
    }else{
      await assert.rejects(s.websocket(url),/newer/);
      if(spec.kind==='automatic-only')await assert.rejects(inspect({cookieMode:'manual'}),/cannot disable/);
      else assert.deepEqual(await inspect({cookieMode:'manual',headers:{Cookie:'explicit=yes'}}),['explicit=yes']);
      assert.equal(s.cookies.get('returned',f.https2),undefined);
    }
    assert.equal(s.stats().open_requests,0);assert.equal((await s.get(f.https2+'/status?code=204')).status,204);
    console.log(JSON.stringify({...spec,passed:true}));
  }finally{await s?.closeAsync();child.stdin.end();const timer=setTimeout(()=>child.kill(),5000);try{await exit;}finally{clearTimeout(timer);}}
}
