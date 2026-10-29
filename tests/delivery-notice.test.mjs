import assert from 'node:assert/strict';
import { test, beforeEach } from 'node:test';

const { noticeFor, NOTICE_TEXT } = await import('../web/lib/delivery-notice.ts');
const { markUnsent, clearUnsent, readUnsent, UNSENT_TTL_MS } = await import('../web/lib/unsent-marker.ts');

const memory = () => { const data = new Map(); return { data, getItem: key => data.get(key) ?? null, setItem: (key, value) => { data.set(key, String(value)); }, removeItem: key => { data.delete(key); } }; };
beforeEach(() => { globalThis.sessionStorage = memory(); });

test('the learner-facing wording is fixed (the design document quotes it)', () => {
  assert.equal(NOTICE_TEXT.sending, 'Sending your answer…');
  assert.equal(NOTICE_TEXT.retrying(2, 3), 'Trying to send again (2 of 3)…');
  assert.equal(NOTICE_TEXT.failed, "We couldn't confirm your answer was received. Your recording is available while this page stays open.");
  assert.equal(NOTICE_TEXT.recordAgain, "Okay, let's try that again.");
  assert.equal(NOTICE_TEXT.unsent, "Your last send was interrupted. The recording is no longer available. Resume to check your saved lesson.");
  for (const text of [NOTICE_TEXT.sending, NOTICE_TEXT.failed, NOTICE_TEXT.recordAgain, NOTICE_TEXT.unsent, NOTICE_TEXT.moved, NOTICE_TEXT.retrying(3, 3)])
    assert.ok(text.length < 130 && !/[?]/.test(text), text);                    // short, no questions: nothing here asks the learner for evidence
});

test('a send that is going normally shows nothing; slow, retrying, failed and moved each have a notice; delivered clears it', () => {
  assert.equal(noticeFor({ kind: 'sending', send: 1, of: 3, slow: false }), null);
  assert.deepEqual(noticeFor({ kind: 'sending', send: 1, of: 3, slow: true }), { kind: 'sending', text: NOTICE_TEXT.sending });
  assert.deepEqual(noticeFor({ kind: 'retrying', next: 2, of: 3 }), { kind: 'retrying', text: 'Trying to send again (2 of 3)…' });
  assert.deepEqual(noticeFor({ kind: 'failed', reason: 'timeout', sends: 3 }), { kind: 'failed', text: NOTICE_TEXT.failed });
  assert.deepEqual(noticeFor({ kind: 'moved' }), { kind: 'moved', text: NOTICE_TEXT.moved });
  assert.equal(noticeFor({ kind: 'delivered', via: 'sent' }), null);
  assert.equal(noticeFor({ kind: 'delivered', via: 'adopted' }), null);
});

test('the unsent marker round-trips, is per account, expires, and holds no audio or text', () => {
  markUnsent('acct-a', 'checkin', 'lesson-1', 1_000_000);
  assert.deepEqual(readUnsent('acct-a', 1_000_500), { kind: 'checkin', id: 'lesson-1', at: 1_000_000 });
  assert.equal(readUnsent('acct-b', 1_000_500), null);
  assert.equal(readUnsent('acct-a', 1_000_000 + UNSENT_TTL_MS + 1), null);          // too old: forgotten
  assert.equal(sessionStorage.getItem('audli-unsent:acct-a'), null);                 // and cleaned up
  markUnsent('acct-a', 'exercise', 'ex-1', 5);
  assert.deepEqual(Object.keys(JSON.parse(sessionStorage.getItem('audli-unsent:acct-a'))).sort(), ['at', 'id', 'kind']);
  clearUnsent('acct-a'); assert.equal(readUnsent('acct-a', 6), null);
});

test('a corrupt, future-dated or hostile marker is ignored and removed; a storage that throws never matters', () => {
  for (const raw of ['{', '[]', '"x"', 'null', '{"kind":"exercise","id":3,"at":1}', '{"kind":"other","id":"x","at":1}', '{"kind":"exercise","id":"x","at":"1"}', '{"kind":"exercise","id":"x","at":99999999999999}']) {
    sessionStorage.setItem('audli-unsent:acct-a', raw);
    assert.equal(readUnsent('acct-a', 1000), null, raw);
    assert.equal(sessionStorage.getItem('audli-unsent:acct-a'), null, raw);
  }
  globalThis.sessionStorage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); }, removeItem() { throw new Error('denied'); } };
  markUnsent('a', 'checkin', 'x'); clearUnsent('a'); assert.equal(readUnsent('a'), null);
});

test('processing has polite text without requesting another recording or audio',()=>{
  assert.deepEqual(noticeFor({kind:'processing'}),{kind:'sending',text:'Your answer is processing…'});
});
