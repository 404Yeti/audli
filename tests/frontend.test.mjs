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
