import test from 'node:test';
import assert from 'node:assert/strict';
import { stepStates, noteLines, defaultedFields, lengthChoices, unavailableCopy, canCreate, isLive, DEFAULT_SPEC, STEPS } from '../web/lib/generation-core.ts';

test('building steps follow the phase the server reports and never run ahead of it', () => {
  assert.deepEqual(stepStates('queued'), ['todo', 'todo', 'todo', 'todo', 'todo']);
  assert.deepEqual(stepStates('script'), ['active', 'todo', 'todo', 'todo', 'todo']);
  assert.deepEqual(stepStates('audio'), ['done', 'done', 'active', 'todo', 'todo']);
  assert.deepEqual(stepStates('transcript_sync'), ['done', 'done', 'done', 'active', 'todo']);
  assert.deepEqual(stepStates('exercises'), ['done', 'done', 'done', 'done', 'active']);
  assert.deepEqual(stepStates('ready'), ['done', 'done', 'done', 'done', 'done']); assert.equal(STEPS.length, 5);
  assert.deepEqual(['queued', 'script', 'audio', 'transcript_sync', 'exercises'].map(isLive), [true, true, true, true, true]);
  assert.deepEqual(['ready', 'failed', 'rejected', 'unavailable', 'interpreted'].map(isLive), [false, false, false, false, false]);
});
test('the notes about an interpreted request are said honestly', () => {
  assert.deepEqual(noteLines(['minutes_limited:2', 'defaulted:format'], 2), ['Lessons are up to 2 minutes for now, so we shortened it.']);
  assert.match(noteLines(['accent_unavailable'], 2)[0], /picked American/);
  assert.deepEqual(defaultedFields(['defaulted:cefr', 'minutes_limited:2', 'defaulted:accent']), ['cefr', 'accent']);
  assert.deepEqual(lengthChoices(2), [1, 2]); assert.deepEqual(lengthChoices(10), [1, 2, 3, 4, 5]); assert.deepEqual(lengthChoices(0), [1]);
});
test('every unavailable reason has a clear title, a way forward and no internals', () => {
  for (const reason of ['daily_limit', 'budget', 'credits', 'paused', 'disabled', null]) {
    const copy = unavailableCopy(reason, 2); assert.ok(copy.title && copy.text);
    assert.doesNotMatch(copy.title + copy.text, /credit|dollar|\$|elevenlabs|openai|quota|api/i);
  }
  assert.match(unavailableCopy('daily_limit', 2).text, /2 lessons a day/);
});
test('a lesson can be created from a description or a topic, within the length limit, with an available accent only', () => {
  assert.equal(canCreate(DEFAULT_SPEC, ''), false); assert.equal(canCreate(DEFAULT_SPEC, 'ordering coffee'), true);
  assert.equal(canCreate({ ...DEFAULT_SPEC, topic: 'ordering coffee' }, ''), true); assert.equal(canCreate(DEFAULT_SPEC, 'x'.repeat(281)), false);
  assert.equal(canCreate({ ...DEFAULT_SPEC, topic: 'ordering coffee', accent: 'irish' }, ''), false);
});
