import { parity, session, fixture, assert, sdk, Native, SLAB, sha, root, readFileSync } from './harness.mjs';
import { resolve } from 'node:path';
const p = (name, fn) => parity('test_capabilities.py', name, fn);
const specValue = () => JSON.parse(readFileSync(resolve(root, 'tests/fixtures/profile_spec.json'), 'utf8'));

p('test_policy_owner_snapshot_and_redirect_binding', async t => {
  const policy = { allow_hosts: ['127.0.0.1'], allow_cidrs: ['127.0.0.0/8'] };
  const allowed = session(t, { addressPolicy: policy });
  const denied = session(t, { addressPolicy: { deny_cidrs: ['127.0.0.0/8'] } });
  policy.allow_hosts.push('localhost');
  assert.equal((await allowed.get(fixture.https2 + '/inspect')).status, 200);
  for (const [owner, path] of [[denied, '/inspect'], [allowed, '/redirect-host']]) {
    await assert.rejects(owner.get(fixture.https2 + path), error => error.code === 'address_denied');
    assert.equal(owner.stats().open_requests, 0);
  }
  assert.equal((await allowed.get(fixture.https2 + '/status?code=204')).status, 204);
});

p('test_configuration_rejection_and_abi_allocation', async t => {
  const s = session(t), baseline = s.stats().open_sessions, native = Native.load();
  for (const input of [{ schema_version: 2 }, { schema_version: 1, address_policy: null },
      { schema_version: 1, profile_spec: {} }, { schema_version: 1, http_version: 'h2' }]) {
    const raw = Buffer.from(JSON.stringify(input));
    assert.throws(() => native.call('transport_create_with_options', raw, raw.length), error => error.code === 'invalid_configuration');
  }
  const duplicate = Buffer.from('{"schema_version":1,"schema_version":1}');
  assert.throws(() => native.call('transport_create_with_options', duplicate, duplicate.length));
  assert.throws(() => native.call('transport_create_with_options', null, 1), error => error.code === 'invalid_configuration');
  assert.equal(s.stats().open_sessions, baseline);
  const originalCall = native.call, originalSymbol = native.transportOptionsAvailable;
  try {
    for (const missingSymbol of [false, true]) {
      native.transportOptionsAvailable = !missingSymbol;
      native.call = function(name, ...args) {
        const value = originalCall.call(this, name, ...args);
        return name === 'features' && !missingSymbol ? value.filter(v => !['profile_spec_v1','http3_v1','transport_address_policy_v1'].includes(v)) : value;
      };
      for (const options of [{ addressPolicy: {} }, { httpVersion: 'h3' }])
        assert.throws(() => session(t, options), error => error.code === 'unsupported_capability');
    }
  } finally { native.call = originalCall; native.transportOptionsAvailable = originalSymbol; }
  assert.equal(s.stats().open_sessions, baseline);
});

p('test_complete_spec_wire_and_pool_isolation', async t => {
  const value = specValue();
  value.http2.settings = [{ id: 2, value: 0 }, { id: 4, value: 65536 }];
  value.headers.pseudo_order = [':path', ':authority', ':method', ':scheme'];
  value.headers.order = ['x-b', 'x-a', 'user-agent', 'accept'];
  const first = new sdk.ProfileSpec(value);
  value.tls.cipher_suites.reverse();
  const second = new sdk.ProfileSpec(value), s = session(t);
  const clean = codes => codes.filter(c => (c & 0x0f0f) !== 0x0a0a);
  for (const profileSpec of [first, second, first]) {
    const wire = (await s.get(fixture.wire, { profileSpec, headers: { 'X-A': 'a', 'X-B': 'b' } })).json();
    assert.deepEqual(clean(wire.ciphers), clean(profileSpec.toJSON().tls.cipher_suites.map(c => Number(c))));
    assert.deepEqual(wire.settings, [{ id: 2, value: 0 }, { id: 4, value: 65536 }]);
    assert.deepEqual(wire.headers.slice(0, 4), [':path', ':authority', ':method', ':scheme']);
    assert.ok(wire.headers.indexOf('x-b') < wire.headers.indexOf('x-a'));
    assert.ok(wire.alpn.includes('h2'));
  }
  assert.equal(s.stats().session_pools, 2);
});

p('test_spec_presence_conflicts_and_snapshot', async t => {
  const value = specValue(), profileSpec = new sdk.ProfileSpec(value);
  value.schema_version = 999;
  const s = session(t, { profileSpec });
  assert.equal((await s.get(fixture.https2 + '/inspect')).status, 200);
  await assert.rejects(s.get(fixture.https2, { profile: 'chrome/156/windows' }));
  assert.throws(() => new sdk.Session({ profile: 'chrome/156/windows', profileSpec }));
  for (const mode of ['missing', 'null', 'empty']) {
    const value = profileSpec.toJSON();
    if (mode === 'missing') delete value.http2.settings;
    else value.http2.settings = mode === 'null' ? null : [];
    if (mode === 'empty') {
      const empty = session(t, { profileSpec: new sdk.ProfileSpec(value) });
      assert.deepEqual((await empty.get(fixture.wire)).json().settings, []);
    } else assert.throws(() => session(t, { profileSpec: new sdk.ProfileSpec(value) }), error => error.code === 'invalid_configuration');
  }
});

p('test_h3_upload_download_sync_async', async t => {
  const s = session(t, { httpVersion: 'h3' }), data = Buffer.concat([SLAB, SLAB, SLAB, SLAB]);
  const response = await s.get(fixture.https3 + '/?n=' + data.length);
  assert.equal(response.httpVersion, 'HTTP/3.0');assert.deepEqual(response.content, data);
  const upload = await s.post(fixture.https3 + '/upload', { body: (async function* () { yield SLAB; yield SLAB; })() });
  assert.equal(upload.json().sha256, sha(Buffer.concat([SLAB, SLAB])));assert.equal(upload.httpVersion, 'HTTP/3.0');
  const buffered = await s.post(fixture.https3 + '/echo', { body: data });assert.deepEqual(buffered.content, data);
  const fetched = await sdk.fetch(fixture.https3 + '/echo', { method: 'POST', body: data, httpVersion: 'h3', caFile: fixture.ca_file });
  assert.equal(fetched.httpVersion, 'HTTP/3.0');assert.deepEqual(Buffer.from(await fetched.arrayBuffer()), data);
});

p('test_h3_cancel_sibling_and_recovery', async t => {
  const s = session(t, { httpVersion: 'h3' });
  const held = await s.stream('GET', fixture.https3 + '/hold?id=node-h3-cancel');
  assert.deepEqual((await held.body[Symbol.asyncIterator]().next()).value, Buffer.from('first'));
  assert.equal((await s.get(fixture.https3 + '/status?code=204')).status, 204);
  held.cancel();await assert.rejects(held.read());held.close();
  assert.equal((await s.get(fixture.https3 + '/status?code=204')).status, 204);
});

p('test_unsupported_combinations_and_no_tcp_fallback', async t => {
  const s = session(t, { httpVersion: 'h3', timeoutMs: 300 });
  for (const options of [{ profile: 'chrome/156/windows' }, { proxy: 'http://127.0.0.1:1' }, { httpVersion: 'auto' }]) {
    await assert.rejects(s.get(fixture.https3, options), error => error.code === 'unsupported_capability');
  }
  await assert.rejects(s.get(fixture.https2 + '/status?code=204'));
  assert.equal(s.stats().open_requests, 0);
});

p('test_h3_policy_combination_and_owner_isolation', async t => {
 const policy={allow_hosts:['127.0.0.1'],allow_cidrs:['127.0.0.0/8']};
 const allowed=session(t,{httpVersion:'h3',addressPolicy:policy}),denied=session(t,{httpVersion:'h3',addressPolicy:{deny_cidrs:['127.0.0.0/8']}});
 policy.allow_hosts[0]='blocked.test';
 assert.equal((await allowed.get(fixture.https3+'/echo')).httpVersion,'HTTP/3.0');
 await assert.rejects(denied.get(fixture.https3),e=>e.code==='address_denied');
 await assert.rejects(denied.get(fixture.https3,{addressPolicy:{}}));
 assert.equal((await allowed.get(fixture.https3+'/status?code=204')).status,204);
 const owner=session(t,{addressPolicy:{allow_cidrs:['127.0.0.0/8']}});
 assert.equal((await owner.get(fixture.https3,{httpVersion:'h3'})).httpVersion,'HTTP/3.0');
 assert.deepEqual((await allowed.post(fixture.https3+'/echo',{body:SLAB})).content,SLAB);
 for (const [httpVersion,scheme] of [['h3','https'],['auto','http']]) {
  const guarded=session(t,{httpVersion,addressPolicy:{deny_cidrs:['127.0.0.0/8','::1/128']}});
  for(const host of ['0.0.0.0','[::]','[::ffff:0.0.0.0]']) await assert.rejects(guarded.get(scheme+'://'+host+':9/'),e=>e.code==='address_denied');
 }
 const fetched=await sdk.fetch(fixture.https3+'/echo',{method:'POST',body:SLAB,httpVersion:'h3',addressPolicy:{allow_cidrs:['127.0.0.0/8']},caFile:fixture.ca_file});
 assert.deepEqual(Buffer.from(await fetched.arrayBuffer()),SLAB);
});

p('test_h3_policy_combined_capability_before_allocation', async t => {
 const monitor=session(t),native=monitor.native,original=native.call;
 const baseline=monitor.stats().open_sessions;
 native.call=function(name,...args){const value=original.call(this,name,...args);return name==='features'?value.filter(v=>v!=='http3_address_policy_v1'):value;};
 try {
  assert.throws(()=>session(t,{httpVersion:'h3',addressPolicy:{}}),e=>e.code==='unsupported_capability');
  const owner=session(t,{addressPolicy:{}});
  await assert.rejects(owner.get(fixture.https3,{httpVersion:'h3'}),e=>e.code==='unsupported_capability');
  assert.equal(owner.stats().open_requests,0);await owner.closeAsync();
 } finally {native.call=original;}
 assert.equal(monitor.stats().open_sessions,baseline);
});


p('test_h3_config_validation_and_legacy_before_allocation', async t => {
 for(const options of [{maxIdleTimeoutMs:true},{maxIdleTimeoutMs:1.5},{maxIdleTimeoutMs:null},{keepAlivePeriodMs:-1},{maxStreamReceiveWindow:2**64},{maxResponseHeaderBytes:1023},{initialStreamReceiveWindow:8388608},{maxIdleTimeoutMs:100,keepAlivePeriodMs:51},{unknown:1},{toString:1}]) assert.throws(()=>new sdk.HTTP3Config(options));
 assert.throws(()=>session(t,{http3Config:new sdk.HTTP3Config()}));
 const monitor=session(t),native=monitor.native,original=native.call,symbol=native.http3ConfigAvailable,baseline=monitor.stats().open_sessions;
 try {
  native.call=function(name,...args){const v=original.call(this,name,...args);return name==='features'?v.filter(f=>f!=='http3_config_v1'):v;};
  assert.throws(()=>session(t,{httpVersion:'h3',http3Config:new sdk.HTTP3Config()}),e=>e.code==='unsupported_capability');
  native.call=original;native.http3ConfigAvailable=false;
  assert.throws(()=>session(t,{httpVersion:'h3',http3Config:new sdk.HTTP3Config()}),e=>e.code==='unsupported_capability');
 } finally {native.call=original;native.http3ConfigAvailable=symbol;}
 assert.equal(monitor.stats().open_sessions,baseline);
});

p('test_h3_config_snapshot_discovery_and_real_parameters', async t => {
 const capability=sdk.http3Capabilities();assert.equal(capability.fields.length,7);assert.ok(Object.isFrozen(capability.defaults));
 const input={initialStreamReceiveWindow:16384,maxStreamReceiveWindow:65536,initialConnectionReceiveWindow:32768,maxConnectionReceiveWindow:131072,maxIdleTimeoutMs:12345,keepAlivePeriodMs:0,maxResponseHeaderBytes:2048};
 const config=new sdk.HTTP3Config(input),owner=session(t,{httpVersion:'h3',http3Config:config});input.maxIdleTimeoutMs=1;config.toJSON().max_idle_timeout_ms=1;
 const effective=owner.effectiveHTTP3Config();assert.deepEqual(effective,{...input,maxIdleTimeoutMs:12345});assert.ok(Object.isFrozen(effective));
 const peer=(await owner.get(fixture.https3+'/h3-config-state')).json();
 assert.equal(peer.initial_stream_receive_window,effective.initialStreamReceiveWindow);assert.equal(peer.initial_connection_receive_window,effective.initialConnectionReceiveWindow);assert.equal(peer.max_idle_timeout_ms,effective.maxIdleTimeoutMs);
 assert.deepEqual((await owner.get(fixture.https3+'/?n=2097152')).content,Buffer.concat(Array(32).fill(SLAB)));
 await assert.rejects(owner.get(fixture.https3,{http3Config:config}));await owner.closeAsync();assert.throws(()=>owner.effectiveHTTP3Config());
 assert.equal(session(t).effectiveHTTP3Config(),undefined);
 assert.equal(session(t,{httpVersion:'h3',http3Config:new sdk.HTTP3Config({maxIdleTimeoutMs:101})}).effectiveHTTP3Config().keepAlivePeriodMs,50.5);
});

p('test_h3_config_header_limits_policy_and_recovery', async t => {
 for(const addressPolicy of [undefined,{allow_cidrs:['127.0.0.0/8']}]) {
  const small=session(t,{httpVersion:'h3',addressPolicy,http3Config:new sdk.HTTP3Config({maxResponseHeaderBytes:1024})});
  const large=session(t,{httpVersion:'h3',addressPolicy,http3Config:new sdk.HTTP3Config({maxResponseHeaderBytes:8192})});
  for(const path of ['/h3-config-headers','/h3-config-trailers']) {await assert.rejects(small.get(fixture.https3+path));assert.deepEqual((await large.get(fixture.https3+path)).content,Buffer.from('ok'));}
  assert.equal((await small.get(fixture.https3+'/status?code=204')).status,204);assert.equal(small.stats().open_requests,0);
 }
 const denied=session(t,{httpVersion:'h3',http3Config:new sdk.HTTP3Config(),addressPolicy:{deny_cidrs:['127.0.0.0/8']}});
 await assert.rejects(denied.get(fixture.https3),e=>e.code==='address_denied');
});

p('test_h3_config_async_cancel_upload_and_sibling', async t => {
 const s=session(t,{httpVersion:'h3',http3Config:new sdk.HTTP3Config({initialStreamReceiveWindow:32768,maxStreamReceiveWindow:131072,initialConnectionReceiveWindow:65536,maxConnectionReceiveWindow:262144,keepAlivePeriodMs:10})});
 assert.deepEqual((await s.post(fixture.https3+'/echo',{body:Buffer.concat([SLAB,SLAB])})).content,Buffer.concat([SLAB,SLAB]));
 const held=await s.stream('GET',fixture.https3+'/hold?id=h3-config-cancel');assert.deepEqual((await held.body[Symbol.asyncIterator]().next()).value,Buffer.from('first'));
 assert.equal((await s.get(fixture.https3+'/status?code=204')).status,204);held.cancel();await held.closeAsync();
 assert.equal((await s.get(fixture.https3+'/status?code=204')).status,204);assert.equal(s.stats().open_requests,0);
});

p('test_h3_config_one_shot_and_request_injection', async t => {
 const config=new sdk.HTTP3Config({initialStreamReceiveWindow:32768,maxStreamReceiveWindow:65536});
 const response=await sdk.fetch(fixture.https3+'/h3-config-state',{httpVersion:'h3',http3Config:config,caFile:fixture.ca_file});assert.equal((await response.json()).initial_stream_receive_window,32768);
 const owner=session(t),before=owner.stats().open_requests;
 assert.throws(()=>owner.native.start({session:owner.handle,url:fixture.https3,http3_config:{}}),e=>e.code==='invalid_configuration');
 await assert.rejects(owner.fetch(fixture.https3,{http3Config:config}));assert.equal(owner.stats().open_requests,before);
});
