import test from 'node:test';
import { writeFileSync } from 'node:fs';
import { session, fixture, assert, sdk, delay, eventually, resources, root, randomUUID } from './harness.mjs';

const seconds = Number(process.env.FASTLS_NODE_SSE_SOAK_SECONDS ?? 60);
assert.ok(Number.isInteger(seconds) && seconds >= 1 && seconds <= 300);
test('repeated SSE completion, early break, invalid headers and parser/decode errors recover resources', { timeout: seconds * 1000 + 45000 }, async t => {
  const s = session(t, { timeoutMs: 10000 });
  const proof = { node: process.version, platform: process.platform, arch: process.arch,
    concurrency: 32, passed: false, requests: 0, warmup_requests: 96, samples: [],
    completed: 0, cancelled: 0, rejected: 0, parser_errors: 0, decode_errors: 0 };
  let started, batchNumber = 0;
  async function consume(events) { const values = []; for await (const event of events) values.push(event.data); return values; }
  const batch = async () => {
    const number = batchNumber++;
    await Promise.all(Array.from({ length: 32 }, async (_, index) => {
      if (index % 4 === 0) {
        const events = await s.sse('GET', fixture.https2 + '/sse-vectors' + (number % 2 ? '?gzip=1' : ''));
        assert.deepEqual(await consume(events), ['第一条\nsecond', '', ' final', '{"ok":true}', '[DONE]']);
        assert.equal(events.lastEventId, ''); proof.completed++;
      } else if (index % 4 === 1) {
        const events = await s.sse('GET', fixture.https2 + '/sse-hold?id=' + randomUUID());
        for await (const event of events) { assert.equal(event.data, 'first'); break; }
        proof.cancelled++;
      } else if (index % 4 === 2) {
        await assert.rejects(s.sse('GET', fixture.https2 + '/sse-bad?duplicate=1&type=text/event-stream'), /Content-Type/);
        proof.rejected++;
      } else if (number % 2) {
        await assert.rejects((async () => consume(await s.sse('GET', fixture.https2 + '/sse-vectors?corrupt=1')))(), sdk.FastlsDecodeError);
        proof.decode_errors++;
      } else {
        await assert.rejects((async () => consume(await s.sse('GET', fixture.https2 + '/sse-many?n=3&size=2048', { maxEventBytes: 1024 })))(), /maxEventBytes/);
        proof.parser_errors++;
      }
    }));
    await eventually(() => s.stats().workers === 0);
    assert.equal(s.stats().open_requests, 0); proof.requests += 32;
  };
  try {
    for (let i = 0; i < 3; i++) await batch();
    global.gc?.(); proof.baseline = resources(); started = performance.now();
    while (performance.now() - started < seconds * 1000) {
      await batch(); proof.samples.push(resources()); await delay(50);
    }
    assert.equal(proof.completed, proof.requests / 4); assert.equal(proof.cancelled, proof.requests / 4);
    assert.equal(proof.rejected, proof.requests / 4); assert.equal(proof.parser_errors + proof.decode_errors, proof.requests / 4);
    await s.closeAsync(); proof.final = resources();
    assert.ok(proof.final.goroutines <= proof.baseline.goroutines + 16);
    assert.ok(proof.final.descriptors_or_handles <= proof.baseline.descriptors_or_handles + 64);
    assert.ok(proof.final.rss_bytes <= proof.baseline.rss_bytes + 96 * 1024 ** 2);
    assert.ok(Math.max(...proof.samples.map(item => item.rss_bytes)) <= proof.baseline.rss_bytes + 128 * 1024 ** 2);
    assert.ok(Math.max(...proof.samples.map(item => item.heap_inuse_bytes)) <= proof.baseline.heap_inuse_bytes + 96 * 1024 ** 2);
    proof.passed = true;
  } catch (error) { proof.error = String(error); proof.final = resources(); throw error; }
  finally {
    proof.seconds = started === undefined ? 0 : (performance.now() - started) / 1000;
    const label = process.env.FASTLS_NODE_PACKAGE ? 'package' : 'source';
    writeFileSync(`${root}/build/node-sse-recovery-${label}-${Date.now()}.json`, JSON.stringify(proof, null, 2));
  }
});
