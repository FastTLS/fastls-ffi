"""Compact parameters must affect actual peer observations, not only hashes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_stability import FixtureCase, FastlsError
from fastls import ProfileSpec, ProfileBundle, ProfileStore
from fastls._native import Native

SPEC = Path(__file__).parent / 'fixtures/profile_spec.json'
H2 = '2:0;4:131072;1:4096|100000|3:1:0:201|p,a,m,s'


def base():
    value=json.loads(SPEC.read_text(encoding='utf-8'))
    value['capabilities']['required_features']=list(value['capabilities'].get('required_features') or [])
    return ProfileSpec(value)


def ja3(spec):
    s = spec.to_dict()['tls']
    clean = lambda values: '-'.join(str(int(c, 16)) for c in values if int(c, 16) & 0x0f0f != 0x0a0a)
    exts = {e['id']: e for e in s['extensions']}
    import base64
    points = base64.b64decode(exts['0x000b']['point_formats'])
    return ','.join(['771', clean(s['cipher_suites'][::-1]), clean([e['id'] for e in s['extensions']]), clean(exts['0x000a']['groups']), '-'.join(map(str, points))])


class FingerprintAdapterAcceptance(FixtureCase):
    def test_compact_parameters_on_independent_peer(self):
        value=base().to_dict()
        value['http2'].update(first_stream_id=3,stream_window_increment=12451840)
        value['capabilities']['required_features'].append('http2.stream.v1')
        original = ProfileSpec(value)
        spec = ProfileSpec.from_fingerprints(original, ja3=ja3(original), h2=H2)
        s = spec.to_dict()
        with self.session(profile_spec=spec) as owner:
            wire = owner.get(self.fixture['wire'],headers={'x-fixture-stream-window':'12451840'}).json()
        self.assertEqual((wire['stream_id'],wire['stream_window_increment']),(3,12451840))
        self.assertEqual(wire['ciphers'], [int(c, 16) for c in s['tls']['cipher_suites']])
        self.assertEqual(wire['settings'], s['http2']['settings'])
        self.assertEqual(wire['connection_flow'], 100000)
        self.assertEqual(wire['priorities'], s['http2']['priority_frames'])
        self.assertEqual(wire['headers'][:4], [':path', ':authority', ':method', ':scheme'])
        self.assertIn('h2', wire['alpn'])
        self.assertEqual(wire['groups'], [int(c,16) for e in s['tls']['extensions'] if e['id']=='0x000a' for c in e['groups']])
        self.assertEqual(wire['signatures'], [int(c,16) for e in s['tls']['extensions'] if e['id']=='0x000d' for c in e['signature_algorithms']])

    def test_base_snapshot_and_unencoded_fields(self):
        value=base().to_dict()
        value['http2'].update(first_stream_id=3, stream_window_increment=12451840)
        value['capabilities']['required_features'].append('http2.stream.v1')
        original=ProfileSpec(value); before=original.to_dict()
        spec=ProfileSpec.from_fingerprints(original,ja3=ja3(original))
        self.assertEqual(original.to_dict(),before)
        self.assertEqual(spec.to_dict()['http2'],before['http2'])
        self.assertEqual(spec.to_dict()['identity'],before['identity'])
        adapted=ProfileSpec.from_fingerprints(original,h2=H2).to_dict()['http2']
        self.assertEqual((adapted['first_stream_id'],adapted['stream_window_increment']),(3,12451840))
        self.assertFalse(spec.to_dict()['evidence'])
        self.assertTrue(spec.to_dict()['id'].endswith('/custom'))
        changed=spec.to_dict();changed['tls']['cipher_suites'].clear()
        self.assertTrue(spec.to_dict()['tls']['cipher_suites'])

    def test_malformed_conflicting_and_unsupported_parameters(self):
        original=base()
        for options in ({}, {'ja3':'771,1'}, {'ja3':ja3(original).replace('771,','772,',1)},
                        {'ja3':','.join(ja3(original).split(',')[:2] + ['9999-' + ja3(original).split(',')[2]] + ja3(original).split(',')[3:])}, {'h2':'2:0;2:0|0|0|m,a,s,p'},
                        {'h2':'|0|0|m,m,s,p'}, {'h2':'|0|3:0:3:1|m,a,s,p'}, {'h2':'x'*16385}):
            with self.subTest(options=options):
                with self.assertRaises(FastlsError) as failure:
                    ProfileSpec.from_fingerprints(original,**options)
                self.assertEqual(failure.exception.code,'invalid_configuration')
        with self.assertRaises(TypeError):ProfileSpec.from_fingerprints({},h2=H2)
        with self.assertRaises(TypeError):ProfileSpec.from_fingerprints(original,h2=1)

    def test_missing_native_capability_is_explicit(self):
        original=Native.call
        def missing(native,name,*args):
            value=original(native,name,*args)
            return [f for f in value if f!='fingerprint_adapter_v1'] if name=='fastls_features' else value
        with patch.object(Native,'call',missing):
            with self.assertRaises(FastlsError) as failure:ProfileSpec.from_fingerprints(base(),h2=H2)
            self.assertEqual(failure.exception.code,'unsupported_capability')
        value=base().to_dict()
        value['http2']['first_stream_id']=3
        value['capabilities']['required_features'].append('http2.stream.v1')
        stream=ProfileSpec(value)
        def old(native,name,*args):
            value=original(native,name,*args)
            return [f for f in value if f!='http2_stream_v1'] if name=='fastls_features' else value
        with patch.object(Native,'call',old):
            with self.assertRaises(FastlsError) as failure: self.session(profile_spec=stream)
            self.assertEqual(failure.exception.code,'unsupported_capability')
            with self.assertRaises(FastlsError) as failure: ProfileSpec.from_fingerprints(stream,h2=H2)
            self.assertEqual(failure.exception.code,'unsupported_capability')

    def test_adapted_bundle_pin_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ProfileStore(directory)
            first=ProfileBundle.create(base(),source='adapter base')
            value=base().to_dict(); value['http2'].update(first_stream_id=3,stream_window_increment=12451840)
            value['capabilities']['required_features'].append('http2.stream.v1')
            second=ProfileBundle.create(ProfileSpec.from_fingerprints(ProfileSpec(value),h2=H2),source='adapter candidate')
            for i,bundle in enumerate((first,second)):
                file=Path(directory)/f'{i}.json';bundle.export(file);store.install(file,sha256=bundle.sha256)
            self.assertEqual(store.load().sha256,second.sha256)
            self.assertEqual(store.load().spec.to_dict()['http2']['first_stream_id'],3)
            self.assertEqual(store.rollback().sha256,first.sha256)

    def test_strict_abi_envelope_and_no_session_allocation(self):
        native=Native()
        before=native.call('fastls_stats')['open_sessions']
        for raw in (b'{"schema_version":1,"schema_version":1}',b'{"schema_version":2}',
                    b'{"schema_version":1,"base":null,"h2":"x"}',b'{"schema_version":1,"unexpected":0}'):
            with self.assertRaises(FastlsError):native.call('fastls_profile_adapt',raw,len(raw))
        raw=json.dumps(dict(schema_version=1,base=base().to_dict(),ja3=None,h2=H2)).encode()
        with self.assertRaises(FastlsError):native.call('fastls_profile_adapt',raw,len(raw))
        ProfileSpec.from_fingerprints(base(),h2=H2)
        self.assertEqual(native.call('fastls_stats')['open_sessions'],before)


if __name__=='__main__':unittest.main(verbosity=2)
