import { parity, session, fixture, assert, delay, healthy, randomUUID, Native } from './harness.mjs';
const p=(name,fn)=>parity('test_cookies.py',name,fn);
const dict=(s,url)=>Object.fromEntries([...s.cookies.getAll(url)].reverse().map(c=>[c.name,c.value]));
const echoed=async(s,url,options)=>((await s.get(url+'/inspect',options)).json().headers.Cookie??[]).join('; ');
p('test_repeated_fields_case_insensitive_without_splitting_values',async t=>{
  for(const protocol of ['https1','https2']){const r=await session(t).get(fixture[protocol]+'/cookie-fields'),values=r.headers.getAll('set-cookie');assert.equal(values.length,5);assert.match(values[1],/Expires=Fri, 01 Jan 2100/);
    assert.equal(r.headers.get('SET-cookie'),values[0]);assert.deepEqual(r.headers.getAll('X-LIST'),['a, b','literal/,/value']);assert.deepEqual(r.headers.getAll('missing'),[]);assert.equal(r.headers.get('missing','default'),'default');
    assert.equal([...r.headers.multiItems()].filter(([name])=>name==='set-cookie').length,5);values.length=0;assert.equal(r.headers.getAll('set-cookie').length,5);assert.equal(r.headers.set,undefined);}
});
p('test_automatic_path_secure_host_scope_and_foreign_domain_rejection',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),base=fixture[protocol];await s.get(base+'/cookie-fields');assert.deepEqual(dict(s,base),{root:'one',future:'two',secure:'four'});
    assert.equal(s.cookies.get('private',base+'/private/item'),'three');assert.equal(s.cookies.get('private',base+'/private-other'),undefined);
    assert.equal(s.cookies.get('secure',fixture.http),undefined);assert.equal(s.cookies.get('foreign',base),undefined);assert.deepEqual(dict(s,base.replace('127.0.0.1','localhost')),{});
    assert.match(await echoed(s,base),/secure=four/);assert.doesNotMatch(await echoed(s,fixture.http),/secure=/);}
});
p('test_manual_domain_default_path_duplicate_names_and_exact_delete',async t=>{
  const s=session(t),url='https://shop.example.com';s.cookies.set('id','root',{url,path:'/'});s.cookies.set('id','private',{url:url+'/private/page'});
  assert.deepEqual(s.cookies.getAll(url+'/private/next'),[{name:'id',value:'private'},{name:'id',value:'root'}]);assert.equal(s.cookies.get('id',url+'/private/next'),'private');
  s.cookies.delete('id',{url:url+'/private/page',path:'/private'});assert.equal(s.cookies.get('id',url+'/private/next'),'root');
  s.cookies.set('shared','domain',{url,domain:'.example.com',path:'/'});assert.deepEqual(dict(s,'https://other.example.com'),{shared:'domain'});assert.deepEqual(dict(s,'https://notexample.com'),{});
});
p('test_expiry_max_age_precedence_and_deletion',async t=>{
  const s=session(t),url=fixture.https2;s.cookies.set('past','gone',{url,expires:'2000-01-01T00:00:00Z'});assert.equal(s.cookies.get('past',url),undefined);
  s.cookies.set('short','yes',{url,maxAge:1,expires:'2000-01-01T00:00:00Z'});assert.equal(s.cookies.get('short',url),'yes');await delay(1150);assert.equal(s.cookies.get('short',url),undefined);
  s.cookies.set('remove','yes',{url,path:'/'});s.cookies.delete('remove',{url,path:'/'});assert.equal(s.cookies.get('remove',url),undefined);
  s.cookies.set('zero','yes',{url});s.cookies.set('zero','',{url,maxAge:0});assert.equal(s.cookies.get('zero',url),undefined);
});
p('test_server_overwrites_and_deletes_existing_cookie',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),base=fixture[protocol];for(const value of ['first','second'])await s.get(base+'/cookie-set?name=token&value='+value);
    assert.equal(await echoed(s,base),'token=second');await s.get(base+'/cookie-set?name=token&max_age=-1');assert.deepEqual(dict(s,base),{});assert.equal(await echoed(s,base),'');}
});
p('test_redirect_sends_only_target_hosts_stored_cookies',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),base=fixture[protocol];s.cookies.set('source','private',{url:base});s.cookies.set('target','allowed',{url:base.replace('127.0.0.1','localhost')});assert.deepEqual((await s.get(base+'/redirect-host')).json().headers.Cookie,['target=allowed']);}
});
p('test_redirect_responses_update_store_before_following',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),base=fixture[protocol];const r=await s.get(base+'/cookie-redirect');const outgoing=r.json().headers.Cookie.join('; ');assert.match(outgoing,/first=one/);assert.match(outgoing,/second=two/);assert.deepEqual(dict(s,base),{first:'one',second:'two'});
    s.cookies.clear();assert.equal((await s.get(base+'/cookie-redirect',{allowRedirects:false})).status,302);assert.deepEqual(dict(s,base),{first:'one'});}
});
p('test_explicit_cookie_header_controls_outgoing_and_still_accepts_response',async t=>{
  const s=session(t),url=fixture.https2;await s.get(url+'/cookie-set',{headers:{Cookie:'manual=only'}});assert.equal(s.cookies.get('saved',url),'yes');assert.equal(await echoed(s,url,{headers:{Cookie:'manual=only'}}),'manual=only');assert.equal(await echoed(s,url,{headers:{Cookie:''}}),'');
});
p('test_sessions_isolated_profiles_shared_and_clear_keeps_connection_pool',async t=>{
  const s=session(t),other=session(t),base=fixture.https2;await s.get(base+'/cookie-set');assert.deepEqual(dict(other,base),{});const profile=s.listProfiles().find(id=>id.startsWith('chrome/')&&id!=='chrome/156/windows');
  assert.match(await echoed(s,base,{profile}),/saved=yes/);const before=await s.get(base+'/inspect');s.cookies.clear();const after=await s.get(base+'/inspect');assert.equal(after.json().headers.Cookie,undefined);assert.equal(before.headers.get('x-test-connection'),after.headers.get('x-test-connection'));
});
p('test_disabled_store_and_access_after_session_close',async t=>{
  const s=session(t,{cookies:false}),url=fixture.https2;await s.get(url+'/cookie-set');assert.deepEqual(dict(s,url),{});assert.equal(await echoed(s,url,{headers:{Cookie:'manual=only'}}),'manual=only');assert.throws(()=>s.cookies.set('no','cookie',{url}),/disabled/);s.close();assert.throws(()=>s.cookies.getAll(url),/closed/);
});
p('test_async_concurrent_updates_and_clear',async t=>{
  const s=session(t),url=fixture.https2;const responses=await Promise.all(Array.from({length:32},(_,i)=>s.get(url+`/cookie-set?name=k${i}&value=${i}`)));assert.ok(responses.every(r=>r.headers.getAll('set-cookie').length===1));assert.deepEqual(dict(s,url),Object.fromEntries(Array.from({length:32},(_,i)=>['k'+i,String(i)])));s.cookies.clear();assert.deepEqual(s.cookies.getAll(url),[]);
});
p('test_cookie_headers_available_before_stream_finishes',async t=>{
  for(const protocol of ['https1','https2']){const s=session(t),base=fixture[protocol],r=await s.stream('GET',base+'/cookie-hold?id='+randomUUID());assert.deepEqual(r.headers.getAll('Set-Cookie'),['streamed=ready; Path=/']);const reader=r.body[Symbol.asyncIterator]();assert.equal((await reader.next()).value.toString(),'first');assert.equal(s.cookies.get('streamed',base),'ready');assert.match(await echoed(s,base),/streamed=ready/);r.cancel();assert.equal(s.cookies.get('streamed',base),'ready');}
});
p('test_invalid_cookie_commands_and_inputs_leave_store_unchanged',async t=>{
  const s=session(t),url=fixture.https2;s.cookies.set('valid','kept',{url});
  for(const [name,value,options] of [['bad;name','x',{url}],['bad','a\nb',{url}],['bad','x',{url:'file:///tmp/none'}],['bad','x',{url,domain:'evil.invalid'}],['bad','x',{url:'https://shop.example.co.uk',domain:'.co.uk'}],['bad','x',{url,expires:'tomorrow'}],['bad','x'.repeat(4097),{url}],['bad','x',{url,maxAge:2147483648}]])assert.throws(()=>s.cookies.set(name,value,options));
  for(const command of [{action:'unknown'},{action:'get',url,unknown:true}]){const raw=Buffer.from(JSON.stringify(command));assert.throws(()=>Native.load().call('session_cookies',s.handle,raw,raw.length));}assert.deepEqual(dict(s,url),{valid:'kept'});
});
