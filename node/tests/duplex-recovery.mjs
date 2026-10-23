import test from 'node:test';
import { writeFileSync } from 'node:fs';
import { session, fixture, assert, delay, eventually, SLAB, resources, root, randomUUID, control } from './harness.mjs';

const seconds = Number(process.env.FASTLS_NODE_DUPLEX_SOAK_SECONDS ?? 60);
assert.ok(Number.isInteger(seconds) && seconds >= 1 && seconds <= 300);
test('repeated duplex success, cancellation, manual exchange and rejection recover resources', { timeout: seconds * 1000 + 45000 }, async t => {
  const s = session(t, { timeoutMs: 10000 });
  const active = new Map();
  const proof = { node: process.version, platform: process.platform, arch: process.arch,
    concurrency: 32, passed: false, requests: 0, finalized: 0, warmup_requests: 96, samples: [] };
  let started;
  const batch = async () => {
    await Promise.all(Array.from({ length: 32 }, async (_, index) => {
      const id = randomUUID(), record = { id, kind: index % 4, phase: 'opening' };
      active.set(id, record);
      try {
        if (index % 4 === 1) {
          const body = signal => (async function* () {
            try { yield SLAB; await delay(60000, null, { signal }); }
            finally { proof.finalized++; }
          })();
          const response = await s.stream('POST', fixture.https2 + '/duplex?id=' + id, { duplex: true, body });
          record.phase = 'first read'; await response.body[Symbol.asyncIterator]().next();
          record.phase = 'cancel cleanup'; response.cancel(); await response.closeAsync();
        } else if (index % 4 === 2) {
          const exchange = s.duplex('POST', fixture.https2 + '/duplex?id=' + id), response = await exchange.response;
          const reading = response.read(); void reading.catch(() => {});
          record.phase = 'send'; await exchange.send(SLAB);
          record.phase = 'finish'; await exchange.finishUpload();
          record.phase = 'read'; const content = await reading;
          assert.equal(content.subarray(0, 6).toString(), 'ready\n'); assert.deepEqual(content.subarray(6), SLAB);
          record.phase = 'wait upload'; await response.waitUpload();
        } else if (index % 4 === 3) {
          const exchange = s.duplex('POST', fixture.https2 + '/upload-reject?id=' + id), response = await exchange.response;
          assert.equal(response.status, 413); record.phase = 'rejected send';
          await assert.rejects(exchange.send(SLAB), /server closed the upload/);
          record.phase = 'rejection read'; assert.equal(await response.text(), '');
          await assert.rejects(response.waitUpload(), /before EOF was accepted/);
        } else {
          record.phase = 'automatic request';
          const response = await s.post(fixture.https2 + '/duplex?id=' + id, { duplex: true, body: [SLAB] });
          assert.equal(response.content.subarray(0, 6).toString(), 'ready\n'); assert.deepEqual(response.content.subarray(6), SLAB);
        }
      } catch (error) {
        proof.failure ??= { error: String(error), request: { ...record }, active: [...active.values()].map(item => ({ ...item })) };
        throw error;
      } finally { active.delete(id); }
    }));
    await eventually(() => s.stats().workers === 0 && s.producers.size === 0);
    assert.equal(s.stats().open_requests, 0); proof.requests += 32;
  };
  try {
    for (let i = 0; i < 3; i++) await batch();
    global.gc?.(); proof.baseline = resources(); started = performance.now();
    while (performance.now() - started < seconds * 1000) {
      await batch(); proof.samples.push(resources()); await delay(50);
    }
    assert.equal(proof.finalized, proof.requests / 4);
    await s.closeAsync(); proof.final = resources();
    assert.ok(proof.final.goroutines <= proof.baseline.goroutines + 16);
    assert.ok(proof.final.descriptors_or_handles <= proof.baseline.descriptors_or_handles + 64);
    assert.ok(proof.final.rss_bytes <= proof.baseline.rss_bytes + 96 * 1024 ** 2);
    assert.ok(Math.max(...proof.samples.map(item => item.rss_bytes)) <= proof.baseline.rss_bytes + 128 * 1024 ** 2);
    assert.ok(Math.max(...proof.samples.map(item => item.heap_inuse_bytes)) <= proof.baseline.heap_inuse_bytes + 96 * 1024 ** 2);
    proof.passed = true;
  } catch (error) {
    proof.error = String(error); proof.final = resources();
    if (proof.failure) proof.failure.states = await Promise.allSettled(proof.failure.active.map(item => control('/state', item.id)));
    throw error;
  } finally {
    proof.seconds = started === undefined ? 0 : (performance.now() - started) / 1000;
    const label = process.env.FASTLS_NODE_PACKAGE ? 'package' : 'source';
    writeFileSync(`${root}/build/node-duplex-recovery-${label}-${Date.now()}.json`, JSON.stringify(proof, null, 2));
  }
});
