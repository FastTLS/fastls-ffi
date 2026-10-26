import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';

const require = createRequire(import.meta.url);
const root = process.env.FASTLS_NODE_PACKAGE
  ? dirname(createRequire(resolve(process.env.FASTLS_NODE_PACKAGE, 'package.json')).resolve('@fastls/fastls'))
  : resolve(import.meta.dirname, '../dist');
const { parseNativeJSON, encodeNativeJSON, asHandle, Native } = require(resolve(root, 'native.js'));

test('uint64 JSON roundtrip never passes through an imprecise Number', () => {
  for (const value of [1n, 9007199254740991n, 9007199254740993n, 18446744073709551615n]) {
    const result = parseNativeJSON(`{"ok":true,"result":${value}}`);
    assert.equal(asHandle(result.result), value);
    assert.equal(encodeNativeJSON({ session: asHandle(result.result) }).toString(), `{"session":${value}}`);
  }
  for (const value of [0, -1, 1.2, 9007199254740992, '1', 18446744073709551616n]) {
    assert.throws(() => asHandle(value));
  }
  assert.throws(() => encodeNativeJSON({ x: '中'.repeat(10) }, 20), /exceeds/);
});

test('native binding validates invalid handles and owns output buffers', () => {
  const native = Native.load();
  assert.throws(() => native.call('info', 18446744073709551615n), /invalid or closed/);
  assert.throws(() => native.read(18446744073709551615n, Buffer.alloc(16)), /invalid or closed/);
  assert.throws(() => Native.load('/missing-fastls-library'), /Cannot find Fastls/);
});
