import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';

const { parseUploadPolicy, sendTimeoutMs, createPolicyLoader, DISABLED } = await import('../web/lib/upload-policy.ts');
const shared = JSON.parse(readFileSync(new URL('./fixtures/upload_policy_cases.json', import.meta.url), 'utf8'));
const document = overrides => ({ enabled: true, ...shared.defaults, ...overrides });

test('the browser accepts and rejects exactly the policies the server does (shared cases)', () => {
  for (const { name, overrides, valid } of shared.cases) {
    const parsed = parseUploadPolicy(document(overrides));
    assert.equal(parsed.enabled, valid, name);
  }
});

test('the documented defaults parse to the reviewed numbers', () => {
  const policy = parseUploadPolicy(document({}));
  assert.deepEqual(policy, { enabled: true, baseMs: 8000, bytesPerSecond: 64000, minMs: 9000, maxMs: 30000, growth: [1, 1.25, 1.5], maxSends: 3,
    deadlineMs: 50000, backoffMs: [300, 900], slowHintMs: 3000 });
});

test('anything that is not a complete enabled policy is disabled', () => {
  for (const value of [undefined, null, 0, 'x', [], {}, { enabled: false }, { enabled: 'true' }, { enabled: 1 }, { enabled: true }, { enabled: true, base_ms: 8000 },
    { ...document({}), enabled: false }, { ...document({}), extra: 1 }, { ...document({}), growth: undefined }]) {
    assert.deepEqual(parseUploadPolicy(value), DISABLED, JSON.stringify(value));
  }
});

test('the per-send timeout scales with the recording and grows with each send (the evaluated table)', () => {
  const policy = parseUploadPolicy(document({}));
  const kb = n => Math.round(n * 1000);
  const row = bytes => [0, 1, 2].map(send => sendTimeoutMs(policy, bytes, send));
  assert.deepEqual(row(kb(132)), [10063, 12578, 15094]);        // a 5 s answer: 10.1 + 12.6 + 15.1 s
  assert.deepEqual(row(kb(263)).slice(0, 1), [12109]);          // 10 s
  assert.equal(sendTimeoutMs(policy, kb(789), 0), 20328);       // 30 s
  assert.equal(sendTimeoutMs(policy, kb(1578), 0), 30000);      // 60 s: capped
  assert.equal(sendTimeoutMs(policy, 0, 0), 9000);              // tiny: floored at min_ms
  assert.equal(sendTimeoutMs(policy, 97_000, 0), 9516);         // the smallest real recording seen
  assert.equal(sendTimeoutMs(policy, 132_000, 9), 15094);       // the last growth factor repeats
  assert.equal(sendTimeoutMs(policy, Number.NaN, 0), 9000);
  assert.equal(sendTimeoutMs(policy, -5, -1), 9000);
});

test('a fixed 8.5 s timeout is what this replaces: a slow-link answer is not timed out early', () => {
  const policy = parseUploadPolicy(document({}));
  // 1 Mbps (125 KB/s), 5 s of server work, 30 s answer (about 789 KB): about 11.8 s total, inside the first timeout of 20.3 s.
  const total = 500 + 5000 + (789_000 / 125_000) * 1000;
  assert.ok(total < sendTimeoutMs(policy, 789_000, 0) && total > 8500);
});

test('the loader reads once, caches an enabled policy for a minute and a disabled one for five, and fails closed', async () => {
  let now = 0, reads = 0, answer = document({});
  const loader = createPolicyLoader(async () => { reads++; if (answer === 'fail') throw new Error('offline'); return answer; }, () => now);
  assert.equal((await Promise.all([loader.get(), loader.get(), loader.get()]))[0].enabled, true);
  assert.equal(reads, 1);                                        // concurrent callers share one read
  now = 59_000; await loader.get(); assert.equal(reads, 1);
  answer = { enabled: false };                                   // the production kill switch
  now = 61_000; assert.equal((await loader.get()).enabled, false); assert.equal(reads, 2);
  now = 61_000 + 299_000; await loader.get(); assert.equal(reads, 2);
  now = 61_000 + 301_000; answer = 'fail';
  assert.deepEqual(await loader.get(), DISABLED); assert.equal(reads, 3);
  now += 10_000; await loader.get(); assert.equal(reads, 3);     // a failure is remembered briefly, not retried on every upload
  now += 25_000; answer = document({}); assert.equal((await loader.get()).enabled, true); assert.equal(reads, 4);
});

test('a policy read that returns nonsense, not JSON or an error is disabled and never throws', async () => {
  for (const answer of ['text', 12, [], { enabled: true }, { enabled: true, base_ms: '8000' }]) {
    const loader = createPolicyLoader(async () => answer);
    assert.deepEqual(await loader.get(), DISABLED);
  }
});
