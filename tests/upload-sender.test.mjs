import assert from 'node:assert/strict';
import { test } from 'node:test';
import { deliver, boundedTask, DeliveryFailed } from '../web/lib/upload-sender.ts';
import { ApiError } from '../web/lib/api.ts';
import { POLICY, fakeEnv } from './upload-fakes.mjs';
const never = () => new Promise(() => {});
async function run(clock, promise, ms = 60000) {
  let result, error;
  promise.then(v => result = v, e => error = e);
  await clock.advance(ms);
  return { result, error };
}
test('repeated lost sends stop at a hard cap even when fetch ignores abort', async () => {
  const c = fakeEnv(); let sends = 0;
  const r = await run(c, deliver({ policy: POLICY, bytes: 132000, env: c.env, attempt: () => { sends++; return never(); } }));
  assert.ok(r.error instanceof DeliveryFailed); assert.equal(sends, 3); assert.equal(c.timers, 0);
});
test('the wall deadline caps large recordings before three full sends', async () => {
  const c = fakeEnv(); let sends = 0;
  const r = await run(c, deliver({ policy: POLICY, bytes: 2e6, env: c.env, attempt: () => { sends++; return never(); } }), 50000);
  assert.equal(r.error.reason, 'deadline'); assert.equal(sends, 2); assert.equal(c.timers, 0);
});
for (const stage of ['send', 'reconcile', 'policy', 'backoff']) test(`cancellation ends ${stage} even when a dependency ignores abort`, async () => {
  const c = fakeEnv(), ctl = new AbortController(); let sends = 0;
  const p = deliver({ policy: POLICY, bytes: 1, env: c.env, cancelled: ctl.signal,
    attempt: () => { sends++; return stage === 'send' ? never() : Promise.reject(new TypeError('Load failed')); },
    reconcile: stage === 'reconcile' ? never : undefined, canRetry: stage === 'policy' ? never : undefined });
  let error; p.catch(e => error = e);
  await c.advance(stage === 'policy' ? 400 : 10); ctl.abort(); await c.settle();
  assert.equal(error.name, 'AbortError'); assert.equal(sends, 1); assert.equal(c.timers, 0);
});
for (const stage of ['reconcile', 'policy']) test(`a hanging ${stage} is bounded by the total wall deadline`, async () => {
  const c = fakeEnv(); let sends = 0;
  const r = await run(c, deliver({ policy: { ...POLICY, deadlineMs: 10000 }, bytes: 1, env: c.env,
    attempt: async () => { sends++; throw new TypeError('Load failed'); },
    reconcile: stage === 'reconcile' ? never : undefined, canRetry: stage === 'policy' ? never : undefined }), 10000);
  assert.ok(r.error instanceof DeliveryFailed); assert.equal(sends, 1); assert.equal(c.timers, 0);
});
test('hidden pages stop immediately, never gain free sends, and expire in wall time', async () => {
  const c = fakeEnv(); let sends = 0, error;
  const p = deliver({ policy: POLICY, bytes: 1, env: c.env, attempt: () => { sends++; return never(); } });
  p.catch(e => error = e); await c.advance(1); c.setHidden(true); await c.settle();
  assert.equal(error.reason, 'background'); await c.advance(100000); c.setHidden(false);
  assert.equal(sends, 1); assert.equal(c.timers, 0);
});
test('starting hidden never sends', async () => {
  const c = fakeEnv({ hidden: true }); let sends = 0;
  const r = await run(c, deliver({ policy: POLICY, bytes: 1, env: c.env, attempt: async () => { sends++; } }));
  assert.equal(r.error.reason, 'background'); assert.equal(sends, 0);
});
test('a failed reconciliation or changed checkpoint stops without blind resend', async () => {
  for (const reconcile of [async () => ({ kind: 'stop' }), async () => { throw new TypeError('offline'); }]) {
    const c = fakeEnv(); let sends = 0;
    const r = await run(c, deliver({ policy: POLICY, bytes: 1, env: c.env, attempt: async () => { sends++; throw new TypeError(); }, reconcile }));
    assert.equal(r.error.reason, 'ambiguous'); assert.equal(sends, 1);
  }
});
test('kill switch is checked before another automatic send', async () => {
  const c = fakeEnv(); let sends = 0;
  const r = await run(c, deliver({ policy: POLICY, bytes: 1, env: c.env, attempt: async () => { sends++; throw new TypeError(); }, canRetry: async () => false }));
  assert.equal(r.error.reason, 'ambiguous'); assert.equal(sends, 1);
});
for (const status of [401,403,413,415,422,429]) test(`HTTP ${status} is final`, async () => {
  const c = fakeEnv(); let sends = 0;
  const r = await run(c, deliver({ policy: POLICY, bytes: 1, env: c.env, attempt: async () => { sends++; throw new ApiError('stop',status); } }));
  assert.equal(r.error.status, status); assert.equal(sends, 1);
});
test('late results cannot resolve timed-out stages', async () => {
  const c = fakeEnv(); let finish;
  const p = boundedTask(() => new Promise(resolve => finish = resolve), 100, undefined, c.env);
  let value, error; p.then(v => value = v, e => error = e);
  await c.advance(100); finish('late'); await c.settle();
  assert.ok(error instanceof DeliveryFailed); assert.equal(value, undefined); assert.equal(c.timers, 0);
});
