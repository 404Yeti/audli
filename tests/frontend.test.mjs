import assert from 'node:assert/strict';
import { test } from 'node:test';
import { api } from '../web/lib/api.ts';

// Dependency-free checks of the actual helper imported by the exercise screen.
// These do not replace a Next.js build or browser test.
test('client uses same-origin API paths and disables caching', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.equal(url, '/api/exercises');
    assert.equal(init.method, 'POST');
    assert.equal(init.cache, 'no-store');
    return new Response(JSON.stringify({ id: 'exercise-id', difficulty: { speech_rate: .8 } }), { status: 200 });
  };
  try {
    const result = await api('/exercises', { method: 'POST' });
    assert.equal(result.difficulty.speech_rate, .8);
  } finally { globalThis.fetch = original; }
});

test('client accepts an empty current exercise', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response('null', { status: 200 });
  try { assert.equal(await api('/exercises/current'), null); }
  finally { globalThis.fetch = original; }
});

test('server transcript gate errors remain visible to the learner', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Make a spoken attempt first.' }), { status: 403 });
  try { await assert.rejects(api('/exercises/id/transcript'), /Make a spoken attempt first/); }
  finally { globalThis.fetch = original; }
});

test('HTML from an unavailable backend produces a useful error instead of a JSON parser error', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response('<html>Bad gateway</html>', { status: 502 });
  try { await assert.rejects(api('/profile'), /Check that FastAPI is running/); }
  finally { globalThis.fetch = original; }
});

test('malformed error bodies do not cause property-access exceptions', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response('null', { status: 503 });
  try { await assert.rejects(api('/profile'), /Please check your input/); }
  finally { globalThis.fetch = original; }
});

test('transcription response status is observable even when the server rejects the upload', async () => {
  const original = globalThis.fetch;
  const statuses = [];
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Recording is invalid.' }), { status: 422 });
  try {
    await assert.rejects(api('/exercises/id/attempts', { method: 'POST' }, status => statuses.push(status)), /Recording is invalid/);
    assert.deepEqual(statuses, [422]);
  } finally { globalThis.fetch = original; }
});

test('verified API requests attach a bearer token without changing JSON or upload headers', async () => {
  const { setAccessTokenProvider } = await import('../web/lib/api.ts');
  const original = globalThis.fetch;
  setAccessTokenProvider(async () => 'private-access-token');
  globalThis.fetch = async (url, init) => {
    assert.equal(url, '/api/profile');
    assert.equal(init.headers.get('Authorization'), 'Bearer private-access-token');
    assert.equal(init.headers.get('Content-Type'), 'application/json');
    assert.equal(init.credentials, 'omit');
    assert.equal(init.redirect, 'error');
    return new Response('{}');
  };
  try { await api('/profile', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{}' }); }
  finally { setAccessTokenProvider(null); globalThis.fetch = original; }
});

test('authenticated audio fetch uses headers, returns a disposable blob URL and rejects external paths', async () => {
  const { audioBlobUrl, setAccessTokenProvider } = await import('../web/lib/api.ts');
  const original = globalThis.fetch;
  setAccessTokenProvider(async () => 'audio-token');
  globalThis.fetch = async (url, init) => {
    assert.equal(url, '/api/exercises/owned/audio');
    assert.equal(init.headers.get('Authorization'), 'Bearer audio-token');
    return new Response(new Blob(['audio'], { type: 'audio/wav' }));
  };
  try {
    const url = await audioBlobUrl('/api/exercises/owned/audio');
    assert.ok(url.startsWith('blob:')); URL.revokeObjectURL(url);
    await assert.rejects(audioBlobUrl('https://evil.example/audio'), /Invalid Audli API path/);
    await assert.rejects(audioBlobUrl('//evil.example/audio'), /Invalid Audli API path/);
  } finally { setAccessTokenProvider(null); globalThis.fetch = original; }
});

test('missing session never sends an unauthenticated learner request', async () => {
  const { setAccessTokenProvider } = await import('../web/lib/api.ts');
  const original = globalThis.fetch;
  let called = false;
  globalThis.fetch = async () => { called = true; return new Response('{}'); };
  setAccessTokenProvider(async () => null);
  try { await assert.rejects(api('/profile'), /sign in/); assert.equal(called, false); }
  finally { setAccessTokenProvider(null); globalThis.fetch = original; }
});

test('401 signals session expiry while a stale account request cannot expire a newer account', async () => {
  const { setAccessTokenProvider } = await import('../web/lib/api.ts');
  const originalFetch = globalThis.fetch, originalWindow = globalThis.window;
  const events = new EventTarget(); let expired = 0;
  events.addEventListener('audli-session-expired', () => expired++);
  globalThis.window = events;
  setAccessTokenProvider(async () => 'old-token');
  globalThis.fetch = async () => new Response('{"detail":"Expired"}', { status: 401 });
  try {
    await assert.rejects(api('/profile'), /Expired/); assert.equal(expired, 1);
    globalThis.fetch = async () => {
      setAccessTokenProvider(async () => 'new-token');
      return new Response('{"detail":"Expired"}', { status: 401 });
    };
    await assert.rejects(api('/profile'), /Expired/); assert.equal(expired, 1);
  } finally { setAccessTokenProvider(null); globalThis.fetch = originalFetch; globalThis.window = originalWindow; }
});

test('a late 401 for an old token does not discard a refreshed session', async () => {
  const { setAccessTokenProvider } = await import('../web/lib/api.ts');
  const originalFetch = globalThis.fetch, originalWindow = globalThis.window;
  let token = 'old-token', expired = 0;
  const events = new EventTarget();
  events.addEventListener('audli-session-expired', () => expired++);
  globalThis.window = events;
  setAccessTokenProvider(async () => token);
  globalThis.fetch = async () => {
    token = 'refreshed-token';
    return new Response('{"detail":"Expired old token"}', { status: 401 });
  };
  try { await assert.rejects(api('/profile'), /Expired old token/); assert.equal(expired, 0); }
  finally { setAccessTokenProvider(null); globalThis.fetch = originalFetch; globalThis.window = originalWindow; }
});

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test('a lesson operation cannot acquire a later account token after delayed permission', async () => {
  const { LessonLifetime, setAccessTokenProvider } = await import('../web/lib/api.ts');
  const original = globalThis.fetch;
  const calls = [], permission = deferred();
  setAccessTokenProvider(async () => 'account-a');
  const lifetime = new LessonLifetime(), operation = lifetime.begin();
  globalThis.fetch = async (...args) => { calls.push(args); return new Response('{}'); };
  const action = (async () => {
    await operation.wait(() => permission.promise);
    await operation.api('/profile', { method: 'PUT', body: '{"name":"A"}' });
    await operation.api('/exercises', { method: 'POST' });
  })();
  const rejected = assert.rejects(action, { name: 'AbortError' });
  lifetime.close();
  setAccessTokenProvider(async () => null);
  setAccessTokenProvider(async () => 'account-b');
  permission.resolve();
  try { await rejected; assert.deepEqual(calls, []); }
  finally { lifetime.close(); setAccessTokenProvider(null); globalThis.fetch = original; }
});

test('auth changes abort requests and discard delayed success before any next request', async () => {
  const { LessonLifetime, setAccessTokenProvider } = await import('../web/lib/api.ts');
  const original = globalThis.fetch, response = deferred();
  const calls = [];
  setAccessTokenProvider(async () => 'account-a');
  const lifetime = new LessonLifetime(), operation = lifetime.begin();
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return response.promise; };
  const action = (async () => {
    await operation.api('/profile', { method: 'PUT' });
    await operation.api('/exercises', { method: 'POST' });
  })();
  const rejected = assert.rejects(action, { name: 'AbortError' });
  await new Promise(resolve => setImmediate(resolve));
  setAccessTokenProvider(async () => 'account-b');
  assert.equal(calls[0].init.signal.aborted, true);
  response.resolve(new Response('{}'));
  try { await rejected; assert.equal(calls.length, 1); assert.equal(calls[0].init.headers.get('Authorization'), 'Bearer account-a'); }
  finally { lifetime.close(); setAccessTokenProvider(null); globalThis.fetch = original; }
});

test('cancelled media acquisition releases a late stream and cannot restart', async () => {
  const { LessonLifetime } = await import('../web/lib/api.ts');
  const lifetime = new LessonLifetime(), operation = lifetime.begin(), media = deferred();
  let stopped = 0, started = false;
  const action = (async () => {
    await operation.wait(() => media.promise, stream => stream.getTracks().forEach(track => track.stop()));
    started = true;
  })();
  const rejected = assert.rejects(action, { name: 'AbortError' });
  operation.cancel();
  media.resolve({ getTracks: () => [{ stop() { stopped++; } }] });
  await rejected;
  assert.equal(stopped, 1); assert.equal(started, false);
  assert.throws(() => operation.assertCurrent(), { name: 'AbortError' });
  lifetime.close();
  assert.throws(() => lifetime.begin(), { name: 'AbortError' });
});

test('account rejection during response parsing prevents state and follow-up work', async () => {
  const { LessonLifetime, setAccessTokenProvider } = await import('../web/lib/api.ts');
  const original = globalThis.fetch, parsed = deferred();
  setAccessTokenProvider(async () => 'account-a');
  const lifetime = new LessonLifetime(), operation = lifetime.begin();
  globalThis.fetch = async () => ({ status: 200, ok: true, json: () => parsed.promise });
  const action = operation.api('/attempts/a/assess', { method: 'POST' });
  const rejected = assert.rejects(action, { name: 'AbortError' });
  await new Promise(resolve => setImmediate(resolve));
  setAccessTokenProvider(async () => null);
  parsed.resolve({ state: 'AWAITING_FOLLOWUP' });
  try { await rejected; }
  finally { lifetime.close(); setAccessTokenProvider(null); globalThis.fetch = original; }
});

test('late audio body never creates a blob URL after its lifetime ends', async () => {
  const { LessonLifetime } = await import('../web/lib/api.ts');
  const original = globalThis.fetch, create = URL.createObjectURL, body = deferred();
  let created = 0;
  const lifetime = new LessonLifetime(), operation = lifetime.begin();
  globalThis.fetch = async () => ({ status: 200, ok: true, blob: () => body.promise });
  URL.createObjectURL = () => { created++; return 'blob:private-audio'; };
  const rejected = assert.rejects(operation.audio('/api/exercises/a/audio'), { name: 'AbortError' });
  await new Promise(resolve => setImmediate(resolve));
  lifetime.close(); body.resolve(new Blob(['audio']));
  try { await rejected; assert.equal(created, 0); }
  finally { globalThis.fetch = original; URL.createObjectURL = create; }
});

test('an account change prevents even a new operation on the old mounted lifetime', async () => {
  const { LessonLifetime, setAccessTokenProvider } = await import('../web/lib/api.ts');
  setAccessTokenProvider(async () => 'a');
  const lifetime = new LessonLifetime();
  setAccessTokenProvider(async () => 'b');
  assert.throws(() => lifetime.begin(), { name: 'AbortError' });
  lifetime.close(); setAccessTokenProvider(null);
});

test('session generation stays stable across token refresh and changes for a new login', async () => {
  const { sessionIdentity } = await import('../web/lib/auth.ts');
  const session = (id, sessionId, nonce) => ({ user: { id }, access_token: `header.${Buffer.from(JSON.stringify({ session_id: sessionId, nonce })).toString('base64url')}.signature` });
  assert.equal(sessionIdentity(session('a', 'first', 1)), sessionIdentity(session('a', 'first', 2)));
  assert.notEqual(sessionIdentity(session('a', 'first', 1)), sessionIdentity(session('a', 'second', 1)));
  assert.notEqual(sessionIdentity(session('a', 'first', 1)), sessionIdentity(session('b', 'first', 1)));
  assert.equal(sessionIdentity(null), null);
});

test('delayed token acquisition cannot send a request after the account generation changes', async () => {
  const { LessonLifetime, setAccessTokenProvider } = await import('../web/lib/api.ts');
  const original = globalThis.fetch, token = deferred();
  let sent = false;
  setAccessTokenProvider(() => token.promise);
  const lifetime = new LessonLifetime(), operation = lifetime.begin();
  globalThis.fetch = async () => { sent = true; return new Response('{}'); };
  const rejected = assert.rejects(operation.api('/profile', { method: 'PUT' }), { name: 'AbortError' });
  setAccessTokenProvider(async () => 'account-b'); token.resolve('account-b');
  try { await rejected; assert.equal(sent, false); }
  finally { lifetime.close(); setAccessTokenProvider(null); globalThis.fetch = original; }
});
