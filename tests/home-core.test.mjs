import test from 'node:test';
import assert from 'node:assert/strict';
import { greeting, displayName, initials, continueItem, stepsDone, recommendedNext, applyFilters, topicTiles, NO_FILTERS, levelRows, accentRows, accentLabel, AVAILABLE_ACCENTS } from '../web/lib/home-core.ts';

const item = (over) => ({ lesson_id: 'l', slug: 's', title: 'T', topic: 'Food', cefr: 'B1', listen_for: 'x', accents: ['american'], duration_ms: 60_000, status: 'new', attempt_id: null,
  answered: 0, total: 4, correct: null, scored: null, spoken_status: null, last_activity_at: null, completed_count: 0, ...over });

test('greeting follows the local hour', () => {
  assert.deepEqual([4, 5, 11, 12, 17, 18, 23].map(greeting), ['Good evening', 'Good morning', 'Good morning', 'Good afternoon', 'Good afternoon', 'Good evening', 'Good evening']);
});
test('display name and initials come from the email until a name is set', () => {
  assert.equal(displayName('linh.nguyen@x.com'), 'Linh Nguyen'); assert.equal(displayName(''), 'Learner'); assert.equal(displayName(undefined), 'Learner');
  assert.equal(initials('Linh Nguyen'), 'LN'); assert.equal(initials('Desert'), 'D');
});
test('continue picks the most recently touched unfinished lesson and the stepper never overflows', () => {
  const a = item({ lesson_id: 'a', status: 'in_progress', last_activity_at: '2026-11-01T10:00:00Z' });
  const b = item({ lesson_id: 'b', status: 'in_progress', last_activity_at: '2026-11-02T10:00:00Z' });
  assert.equal(continueItem([a, b, item({ status: 'completed' })]).lesson_id, 'b'); assert.equal(continueItem([item({})]), null);
  assert.deepEqual(stepsDone({ answered: 2, total: 4 }), { done: 3, total: 5 }); assert.deepEqual(stepsDone({ answered: 9, total: 4 }), { done: 5, total: 5 });
});
test('the recommendation is an unseen lesson near the last level finished, with an honest reason', () => {
  const items = [item({ lesson_id: 'a2', cefr: 'A2' }), item({ lesson_id: 'c1', cefr: 'C1' }), item({ lesson_id: 'done', cefr: 'B1', status: 'completed' })];
  const r = recommendedNext(items, [{ attempt_id: '1', lesson_id: 'done', title: 'T', cefr: 'B1', status: 'completed', when: '', correct: 1, scored: 2, spoken_status: null }]);
  assert.equal(r.item.lesson_id, 'a2'); assert.match(r.reason, /haven’t tried/); assert.doesNotMatch(r.reason, /struggl|weak|missed/i);
  assert.equal(recommendedNext([item({ status: 'completed' })], []), null);
});
test('filters combine and topics are counted from the library', () => {
  const items = [item({ lesson_id: '1', title: 'Café', topic: 'Food', cefr: 'B1', accents: ['british'], duration_ms: 120_000 }),
                 item({ lesson_id: '2', title: 'Train', topic: 'Travel', cefr: 'A2', accents: ['american'], duration_ms: 240_000 }),
                 item({ lesson_id: '3', title: 'Hotel', topic: 'Travel', cefr: 'A2', accents: ['australian'], duration_ms: 400_000 })];
  const ids = (f) => applyFilters(items, { ...NO_FILTERS, ...f }).map(i => i.lesson_id);
  assert.deepEqual(ids({}), ['1', '2', '3']); assert.deepEqual(ids({ query: 'caf' }), ['1']); assert.deepEqual(ids({ cefr: 'A2' }), ['2', '3']);
  assert.deepEqual(ids({ accent: 'australian' }), ['3']); assert.deepEqual(ids({ length: 'short' }), ['1']); assert.deepEqual(ids({ length: 'mid' }), ['2']); assert.deepEqual(ids({ length: 'long' }), ['3']);
  assert.deepEqual(ids({ topic: 'Travel', cefr: 'A2', accent: 'american' }), ['2']); assert.deepEqual(ids({ query: 'zzz' }), []);
  assert.deepEqual(topicTiles(items), [{ topic: 'Travel', count: 2 }, { topic: 'Food', count: 1 }]);
});
test('progress rows are facts from the server and only launch accents are selectable', () => {
  const p = { lessons_available: 2, lessons_completed: 1, attempts_completed: 1, questions_correct: 1, questions_scored: 3, recent: [], accent_counts: { british: 1, american: 3 }, level_counts: { B1: 2 } };
  assert.deepEqual(levelRows(p).map(r => r.count), [0, 0, 2, 0, 0, 0]); assert.deepEqual(accentRows(p), [{ accent: 'american', count: 3 }, { accent: 'british', count: 1 }]);
  assert.deepEqual(levelRows(null).map(r => r.count), [0, 0, 0, 0, 0, 0]); assert.deepEqual(accentRows(null), []);
  assert.deepEqual([...AVAILABLE_ACCENTS], ['american', 'british', 'australian']); assert.equal(accentLabel('australian'), 'Australian');
});
