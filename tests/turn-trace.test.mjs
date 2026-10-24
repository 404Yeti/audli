import assert from 'node:assert/strict';
import { test } from 'node:test';

const timing = await import('../web/lib/turn-timing.ts');
const api = await import('../web/lib/api.ts');
const ENV = 'NEXT_PUBLIC_AUDLI_TURN_TRACE';

test('AUD-85: tracing is off unless the build flag is set', () => {
  delete process.env[ENV];
  timing.beginTurnTrace(0);
  assert.equal(timing.activeTurnId(), null);
  timing.recordRequestSpan('upload', 10, 1, 1);
  assert.equal(timing.takeTurnTrace(true), null);
});

test('a turn gets a random 12-hex id, bounded integer spans and ends with the meaningful playback', () => {
  process.env[ENV] = '1';
  timing.beginTurnTrace(1000);
  const first = timing.activeTurnId();
  assert.match(first, /^[0-9a-f]{12}$/);
  timing.recordRequestSpan('upload', 3000.4, 4300.6, 5200);
  timing.recordRequestSpan('assess', 9e9, -5, 9e9);
  assert.deepEqual(timing.takeTurnTrace(false), { turn: first, requests: [
    { kind: 'upload', start: 2000, token: 4301, ttfb: 5200 }, { kind: 'assess', start: 300000, token: 0, ttfb: 300000 }] });
  assert.equal(timing.activeTurnId(), first);             // a retry flush does not end the turn
  timing.recordRequestSpan('ack', 5000, 10, 20);
  assert.equal(timing.takeTurnTrace(true).requests.length, 1);
  assert.equal(timing.activeTurnId(), null);
  timing.beginTurnTrace(0);
  assert.notEqual(timing.activeTurnId(), first);
  timing.takeTurnTrace(true);
  for (let i = 0; i < 40; i++) timing.recordRequestSpan('coach', 0, 0, 0);   // no active turn: ignored
  delete process.env[ENV];
});

test('only the four turn requests have a kind and the number of spans is bounded', () => {
  process.env[ENV] = '1';
  assert.equal(timing.traceKind('/api/exercises/abc/attempts'), 'upload');
  assert.equal(timing.traceKind('/api/lessons/abc/attempts'), 'upload');
  assert.equal(timing.traceKind('/api/attempts/abc/assess'), 'assess');
  assert.equal(timing.traceKind('/api/recognition/acknowledgment-audio'), 'ack');
  assert.equal(timing.traceKind('/api/exercises/abc/coach-audio'), 'coach');
  for (const other of ['/api/profile', '/api/telemetry/turn', '/api/exercises/abc/attempts/x', '/api/exercises//attempts', '/x/api/attempts/a/assess'])
    assert.equal(timing.traceKind(other), null, other);
  timing.beginTurnTrace(0);
  for (let i = 0; i < 40; i++) timing.recordRequestSpan('coach', i, 0, 0);
  assert.equal(timing.takeTurnTrace(true).requests.length, 16);
  delete process.env[ENV];
});

test('the reporter attaches the spans to the final flush only', () => {
  process.env[ENV] = '1';
  const sent = [];
  const reporter = timing.createTurnReporter((stages, trace) => sent.push({ stages, trace }));
  timing.beginTurnTrace(0);
  timing.recordRequestSpan('upload', 1, 2, 3);
  reporter.add('retry_playback', 10);
  assert.equal(sent[0].trace.requests.length, 1);
  timing.recordRequestSpan('assess', 1, 2, 3);
  reporter.add('turn_to_playback', 20);
  assert.equal(sent[1].trace.requests[0].kind, 'assess');
  reporter.add('assessment', 5); reporter.flush();
  assert.equal(sent[2].trace, null);                      // the turn is over: nothing to attach
  delete process.env[ENV];
});

test('authenticatedFetch sends the turn id on same-origin API calls and measures the token wait', async () => {
  process.env[ENV] = '1';
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => { calls.push({ url, headers: init.headers }); return new Response('{}', { status: 200 }); };
  api.installRequestTrace({ turnId: timing.activeTurnId, kindOf: timing.traceKind, record: timing.recordRequestSpan });
  api.setAccessTokenProvider(async () => { await new Promise(resolve => setTimeout(resolve, 30)); return 'token-value'; });
  try {
    timing.beginTurnTrace(performance.now());
    await api.authenticatedFetch('/api/exercises/abc/attempts', { method: 'POST' });
    await api.authenticatedFetch('/api/profile');
    await api.authenticatedFetch('/api/telemetry/turn', { method: 'POST' });
    assert.equal(calls[0].headers.get('X-Audli-Turn'), timing.activeTurnId());
    assert.equal(calls[1].headers.get('X-Audli-Turn'), timing.activeTurnId());      // any same-origin call of the turn carries the id
    assert.equal(calls[2].headers.get('X-Audli-Turn'), null);                       // telemetry itself never does
    const trace = timing.takeTurnTrace(true);
    assert.equal(trace.requests.length, 1);                                         // only the four turn requests are spanned
    assert.equal(trace.requests[0].kind, 'upload');
    assert.ok(trace.requests[0].token >= 25 && trace.requests[0].token < 2000, trace.requests[0].token);
    await api.authenticatedFetch('/api/profile');
    assert.equal(calls[3].headers.get('X-Audli-Turn'), null);                       // no turn, no header
  } finally { globalThis.fetch = original; api.installRequestTrace(null); api.setAccessTokenProvider(null); delete process.env[ENV]; }
});
