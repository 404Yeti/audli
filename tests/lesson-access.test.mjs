import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadLesson, classify, isTransient, failureMessage, newClientReference } from '../web/lib/lesson-access.ts';

// Plain stand-ins for the API layer's errors (recognised structurally, so no browser or fetch is needed).
const apiError = (status, code = null, reference = 'srv-ref-0001') => Object.assign(new Error('x'), { name: 'ApiError', status, code, reference });
const unreachable = (status = 502) => Object.assign(new Error('x'), { name: 'UnreachableError', status, reference: null });
const view = id => ({ attempt: { id, status: 'in_progress', created_at: '2026-01-01T00:00:00Z' }, exercises: [] });

function harness(overrides = {}) {
  const calls = [];
  const stored = new Set(['lesson-1']);
  const deps = {
    calls, stored,
    currentAttemptId: async (lesson) => { calls.push('current'); return null; },
    getAttempt: async (id) => { calls.push('get:' + id); return view(id); },
    startAttempt: async (lesson, fresh) => { calls.push('start' + (fresh ? ':fresh' : '')); return view('new'); },
    forgetRequestId: (lesson) => { calls.push('forget'); stored.delete(lesson); },
    sleep: async () => { calls.push('sleep'); },
    newReference: () => 'C-local0001',
    limits: { read: 40, write: 40 },
    ...overrides,
  };
  return deps;
}
const never = signal => new Promise((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
const run = (deps, fresh = false, signal = new AbortController().signal) => loadLesson('lesson-1', fresh, deps, signal);

test('a saved attempt is resumed without ever writing', async () => {
  const deps = harness({ currentAttemptId: async () => 'saved' });
  const result = await run(deps);
  assert.equal(result.kind, 'ready'); assert.equal(result.existing, true);
  assert.deepEqual(deps.calls, ['get:saved']);
});

test('with no saved attempt one is started, and that is not "resumed"', async () => {
  const deps = harness();
  const result = await run(deps);
  assert.equal(result.kind, 'ready'); assert.equal(result.existing, false);
  assert.deepEqual(deps.calls, ['current', 'start']);
});

test('a read that fails transiently is retried with backoff, then succeeds', async () => {
  let n = 0;
  const deps = harness({ currentAttemptId: async () => { if (n++ < 2) throw unreachable(502); return 'saved'; } });
  const result = await run(deps);
  assert.equal(result.kind, 'ready');
  assert.equal(deps.calls.filter(c => c === 'sleep').length, 2);
});

test('retries are bounded: three reads, then a failure with a reference', async () => {
  let n = 0;
  const deps = harness({ currentAttemptId: async () => { n++; throw unreachable(503); } });
  const result = await run(deps);
  assert.equal(n, 3);
  assert.equal(result.kind, 'error'); assert.equal(result.failure.kind, 'unreachable'); assert.equal(result.failure.transient, true);
  assert.equal(result.failure.reference, 'C-local0001');
});

test('a hung request gives up at the deadline instead of loading forever', async () => {
  let n = 0;
  const deps = harness({ currentAttemptId: (lesson, signal) => { n++; return never(signal); } });
  const started = Date.now();
  const result = await run(deps);
  assert.equal(result.kind, 'error'); assert.equal(result.failure.kind, 'timeout');
  assert.equal(n, 3); assert.ok(Date.now() - started < 2000);
});

test('4xx answers are final: never retried (401, 404, 422, 429, and a plain 409)', async () => {
  for (const status of [401, 404, 422, 429, 409]) {
    let n = 0;
    const deps = harness({ currentAttemptId: async () => { n++; throw apiError(status); } });
    const result = await run(deps);
    assert.equal(n, 1, `status ${status}`);
    assert.equal(result.kind, 'error'); assert.equal(result.failure.transient, false);
    assert.equal(result.failure.reference, 'srv-ref-0001');                     // the server's reference wins when a response arrived
  }
});

test('a 500 is a real fault and is not retried; 502/503/504 are', () => {
  assert.equal(isTransient(apiError(500)), false);
  for (const s of [502, 503, 504]) assert.equal(isTransient(apiError(s)), true);
  assert.equal(isTransient(new TypeError('Failed to fetch')), true);
  assert.equal(isTransient(apiError(409, 'request_id_conflict')), false);
});

test('start with an unrelated 409 is final: no forget, no second write', async () => {
  const deps = harness({ startAttempt: async () => { deps.calls.push('start'); throw apiError(409, 'conflict'); } });
  const result = await run(deps);
  assert.equal(result.kind, 'error');
  assert.deepEqual(deps.calls, ['current', 'start']);
});

test('a request-id conflict forgets the poisoned id, re-reads, and starts once more with a new id', async () => {
  let writes = 0;
  const deps = harness({ startAttempt: async () => { deps.calls.push('start'); if (writes++ === 0) throw apiError(409, 'request_id_conflict'); return view('recovered'); } });
  const result = await run(deps);
  assert.equal(result.kind, 'ready');
  assert.deepEqual(deps.calls, ['current', 'start', 'forget', 'current', 'start']);
  assert.equal(deps.stored.has('lesson-1'), false);
});

test('a request-id conflict adopts an attempt that appeared meanwhile (another tab) instead of writing again', async () => {
  let reads = 0;
  const deps = harness({
    currentAttemptId: async () => { deps.calls.push('current'); return reads++ === 0 ? null : 'from-other-tab'; },
    startAttempt: async () => { deps.calls.push('start'); throw apiError(409, 'request_id_conflict'); },
  });
  const result = await run(deps);
  assert.equal(result.kind, 'ready'); assert.equal(result.existing, true);
  assert.deepEqual(deps.calls, ['current', 'start', 'forget', 'current', 'get:from-other-tab']);
});

test('a request-id conflict that persists stops after ONE recovery (no loop)', async () => {
  let writes = 0;
  const deps = harness({ startAttempt: async () => { writes++; throw apiError(409, 'request_id_conflict'); } });
  const result = await run(deps);
  assert.equal(writes, 2); assert.equal(result.kind, 'error'); assert.equal(result.failure.kind, 'conflict');
});

test('an interrupted start (response lost) re-reads the current attempt before writing again', async () => {
  let reads = 0;
  const deps = harness({
    currentAttemptId: async () => { deps.calls.push('current'); return reads++ === 0 ? null : 'created-by-first-try'; },
    startAttempt: async () => { deps.calls.push('start'); throw new TypeError('Failed to fetch'); },
  });
  const result = await run(deps);
  assert.equal(result.kind, 'ready'); assert.equal(result.existing, true);
  assert.deepEqual(deps.calls, ['current', 'start', 'sleep', 'current', 'get:created-by-first-try']);
});

test('an interrupted start that did not land is replayed ONCE with the same stored id', async () => {
  let writes = 0;
  const deps = harness({ startAttempt: async (lesson, fresh) => { deps.calls.push('start' + (fresh ? ':fresh' : '')); if (writes++ === 0) throw unreachable(504); return view('second-try'); } });
  const result = await run(deps);
  assert.equal(result.kind, 'ready');
  assert.deepEqual(deps.calls, ['current', 'start', 'sleep', 'current', 'start']);     // second write is not "fresh": same id, so the server replays
});

test('"listen again" never adopts the old finished attempt after an interrupted start', async () => {
  let writes = 0;
  const deps = harness({ startAttempt: async (lesson, fresh) => { deps.calls.push('start' + (fresh ? ':fresh' : '')); if (writes++ === 0) throw new TypeError('x'); return view('new-run'); } });
  const result = await run(deps, true);
  assert.equal(result.kind, 'ready'); assert.equal(result.existing, false);
  assert.ok(!deps.calls.includes('current'));
});

test('leaving the page cancels the load and reports nothing', async () => {
  const controller = new AbortController();
  const deps = harness({ currentAttemptId: (lesson, signal) => never(signal) });
  const pending = run(deps, false, controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
});

test('failures are classified without leaking server text, and copy never shows a status code', () => {
  assert.equal(classify(apiError(401)).kind, 'auth');
  assert.equal(classify(apiError(404)).kind, 'missing');
  assert.equal(classify(new TypeError('Failed to fetch')).kind, 'network');
  for (const kind of ['timeout', 'network', 'unreachable', 'server', 'auth', 'missing', 'conflict', 'limited', 'rejected']) {
    const { headline, detail } = failureMessage(kind);
    assert.match(headline, /couldn’t load your lesson/);
    assert.doesNotMatch(headline + detail, /\b(4\d\d|5\d\d)\b|FastAPI|Traceback/);
  }
  assert.match(newClientReference(() => '12345678-1234-1234-1234-123456789abc'), /^C-[0-9a-f]{10}$/);
});

test('the whole load, retries and recovery included, stops at the overall budget', async () => {
  let writes = 0;
  const deps = harness({ limits: { read: 5000, write: 5000, total: 60 }, startAttempt: (lesson, fresh, signal) => { writes++; return never(signal); } });
  const started = Date.now();
  const result = await run(deps);
  assert.equal(result.kind, 'error'); assert.equal(result.failure.kind, 'timeout'); assert.equal(result.failure.transient, true);
  assert.ok(Date.now() - started < 1000); assert.equal(writes, 1);
});

test('the real budget is a minute or less, with at most three reads and two writes', async () => {
  const { TOTAL_BUDGET_MS, READ_TIMEOUT_MS, WRITE_TIMEOUT_MS, RETRY_DELAYS_MS } = await import('../web/lib/lesson-access.ts');
  assert.ok(TOTAL_BUDGET_MS <= 60_000 && READ_TIMEOUT_MS < TOTAL_BUDGET_MS && WRITE_TIMEOUT_MS < TOTAL_BUDGET_MS);
  assert.equal(RETRY_DELAYS_MS.length, 2);
});
