import assert from 'node:assert/strict';
import { successState, pickNext, successPath, attemptParam, SPOKEN_SENTENCE } from '../web/lib/success-core.ts';

const ex = (id, kind, over = {}) => ({ id, position: 1, kind, prompt: 'p', options: [], feedback: null, fill: null, sequence: null, spoken: null, ...over });
const mc = (ok) => ex('a', 'multiple_choice', { feedback: { selected_option_id: 'x', is_correct: ok, correct_option_id: 'y', explanation: '' } });
const fill = (ok) => ex('b', 'fill_blank', { fill: { answer: 'x', is_correct: ok, accepted: 'y', explanation: '' } });
const seq = (ok) => ex('d', 'sequencing', { sequence: { order: [], is_correct: ok, correct_order: [], positions_correct: 0, explanation: '' } });
const spoken = (status) => ex('c', 'spoken', { spoken: { status, retryable: false, transcript: 'secret words', insufficient: false, points: null, covered: 1, partly: 0, total_points: 3, failure_code: null } });
const view = (status, exercises) => ({ attempt: { id: 'att-12345678', status, total: exercises.length, scored: 3, correct: null, created_at: '', completed_at: null },
  lesson: { lesson_id: 'les-1', version_id: 'v', version_no: 1, title: 'Ordering food', topic: 't', cefr: 'B1', listen_for: '', speakers: [], sentences: [] }, audio: { id: 'au', accent: 'american', duration_ms: 1, cues: [] }, exercises });

// complete: counts only deterministic questions; spoken is words, never a number
let s = successState(view('completed', [mc(true), spoken('assessed'), fill(false), seq(true)]), { lessons_available: 5, lessons_completed: 2, attempts_completed: 3, questions_correct: 0, questions_scored: 0, recent: [] });
assert.equal(s.kind, 'complete');
assert.deepEqual([s.summary.correct, s.summary.scored, s.summary.mistakes, s.summary.spoken], [2, 3, 1, 'assessed']);
assert.deepEqual(s.summary.practised, ['Multiple choice', 'Spoken answer', 'Fill in the blank', 'Put in order']);
assert.equal(s.summary.lessonsFinished, 2);
assert.ok(!JSON.stringify(s).includes('secret words'));                 // the learner's transcript is never copied into this summary
assert.ok(!('score' in s.summary) && !('level' in s.summary));          // no invented proficiency measure

// skipped / failed / pending spoken answers are not "practised" and never become a zero
for (const status of ['skipped', 'failed', 'pending']) {
  s = successState(view('completed', [mc(true), spoken(status), fill(true), seq(true)]), null);
  assert.equal(s.summary.spoken, status); assert.ok(!s.summary.practised.includes('Spoken answer'));
  assert.equal(s.summary.correct, 3); assert.equal(s.summary.lessonsFinished, null);
  assert.ok(SPOKEN_SENTENCE[status]);
}
// an unfinished attempt is never shown as success
s = successState(view('in_progress', [mc(true), spoken('pending')]), null);
assert.deepEqual(s, { kind: 'incomplete', answered: 2, total: 2 });
s = successState(view('in_progress', [ex('z', 'multiple_choice'), mc(true)]), null);
assert.equal(s.answered, 1);

// up next
const item = (id, status) => ({ lesson_id: id, status });
assert.equal(pickNext([item('les-1', 'completed'), item('b', 'new'), item('c', 'in_progress')], 'les-1').lesson_id, 'c');
assert.equal(pickNext([item('les-1', 'in_progress'), item('b', 'new')], 'les-1').lesson_id, 'b');
assert.equal(pickNext([item('les-1', 'completed'), item('b', 'completed')], 'les-1'), null);
assert.equal(pickNext([], 'les-1'), null);

// the address
assert.equal(successPath('a b', 'x/y'), '/listening/a%20b/success?attempt=x%2Fy');
assert.equal(attemptParam('abcd-1234-efgh'), 'abcd-1234-efgh');
for (const bad of [null, undefined, '', '../../x', 'a'.repeat(65), 'short', '<script>alert(1)</script>']) assert.equal(attemptParam(bad), null);
console.log('success tests passed');
