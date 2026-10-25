import assert from 'node:assert/strict';
import { test, beforeEach } from 'node:test';

const delivery = await import('../web/lib/upload-delivery.ts');
const recording = await import('../web/lib/recording.ts');
const api = await import('../web/lib/api.ts');
const timing = await import('../web/lib/turn-timing.ts');
const reporting = await import('../web/lib/turn-reporting.ts');

const AUDIO = 'SECRET-SPOKEN-WORDS-AND-AUDIO-BYTES';
const blobOf = (type = 'audio/mp4') => new Blob([AUDIO.repeat(10)], { type });
const formOf = blob => recording.recordingUpload(blob);

function fakePage() {
  const listeners = { document: new Map(), window: new Map() };
  const target = name => ({
    addEventListener(type, fn) { (listeners[name].get(type) ?? listeners[name].set(type, new Set()).get(type)).add(fn); },
    removeEventListener(type, fn) { listeners[name].get(type)?.delete(fn); },
  });
  const doc = { ...target('document'), visibilityState: 'visible' };
  const win = target('window');
  const fire = (name, type) => { for (const fn of [...(listeners[name].get(type) ?? [])]) fn(); };
  const count = () => [...listeners.document.values(), ...listeners.window.values()].reduce((sum, set) => sum + set.size, 0);
  return { doc, win, fire, count };
}

function memoryStorage() {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => { data.set(key, String(value)); }, removeItem: key => { data.delete(key); }, data };
}

beforeEach(() => {
  delivery.resetDeliveries();
  api.installRequestTrace(null);
  delete globalThis.document; delete globalThis.window; delete globalThis.sessionStorage;
});

test('an upload id is random per recording, 16 hex, and stays with the same recording when it is sent again', () => {
  const a = blobOf(), b = blobOf();
  assert.match(delivery.uploadIdFor(a), /^[0-9a-f]{16}$/);
  assert.equal(delivery.uploadIdFor(a), delivery.uploadIdFor(a));
  assert.notEqual(delivery.uploadIdFor(a), delivery.uploadIdFor(b));
  assert.equal(formOf(a)[delivery.UPLOAD_ID], formOf(a)[delivery.UPLOAD_ID]);          // two forms built from one recording
  assert.notEqual(formOf(a)[delivery.UPLOAD_ID], formOf(b)[delivery.UPLOAD_ID]);
});

test('only recording uploads have a kind, and the kind is a fixed enum', () => {
  const expected = {
    '/api/exercises/abc/attempts': 'exercise', '/api/lessons/abc/attempts': 'lesson', '/api/plan/checkin/attempts': 'plan',
    '/api/onboarding/attempts': 'onboarding', '/api/homework/abc/decision/attempts': 'homework', '/api/lessons/abc/homework-review/attempts': 'homework',
  };
  for (const [path, kind] of Object.entries(expected)) assert.equal(delivery.deliveryKind(path), kind, path);
  for (const other of ['/api/profile', '/api/attempts/abc/assess', '/api/exercises/abc/attempts/x', '/api/exercises//attempts', '/x/api/exercises/a/attempts', '/api/telemetry/turn'])
    assert.equal(delivery.deliveryKind(other), null, other);
});

test('mime types collapse to a small enum', () => {
  assert.deepEqual(['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/x-m4a', 'text/html', '']
    .map(delivery.mimeClass), ['webm', 'mp4', 'ogg', 'wav', 'wav', 'mp3', 'm4a', 'other', 'other']);
});

test('error text is reduced to letters and basic punctuation: no URL, id, number or path can get through', () => {
  const out = delivery.sanitizeError(Object.assign(new Error('Failed to fetch https://app.audli.ai/api/exercises/0b9e5d3c-1111-4222-8333-444455556666/attempts?token=abc123 for user@example.com'), { name: 'Type-Error 9' }));
  assert.match(out.err, /^[A-Za-z .,'-]*$/);
  assert.ok(out.err.length <= 60 && out.errname === 'TypeError');
  for (const forbidden of ['http', '://', '0b9e', 'token=', 'abc123', '@']) assert.ok(!out.err.includes(forbidden), forbidden);
  assert.deepEqual(delivery.sanitizeError(undefined), { errname: '', err: '' });
  assert.deepEqual(delivery.sanitizeError({ name: 'N'.repeat(100), message: 'm'.repeat(500) }).errname.length, 40);
});

test('failures are classified as aborted, timeout or network', () => {
  assert.equal(delivery.classifyFailure(new DOMException('x', 'AbortError')), 'aborted');
  assert.equal(delivery.classifyFailure(new DOMException('x', 'TimeoutError')), 'timeout');
  assert.equal(delivery.classifyFailure(new TypeError('Load failed')), 'network');
  assert.equal(delivery.classifyFailure('weird'), 'network');
});

test('nothing is tracked for non-uploads, untagged forms or forms without audio', () => {
  assert.equal(delivery.startDelivery('/api/profile', formOf(blobOf())), null);
  assert.equal(delivery.startDelivery('/api/exercises/a/attempts', JSON.stringify({})), null);
  assert.equal(delivery.startDelivery('/api/exercises/a/attempts', undefined), null);
  const untagged = new FormData(); untagged.append('audio', blobOf(), 'response.mp4');
  assert.equal(delivery.startDelivery('/api/exercises/a/attempts', untagged), null);
  const noAudio = new FormData(); noAudio[delivery.UPLOAD_ID] = '0123456789abcdef'; noAudio.append('revision', '3');
  assert.equal(delivery.startDelivery('/api/lessons/a/attempts', noAudio), null);
  assert.deepEqual(delivery.takeDeliveries(), []);
});

test('a finished send is one bounded record that carries no content, and a resend of the same recording is send 2', () => {
  const blob = blobOf('audio/mp4');
  recording.recordingUpload(blob);
  delivery.rememberRecordingLength(blob, 7123.6);
  delivery.markApiActivity(performance.now() - 15000);
  const first = delivery.startDelivery('/api/exercises/abc/attempts', formOf(blob));
  first.fail(new TypeError('Load failed'));
  const second = delivery.startDelivery('/api/exercises/abc/attempts', formOf(blob));
  second.done(200);
  const [a, b] = delivery.takeDeliveries();
  assert.equal(a.id, b.id); assert.deepEqual([a.n, b.n], [1, 2]);
  assert.deepEqual([a.outcome, a.status, a.errname, a.err], ['network', 0, 'TypeError', 'Load failed']);
  assert.deepEqual([b.outcome, b.status, b.kind, b.mime], ['ok', 200, 'exercise', 'mp4']);
  assert.equal(a.bytes, blob.size); assert.equal(a.rec_ms, 7124);
  assert.ok(a.since_prev_ms >= 14000 && a.since_prev_ms < 20000);
  const raw = JSON.stringify([a, b]);
  for (const forbidden of [AUDIO, '/api/', 'abc', 'Bearer']) assert.ok(!raw.includes(forbidden), forbidden);
  assert.deepEqual(Object.keys(a).filter(key => !['id', 'n', 'kind', 'outcome', 'status', 'ms', 'bytes', 'mime', 'rec_ms', 'since_prev_ms', 'online', 'net', 'rtt', 'downlink_kbps', 'hidden', 'hidden_ms', 'pagehide', 'errname', 'err'].includes(key)), []);
});

test('http statuses are recorded as http and the status is bounded', () => {
  for (const status of [413, 503, 99999]) delivery.startDelivery('/api/lessons/a/attempts', formOf(blobOf())).done(status);
  const records = delivery.takeDeliveries();
  assert.deepEqual(records.map(record => record.outcome), ['http', 'http', 'http']);
  assert.deepEqual(records.map(record => record.status), [413, 503, 599]);
});

test('hidden page time and pagehide during the send are counted, and the listeners are removed afterwards', async () => {
  const page = fakePage();
  globalThis.document = page.doc; globalThis.window = page.win;
  const tracker = delivery.startDelivery('/api/exercises/a/attempts', formOf(blobOf()));
  assert.equal(page.count(), 2);
  page.doc.visibilityState = 'hidden'; page.fire('document', 'visibilitychange');
  await new Promise(resolve => setTimeout(resolve, 30));
  page.doc.visibilityState = 'visible'; page.fire('document', 'visibilitychange');
  page.doc.visibilityState = 'hidden'; page.fire('document', 'visibilitychange'); page.fire('window', 'pagehide');
  tracker.fail(new TypeError('Load failed'));
  assert.equal(page.count(), 0);
  const [record] = delivery.takeDeliveries();
  assert.equal(record.hidden, 2); assert.equal(record.pagehide, true); assert.ok(record.hidden_ms >= 25);
  tracker.done(200);                                   // finishing twice changes nothing
  assert.deepEqual(delivery.takeDeliveries(), []);
});

test('the queue is bounded, ordered, survives a reload through sessionStorage and can be put back', () => {
  const storage = memoryStorage(); globalThis.sessionStorage = storage;
  for (let i = 0; i < 20; i++) delivery.startDelivery('/api/exercises/a/attempts', formOf(blobOf())).done(200);
  assert.equal(JSON.parse(storage.getItem('audli:deliveries')).length, 16);
  const first = delivery.takeDeliveries(8);
  assert.equal(first.length, 8);
  assert.equal(JSON.parse(storage.getItem('audli:deliveries')).length, 8);
  delivery.requeueDeliveries(first);
  assert.equal(delivery.takeDeliveries(100).length, 16);
  assert.equal(JSON.parse(storage.getItem('audli:deliveries')).length, 0);
});

test('unusable sessionStorage content or a storage that throws never affects the send', () => {
  globalThis.sessionStorage = { getItem: () => '{not json', setItem() { throw new Error('quota'); }, removeItem() { throw new Error('denied'); } };
  delivery.resetDeliveries();
  delivery.startDelivery('/api/exercises/a/attempts', formOf(blobOf())).done(200);
  assert.equal(delivery.takeDeliveries().length, 1);
});

async function withFetch(handler, run) {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return handler(url, init); };
  try { await run(calls); } finally { globalThis.fetch = original; }
}

test('with reporting installed the upload carries its id header; without it nothing is observed or added', async () => {
  const blob = blobOf();
  await withFetch(() => new Response('{}', { status: 200 }), async calls => {
    await api.authenticatedFetch('/api/exercises/a/attempts', { method: 'POST', body: formOf(blob) });
    assert.equal(new Headers(calls[0].init.headers).get('X-Audli-Upload-Id'), null);
    assert.deepEqual(delivery.takeDeliveries(), []);
    api.installRequestTrace({ turnId: () => null, kindOf: () => null, record() {} });
    await api.authenticatedFetch('/api/exercises/a/attempts', { method: 'POST', body: formOf(blob) });
    await api.authenticatedFetch('/api/profile', { method: 'GET' });
    await api.authenticatedFetch('/api/lessons/a/answer', { method: 'POST', body: JSON.stringify({ revision: 1 }) });
    assert.equal(new Headers(calls[1].init.headers).get('X-Audli-Upload-Id'), delivery.uploadIdFor(blob));
    assert.equal(new Headers(calls[2].init.headers).get('X-Audli-Upload-Id'), null);
    assert.equal(new Headers(calls[3].init.headers).get('X-Audli-Upload-Id'), null);
    assert.equal(delivery.takeDeliveries().length, 1);
  });
});

test('a network failure is recorded and still thrown unchanged; the quiet time is measured from the previous request', async () => {
  api.installRequestTrace({ turnId: () => null, kindOf: () => null, record() {} });
  const failure = new TypeError('Load failed');
  await withFetch(url => { if (url.endsWith('/attempts')) throw failure; return new Response('{}'); }, async () => {
    await api.authenticatedFetch('/api/profile');
    await new Promise(resolve => setTimeout(resolve, 40));
    await assert.rejects(api.authenticatedFetch('/api/exercises/a/attempts', { method: 'POST', body: formOf(blobOf()) }), error => error === failure);
  });
  const [record] = delivery.takeDeliveries();
  assert.deepEqual([record.outcome, record.status, record.errname, record.err], ['network', 0, 'TypeError', 'Load failed']);
  assert.ok(record.since_prev_ms >= 35 && record.since_prev_ms < 2000);
});

test('the turn report carries the deliveries and puts them back only when the report itself failed on the server', async () => {
  process.env.NEXT_PUBLIC_AUDLI_LATENCY_REPORTING = '1';
  globalThis.window = {};
  reporting.installTurnReporting();
  const bodies = [];
  let status = 204;
  await withFetch((url, init) => { if (url === '/api/telemetry/turn') { bodies.push(JSON.parse(init.body)); return new Response(null, { status }); } return new Response('{}'); }, async () => {
    delivery.startDelivery('/api/exercises/a/attempts', formOf(blobOf())).fail(new TypeError('Load failed'));
    timing.turnTiming('turn_to_playback', performance.now() - 5);
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(bodies.length, 1); assert.equal(bodies[0].deliveries.length, 1);
    assert.deepEqual(Object.keys(bodies[0]).sort(), ['deliveries', 'stages']);
    assert.deepEqual(delivery.takeDeliveries(), []);                                   // delivered: nothing left to send

    status = 503;
    delivery.startDelivery('/api/exercises/a/attempts', formOf(blobOf())).done(200);
    timing.turnTiming('turn_to_playback', performance.now() - 5);
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(delivery.takeDeliveries().length, 1);                                // a server failure keeps the record for the next report

    status = 422;
    delivery.startDelivery('/api/exercises/a/attempts', formOf(blobOf())).done(200);
    timing.turnTiming('turn_to_playback', performance.now() - 5);
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.deepEqual(delivery.takeDeliveries(), []);                                  // a rejected report is dropped, not retried forever
  });
  timing.installTurnSink(null);
  delete process.env.NEXT_PUBLIC_AUDLI_LATENCY_REPORTING;
});

test('a report with no deliveries is exactly the report it was before', async () => {
  process.env.NEXT_PUBLIC_AUDLI_LATENCY_REPORTING = '1';
  globalThis.window = {};
  reporting.installTurnReporting();
  const bodies = [];
  await withFetch((url, init) => { bodies.push(JSON.parse(init.body)); return new Response(null, { status: 204 }); }, async () => {
    timing.turnTiming('turn_to_playback', performance.now() - 5);
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.deepEqual(Object.keys(bodies[0]), ['stages']);
  timing.installTurnSink(null);
  delete process.env.NEXT_PUBLIC_AUDLI_LATENCY_REPORTING;
});
