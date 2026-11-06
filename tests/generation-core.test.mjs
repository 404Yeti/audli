import test from 'node:test';
import assert from 'node:assert/strict';
import { accentLabel } from '../web/lib/home-core.ts';
import { accentNote, accentOptions, comingAccentLabels, stepStates, noteLines, defaultedFields, lengthChoices, unavailableCopy, canCreate, isLive, DEFAULT_SPEC, STEPS } from '../web/lib/generation-core.ts';

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

test('AUD-110: accents come from the server catalog and nothing unvalidated is selectable', () => {
  const avail = { available: true, reason: null, max_minutes: 2, per_learner_daily: 2, accents: ['american', 'british', 'australian'], coming_accents: ['irish', 'new_zealand'], accent_labels: {} };
  assert.deepEqual(accentOptions(null), ['american', 'british', 'australian']);                 // built-in three until the server answers
  assert.deepEqual(accentOptions(avail), ['american', 'british', 'australian']);
  assert.deepEqual(accentOptions({ ...avail, accents: ['american', 'irish'] }), ['american', 'irish']);   // a newly enabled accent appears with no code change
  assert.deepEqual(comingAccentLabels(avail), ['Irish', 'New Zealand']);
  assert.equal(accentLabel('new_zealand'), 'New Zealand'); assert.equal(accentLabel('south_african'), 'South African'); assert.equal(accentLabel('british'), 'British');
  assert.equal(accentNote('irish').place, 'Irish'); assert.equal(accentNote('american').code.length > 0, true);
  const spec = { topic: 'tea', cefr: 'B1', accent: 'irish', minutes: 2, format: 'dialogue', scenario: '' };
  assert.equal(canCreate(spec, ''), false);                                                      // not offered, so not creatable
  assert.equal(canCreate(spec, '', ['american', 'irish']), true);
  assert.match(noteLines(['accent_unavailable', 'requested_accent:irish'], 2)[0], /Irish English isn’t available for lessons yet, so we picked American/);
  assert.match(noteLines(['accent_unavailable'], 2)[0], /That accent isn’t available yet/);
});
