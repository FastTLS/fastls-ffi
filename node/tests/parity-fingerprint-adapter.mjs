import { parity, session, fixture, assert, sdk, Native, root, readFileSync, randomUUID } from './harness.mjs';
import { resolve, join } from 'node:path';
import { tmpdir } from 'node:os';
import { mkdirSync, rmSync } from 'node:fs';
const p = (name, fn) => parity('test_fingerprint_adapter.py', name, fn);
const base = () => new sdk.ProfileSpec(JSON.parse(readFileSync(resolve(root,'tests/fixtures/profile_spec.json'),'utf8')));
const H2 = '2:0;4:131072;1:4096|100000|3:1:0:201|p,a,m,s';
const ja3 = spec => {
  const s = spec.toJSON().tls, extensions = new Map(s.extensions.map(e=>[e.id,e]));
  const clean = values => values.map(Number).filter(c=>(c&0x0f0f)!==0x0a0a).join('-');
  return ['771',clean([...s.cipher_suites].reverse()),clean(s.extensions.map(e=>e.id)),clean(extensions.get('0x000a').groups),[...Buffer.from(extensions.get('0x000b').point_formats,'base64')].join('-')].join(',');
};
p('test_compact_parameters_on_independent_peer',async t=>{
  const original=base(), spec=sdk.ProfileSpec.fromFingerprints(original,{ja3:ja3(original),h2:H2}), value=spec.toJSON();
  const owner=session(t,{profileSpec:spec}), wire=(await owner.get(fixture.wire)).json();
  assert.deepEqual(wire.ciphers,value.tls.cipher_suites.map(Number));
  assert.deepEqual(wire.settings,value.http2.settings);
  assert.equal(wire.connection_flow,100000);
  assert.deepEqual(wire.priorities,value.http2.priority_frames);
  assert.deepEqual(wire.headers.slice(0,4),[':path',':authority',':method',':scheme']);
  assert.ok(wire.alpn.includes('h2'));
  assert.deepEqual(wire.groups,value.tls.extensions.find(e=>e.id==='0x000a').groups.map(Number));
  assert.deepEqual(wire.signatures,value.tls.extensions.find(e=>e.id==='0x000d').signature_algorithms.map(Number));
});
p('test_base_snapshot_and_unencoded_fields',()=>{
  const original=base(), before=original.toJSON(), spec=sdk.ProfileSpec.fromFingerprints(original,{ja3:ja3(original)});
  assert.deepEqual(original.toJSON(),before);
  assert.deepEqual(spec.toJSON().http2,before.http2);
  assert.deepEqual(spec.toJSON().identity,before.identity);
  assert.ok(!spec.toJSON().evidence?.length);
  assert.ok(spec.toJSON().id.endsWith('/custom'));
  const changed=spec.toJSON();changed.tls.cipher_suites.length=0;
  assert.ok(spec.toJSON().tls.cipher_suites.length);
});
p('test_malformed_conflicting_and_unsupported_parameters',()=>{
  const original=base();
  const parts=ja3(original).split(',');parts[2]='9999-'+parts[2];
  for(const options of [{},{ja3:'771,1'},{ja3:ja3(original).replace('771,','772,')},{ja3:parts.join(',')},{h2:'2:0;2:0|0|0|m,a,s,p'},{h2:'|0|0|m,m,s,p'},{h2:'|0|3:0:3:1|m,a,s,p'},{h2:'x'.repeat(16385)}])
    assert.throws(()=>sdk.ProfileSpec.fromFingerprints(original,options),error=>error.code==='invalid_configuration');
  assert.throws(()=>sdk.ProfileSpec.fromFingerprints({}, {h2:H2}),TypeError);
  assert.throws(()=>sdk.ProfileSpec.fromFingerprints(original, {h2:1}),TypeError);
});
p('test_missing_native_capability_is_explicit',()=>{
  const native=Native.load(), original=native.call;
  try {
    native.call=function(name,...args){const value=original.call(this,name,...args);return name==='features'?value.filter(f=>f!=='fingerprint_adapter_v1'):value;};
    assert.throws(()=>sdk.ProfileSpec.fromFingerprints(base(),{h2:H2}),error=>error.code==='unsupported_capability');
  } finally {native.call=original;}
});
p('test_adapted_bundle_pin_and_rollback',t=>{
  const directory=join(tmpdir(),'fastls-adapter-'+randomUUID());mkdirSync(directory);t.after(()=>rmSync(directory,{recursive:true,force:true}));
  const store=new sdk.ProfileStore(directory), first=sdk.ProfileBundle.create(base(),{source:'adapter base'}), second=sdk.ProfileBundle.create(sdk.ProfileSpec.fromFingerprints(base(),{h2:H2}),{source:'adapter candidate'});
  for(const [i,bundle] of [first,second].entries()){const file=join(directory,i+'.json');bundle.export(file);store.install(file,bundle.sha256);}
  assert.equal(store.load().sha256,second.sha256);
  assert.equal(store.rollback().sha256,first.sha256);
});
p('test_strict_abi_envelope_and_no_session_allocation',()=>{
  const native=Native.load(), before=native.call('stats').open_sessions;
  for(const text of ['{"schema_version":1,"schema_version":1}','{"schema_version":2}','{"schema_version":1,"base":null,"h2":"x"}','{"schema_version":1,"unexpected":0}']){
    const raw=Buffer.from(text);assert.throws(()=>native.call('profile_adapt',raw,raw.length));
  }
  const raw=Buffer.from(JSON.stringify({schema_version:1,base:base().toJSON(),ja3:null,h2:H2}));
  assert.throws(()=>native.call('profile_adapt',raw,raw.length));
  sdk.ProfileSpec.fromFingerprints(base(),{h2:H2});
  assert.equal(native.call('stats').open_sessions,before);
});
