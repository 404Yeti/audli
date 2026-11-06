import test from 'node:test';
import assert from 'node:assert/strict';
import { accentLabel } from '../web/lib/home-core.ts';
import { accentsFor, kindOf, onlyElsewhere, specAvailable, specLine, voicesLabel, accentNote, accentOptions, comingAccentLabels, stepStates, noteLines, defaultedFields, lengthChoices, unavailableCopy, canCreate, isLive, DEFAULT_SPEC, STEPS } from '../web/lib/generation-core.ts';

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
  assert.deepEqual(noteLines(['accent_unavailable', 'requested_accent:irish', 'mixed_unavailable'], 2), []);       // explained by the choice panel, never by quietly picking something else
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
  assert.equal(accentNote('new_zealand').code, 'NZ'); assert.equal(accentNote('boston').place, 'Boston'); assert.equal(accentNote('chinese').code, 'CN');
  assert.equal(accentLabel('new_zealand'), 'New Zealand'); assert.equal(accentLabel('south_african'), 'South African'); assert.equal(accentLabel('british'), 'British');
  assert.equal(accentNote('irish').place, 'Irish'); assert.equal(accentNote('american').code.length > 0, true);
  const spec = { topic: 'tea', cefr: 'B1', accent: 'irish', minutes: 2, format: 'dialogue', scenario: '' };
  assert.equal(canCreate(spec, ''), false);                                                      // not offered, so not creatable
  assert.equal(canCreate(spec, '', ['american', 'irish']), true);
});

test('AUD-112: the three listening formats, and only combinations the server can generate', () => {
  const base = { topic: 'a day in Dublin', cefr: 'B1', accent: 'american', minutes: 2, format: 'dialogue', scenario: '', accent2: null };
  const off = { available: true, reason: null, max_minutes: 2, per_learner_daily: 2, accents: ['american', 'british', 'australian'], formats_enabled: false,
    formats: { monologue: ['american', 'british', 'australian'], conversation: ['american', 'british', 'australian'], mixed_accent_conversation: [] } };
  const on = { ...off, formats_enabled: true, formats: { monologue: ['american', 'irish', 'indian'], conversation: ['american', 'british', 'australian'], mixed_accent_conversation: ['american', 'irish', 'indian'] } };
  assert.equal(kindOf(base), 'conversation'); assert.equal(kindOf({ ...base, format: 'story' }), 'monologue'); assert.equal(kindOf({ ...base, accent2: 'irish' }), 'mixed_accent_conversation');
  assert.deepEqual(accentsFor(off, 'mixed_accent_conversation'), []);                         // flag off: nothing mixed is offered
  assert.deepEqual(accentsFor(null, 'monologue'), ['american', 'british', 'australian']);       // before the server answers: today's three
  assert.deepEqual(accentsFor(on, 'monologue'), ['american', 'irish', 'indian']);
  assert.deepEqual(onlyElsewhere(on, 'conversation'), ['irish', 'indian']);                     // Irish: fine for a monologue, not for two Irish speakers
  assert.deepEqual(onlyElsewhere(off, 'monologue'), []);
  assert.equal(specAvailable(base, on), true);
  assert.equal(specAvailable({ ...base, accent: 'irish' }, on), false);                         // two Irish speakers: not available
  assert.equal(specAvailable({ ...base, format: 'story', accent: 'irish' }, on), true);
  assert.equal(specAvailable({ ...base, accent2: 'irish' }, on), true);
  assert.equal(specAvailable({ ...base, accent2: 'irish' }, off), false);
  assert.equal(specAvailable({ ...base, accent: 'irish', accent2: 'irish' }, on), false);
  assert.equal(specAvailable({ ...base, accent2: 'british' }, on), false);                      // british has no mixed-accent opening here
  assert.equal(specLine({ ...base, accent2: 'irish' }), 'B1 · American + Irish · Dialogue · about 2 min');
  assert.equal(voicesLabel({ format: 'story', accent2: null }), '1 voice'); assert.equal(voicesLabel({ format: 'dialogue', accent2: 'irish' }), '2 voices, 2 accents');
  assert.deepEqual(noteLines(['mixed_unavailable', 'requested_accent2:indian', 'accent2_unrecognised'], 2), []);
  assert.match(noteLines(['second_accent_ignored'], 2)[0], /monologue has one speaker/);
});
