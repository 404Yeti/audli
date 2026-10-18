import test from 'node:test';
import assert from 'node:assert/strict';
import { INITIAL_PHASE, lessonPhase, after, topicPreferencesAllowed } from '../web/lib/lesson-phase.ts';

const SERVER = ['WELCOME', 'WELCOME_ACK', 'REVIEW', 'REVIEW_ACK', 'TRANSITION', 'EXERCISE', 'CLOSING', 'COMPLETED'];
const events = [
  { type: 'topic_setup' }, { type: 'topic_resolved' }, { type: 'home' },
  ...['A', 'B'].flatMap(lessonId => [
    ...SERVER.map(phase => ({ type: 'checkpoint', lessonId, phase })),
    { type: 'restored', lessonId, homeworkResolved: false }, { type: 'restored', lessonId, homeworkResolved: true },
    { type: 'homework_resolved', lessonId }, { type: 'celebrated', lessonId },
  ]),
];
const run = (list, from = INITIAL_PHASE) => list.reduce(lessonPhase, from);
const ORDER = ['HOME', 'TOPIC_SETUP', 'SMALL_TALK', 'HOMEWORK_PREFLIGHT', 'LESSON', 'FEEDBACK', 'HOMEWORK', 'SUCCESS', 'STATS'];

/** Every event sequence up to a bounded length, with the states each one reaches. */
function* walk(depth, state = INITIAL_PHASE, path = []) {
  yield { state, path };
  if (depth === 0) return;
  for (const event of events) yield* walk(depth - 1, lessonPhase(state, event), [...path, event]);
}

test('the intended lifecycle is reachable in order', () => {
  const states = [];
  let state = INITIAL_PHASE;
  for (const event of [{ type: 'topic_setup' }, { type: 'topic_resolved' },
    ...['WELCOME', 'WELCOME_ACK', 'TRANSITION', 'EXERCISE', 'CLOSING', 'COMPLETED'].map(phase => ({ type: 'checkpoint', lessonId: 'A', phase })),
    { type: 'homework_resolved', lessonId: 'A' }, { type: 'celebrated', lessonId: 'A' }, { type: 'home' }]) { state = lessonPhase(state, event); states.push(state.phase); }
  assert.deepEqual([...new Set(states)], ['TOPIC_SETUP', 'HOME', 'SMALL_TALK', 'HOMEWORK_PREFLIGHT', 'LESSON', 'FEEDBACK', 'HOMEWORK', 'SUCCESS', 'STATS']);
});

test('preferences are allowed only in TOPIC_SETUP, which is reachable only from HOME', () => {
  for (const { state, path } of walk(4)) {
    if (!topicPreferencesAllowed(state)) continue;
    assert.equal(state.lessonId, null, JSON.stringify(path));
    assert.ok(path.some(event => event.type === 'topic_setup'), JSON.stringify(path));
  }
  // Entry is only ever from HOME: from every other phase the event changes nothing.
  for (const phase of ORDER.filter(phase => phase !== 'HOME')) {
    const state = { phase, lessonId: phase === 'TOPIC_SETUP' ? null : 'A' };
    assert.equal(lessonPhase(state, { type: 'topic_setup' }), state, phase);
  }
});

test('after FEEDBACK a lesson never returns to setup, whatever arrives, until the learner goes home', () => {
  for (const { state, path } of walk(4)) {
    if (!after(state, 'FEEDBACK') || state.lessonId === null) continue;
    for (const event of events.filter(e => e.type !== 'home')) {
      const next = lessonPhase(state, event);
      if (next.lessonId === state.lessonId) assert.ok(ORDER.indexOf(next.phase) >= ORDER.indexOf(state.phase), JSON.stringify([...path, event]));
      assert.notEqual(next.phase, 'TOPIC_SETUP', JSON.stringify([...path, event]));
    }
  }
});

test('transitions are idempotent: a duplicate or delayed event returns the same state object', () => {
  for (const { state } of walk(3)) for (const event of events) {
    const once = lessonPhase(state, event);
    assert.equal(lessonPhase(once, event), once, JSON.stringify([state, event]));
  }
});

test('stale checkpoints and completions from an earlier lesson cannot disturb a later one', () => {
  const wrapup = run([{ type: 'checkpoint', lessonId: 'B', phase: 'COMPLETED' }, { type: 'homework_resolved', lessonId: 'B' }, { type: 'celebrated', lessonId: 'B' }]);
  assert.deepEqual(wrapup, { phase: 'STATS', lessonId: 'B' });
  for (const phase of SERVER) assert.equal(lessonPhase(wrapup, { type: 'checkpoint', lessonId: 'A', phase }), wrapup);
  assert.equal(lessonPhase(wrapup, { type: 'celebrated', lessonId: 'B' }), wrapup);
  assert.equal(lessonPhase(wrapup, { type: 'homework_resolved', lessonId: 'B' }), wrapup);
  assert.equal(lessonPhase(wrapup, { type: 'checkpoint', lessonId: 'B', phase: 'COMPLETED' }), wrapup);
});

test('the celebration cannot start until the new homework decision is resolved', () => {
  const homework = run([{ type: 'checkpoint', lessonId: 'A', phase: 'COMPLETED' }]);
  assert.equal(homework.phase, 'HOMEWORK');
  assert.equal(lessonPhase(homework, { type: 'celebrated', lessonId: 'A' }), homework);
  assert.equal(lessonPhase(homework, { type: 'homework_resolved', lessonId: 'B' }), homework);
  assert.equal(lessonPhase(homework, { type: 'homework_resolved', lessonId: 'A' }).phase, 'SUCCESS');
  for (const { state, path } of walk(4)) if (state.phase === 'SUCCESS' || state.phase === 'STATS')
    assert.ok(path.some(event => event.type === 'homework_resolved' || event.type === 'restored'), JSON.stringify(path));
});

test('restoration resumes the homework query or the final card and never replays the celebration', () => {
  assert.deepEqual(run([{ type: 'restored', lessonId: 'A', homeworkResolved: false }]), { phase: 'HOMEWORK', lessonId: 'A' });
  assert.deepEqual(run([{ type: 'restored', lessonId: 'A', homeworkResolved: true }]), { phase: 'STATS', lessonId: 'A' });
  const celebrating = run([{ type: 'checkpoint', lessonId: 'A', phase: 'COMPLETED' }, { type: 'homework_resolved', lessonId: 'A' }]);
  assert.equal(lessonPhase(celebrating, { type: 'restored', lessonId: 'A', homeworkResolved: false }), celebrating);
});

test('only an explicit home event leaves the final card', () => {
  const finished = run([{ type: 'restored', lessonId: 'A', homeworkResolved: true }]);
  for (const event of events.filter(e => e.type !== 'home')) assert.equal(lessonPhase(finished, event).phase === 'HOME', false);
  assert.deepEqual(lessonPhase(finished, { type: 'home' }), INITIAL_PHASE);
});
