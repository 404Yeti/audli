import { test, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';
import { AuthDiagnostics, markAuthTimeoutRetry, timedAuthFetch } from '../web/lib/auth-diagnostics.ts';

let time, logs, values;
function setup() {
  time = 100; logs = []; values = new Map();
  mock.method(performance, 'now', () => time);
  mock.method(console, 'warn', (...args) => logs.push(args));
  globalThis.sessionStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
}
afterEach(() => { mock.restoreAll(); delete globalThis.sessionStorage; });
const records = () => logs.map(([prefix, record]) => {
  assert.equal(prefix, '[Audli auth startup]');
  assert.deepEqual(Object.keys(record).sort(), ['category', 'elapsedMs', 'stage']);
  assert.ok(Number.isInteger(record.elapsedMs) && record.elapsedMs >= 0);
  return record;
});

test('normal initialization and refresh are silent; original fetch arguments and response survive', async () => {
  setup(); const diagnostic = new AuthDiagnostics(); diagnostic.configComplete();
  const response = new Response('{}');
  const input = 'https://example.test/auth/v1/token?grant_type=refresh_token';
  const init = { method: 'POST', body: 'SECRET_REFRESH', headers: { Authorization: 'SECRET_ACCESS' } };
  mock.method(globalThis, 'fetch', async (actualInput, actualInit) => {
    assert.equal(actualInput, input); assert.equal(actualInit, init); return response;
  });
  assert.equal(await timedAuthFetch(input, init), response);
  diagnostic.ready(); diagnostic.fail('timeout');
  assert.deepEqual(logs, []);
});

test('config failure only records allowlisted category and duration', () => {
  setup(); const diagnostic = new AuthDiagnostics(); time += 350;
  diagnostic.fail('http_5xx'); diagnostic.fail('timeout');
  assert.deepEqual(records(), [{ stage: 'auth_config', elapsedMs: 350, category: 'http_5xx' }]);
});

test('deadline separates completed config, pending refresh and pending session; late fetch cannot log', async () => {
  setup(); let release;
  mock.method(globalThis, 'fetch', () => new Promise(resolve => { release = resolve; }));
  const abandoned = new AuthDiagnostics(); time += 200; abandoned.configComplete();
  const pending = timedAuthFetch('https://example.test/auth/v1/token?grant_type=refresh_token&secret=SECRET');
  // A replacement effect observes an already-pending SDK refresh as well.
  abandoned.close(); const diagnostic = new AuthDiagnostics(); diagnostic.configComplete();
  time += 20_000; diagnostic.fail('timeout');
  const rows = records();
  assert.equal(rows.find(row => row.stage === 'token_refresh').category, 'pending');
  assert.equal(rows.find(row => row.stage === 'token_refresh').elapsedMs, 20_000);
  assert.equal(rows.find(row => row.stage === 'session_restore').category, 'pending');
  assert.equal(rows.at(-1).stage, 'auth_gate_deadline');
  assert.equal(rows.at(-1).category, 'timeout');
  const before = logs.length; release(new Response('{}')); await pending;
  assert.equal(logs.length, before); assert.ok(!JSON.stringify(logs).includes('SECRET'));
});

test('refresh failure is recorded even when SDK completes initialization without a session', async () => {
  setup(); const diagnostic = new AuthDiagnostics(); diagnostic.configComplete();
  mock.method(globalThis, 'fetch', async () => { time += 450; return new Response('{}', { status: 400 }); });
  await timedAuthFetch('https://example.test/auth/v1/token?grant_type=refresh_token'); diagnostic.ready();
  assert.ok(records().some(row => row.stage === 'token_refresh' && row.category === 'http_4xx' && row.elapsedMs === 450));
});

test('network rejection is rethrown unchanged without logging the exception', async () => {
  setup(); const diagnostic = new AuthDiagnostics(); diagnostic.configComplete();
  const failure = new Error('SECRET_EMAIL_AND_COOKIE');
  mock.method(globalThis, 'fetch', async () => { throw failure; });
  await assert.rejects(timedAuthFetch('https://example.test/auth/v1/token?grant_type=refresh_token'), error => error === failure);
  diagnostic.fail('timeout'); assert.ok(records().some(row => row.category === 'network'));
  assert.ok(!JSON.stringify(logs).includes('SECRET'));
});

test('manual timeout retry emits one recovery then clears its short-lived marker', () => {
  setup(); markAuthTimeoutRetry(); const diagnostic = new AuthDiagnostics();
  time += 75; diagnostic.configComplete(); time += 25; diagnostic.ready(); diagnostic.ready();
  assert.deepEqual(records().at(-1), { stage: 'retry_connection', elapsedMs: 100, category: 'connected' });
  assert.equal(values.size, 0); const count = logs.length;
  new AuthDiagnostics().ready(); assert.equal(logs.length, count);
});

test('expired retry marker never reports a later unrelated connection as recovery', () => {
  setup(); values.set('audli-auth-timeout-retry', String(Date.now() - 300_001));
  new AuthDiagnostics().ready(); assert.deepEqual(logs, []);
});

test('unavailable storage and console cannot break initialization, failure or retry', () => {
  setup(); globalThis.sessionStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
  mock.method(console, 'warn', () => { throw new Error('blocked'); });
  assert.doesNotThrow(() => { markAuthTimeoutRetry(); new AuthDiagnostics().fail('timeout'); new AuthDiagnostics().ready(); });
});
