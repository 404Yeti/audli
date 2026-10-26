import assert from 'node:assert/strict';
import { test } from 'node:test';
import { decideCheckin } from '../web/lib/checkin-reconcile.ts';
const known = { lessonId: 'lesson-1', revision: 4, phase: 'WELCOME', pending: null };
const server = { id: 'lesson-1', revision: 4, phase: 'WELCOME', status: 'active', pending: null };
test('only the unchanged checkpoint permits resending the original revision', () => {
  assert.deepEqual(decideCheckin(known, server), { kind: 'resend', revision: 4 });
});
for (const change of [
  { revision: 5, pending: { text: 'another tab', confidence: .95 } },
  { revision: 5, pending: null }, { revision: 6 }, { revision: 3 },
  { pending: { text: 'new evidence' } }, { phase: 'REVIEW' },
  { status: 'paused' }, { status: 'completed' }, { id: 'different' },
]) test(`ambiguous or moved checkpoint stops without adoption or overwrite: ${JSON.stringify(change)}`, () => {
  assert.deepEqual(decideCheckin(known, { ...server, ...change }), { kind: 'stop' });
});
test('identical pre-existing evidence permits original revision only; an identical-text commit still stops', () => {
  const pending = { text: 'same', confidence: .9, uncertainty: [] };
  const k = { ...known, pending }, s = { ...server, pending: structuredClone(pending) };
  assert.deepEqual(decideCheckin(k, s), { kind: 'resend', revision: 4 });
  assert.deepEqual(decideCheckin(k, { ...s, revision: 5 }), { kind: 'stop' });
  assert.equal(k.revision, 4);
});
