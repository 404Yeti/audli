import assert from 'node:assert/strict';
import { test } from 'node:test';
import { activeSentence, formatTime, clampMs, nextSpeed, phaseOf, stepIndex, newRequestId, waveformBars, optionState, SPEEDS } from '../web/lib/listening-core.ts';

const cues = [{ index: 0, start_ms: 300, end_ms: 3700 }, { index: 1, start_ms: 4150, end_ms: 7400 }, { index: 2, start_ms: 7900, end_ms: 12500 }];

test('the active sentence is the latest one that has started, and stays through the pause after it', () => {
  assert.equal(activeSentence(cues, 0), null);
  assert.equal(activeSentence(cues, 299), null);
  assert.equal(activeSentence(cues, 300), 0);
  assert.equal(activeSentence(cues, 3700), 0);     // inside the gap between sentences: no flicker back to nothing
  assert.equal(activeSentence(cues, 4149), 0);
  assert.equal(activeSentence(cues, 4150), 1);
  assert.equal(activeSentence(cues, 7900), 2);
  assert.equal(activeSentence(cues, 99_999), 2);
  assert.equal(activeSentence([], 1000), null);
});

test('time formatting and seeking stay inside the recording', () => {
  assert.equal(formatTime(0), '0:00');
  assert.equal(formatTime(79_852), '1:19');
  assert.equal(formatTime(-5), '0:00');
  assert.equal(clampMs(-10, 1000), 0);
  assert.equal(clampMs(5000, 1000), 1000);
  assert.equal(clampMs(500, 1000), 500);
});

test('speed cycles through every option and returns to normal', () => {
  let speed = 1;
  const seen = [];
  for (let i = 0; i < SPEEDS.length; i++) { speed = nextSpeed(speed); seen.push(speed); }
  assert.deepEqual(new Set(seen), new Set(SPEEDS));
  assert.equal(speed, 1);
  assert.equal(nextSpeed(3), 1);                    // an unknown speed falls back to normal speed
});

test('the phase comes from the saved attempt; only "started" is local', () => {
  assert.equal(phaseOf('in_progress', false), 'listen');
  assert.equal(phaseOf('in_progress', true), 'question');
  assert.equal(phaseOf('completed', false), 'result');
  assert.equal(phaseOf('completed', true), 'result');
  assert.deepEqual(['listen', 'question', 'result'].map(stepIndex), [0, 1, 2]);
});

test('request ids are unique, valid for the API pattern, and replaceable in tests', () => {
  const a = newRequestId(), b = newRequestId();
  assert.notEqual(a, b);
  assert.match(a, /^[A-Za-z0-9_-]{8,64}$/);
  assert.equal(newRequestId(() => 'fixed-id-0001'), 'fixed-id-0001');
});

test('the waveform is deterministic, so server and browser render the same bars', () => {
  assert.deepEqual(waveformBars(48), waveformBars(48));
  assert.equal(waveformBars(48).length, 48);
  assert.ok(waveformBars(48).every(h => h >= 28 && h <= 100));
});

test('option states: choosing before feedback, then right/wrong/dimmed after', () => {
  assert.equal(optionState('a', null, null), 'idle');
  assert.equal(optionState('a', 'a', null), 'selected');
  assert.equal(optionState('b', 'a', null), 'idle');
  const wrong = { selected_option_id: 'a', is_correct: false, correct_option_id: 'b', explanation: 'x' };
  assert.deepEqual(['a', 'b', 'c'].map(id => optionState(id, 'a', wrong)), ['incorrect', 'correct', 'dimmed']);
  const right = { selected_option_id: 'b', is_correct: true, correct_option_id: 'b', explanation: 'x' };
  assert.deepEqual(['a', 'b', 'c'].map(id => optionState(id, 'b', right)), ['dimmed', 'correct', 'dimmed']);
});

// ---- Milestone 02: the four-exercise journey ---------------------------------------------------------------------------------------
import { isDecided, firstUndecided, hasPendingAssessment, pollDelayMs, moveItem, outcomeOf, scoreLine, pickRecorderType, cleanGap, splitPrompt, clock } from '../web/lib/listening-core.ts';

const ex = (kind, extra = {}) => ({ id: kind, position: 1, kind, prompt: 'q', options: [], feedback: null, fill: null, sequence: null, spoken: null, ...extra });
const spoken = status => ({ status, retryable: false, transcript: null, insufficient: false, points: null, covered: null, partly: null, total_points: null, failure_code: null });

test('a question is decided once answered, and a recording still being received is not', () => {
  assert.equal(isDecided(ex('multiple_choice')), false);
  assert.equal(isDecided(ex('multiple_choice', { feedback: { is_correct: true } })), true);
  assert.equal(isDecided(ex('fill_blank', { fill: { is_correct: false } })), true);
  assert.equal(isDecided(ex('sequencing', { sequence: { is_correct: false } })), true);
  assert.equal(isDecided(ex('spoken', { spoken: spoken('receiving') })), false);
  for (const status of ['pending', 'assessed', 'failed', 'skipped']) assert.equal(isDecided(ex('spoken', { spoken: spoken(status) })), true, status);
});

test('the learner continues past a pending spoken answer to the next unanswered question', () => {
  const list = [ex('multiple_choice', { feedback: { is_correct: true } }), ex('spoken', { spoken: spoken('pending') }), ex('fill_blank'), ex('sequencing')];
  assert.equal(firstUndecided(list).kind, 'fill_blank');
  assert.equal(hasPendingAssessment(list), true);
  assert.equal(hasPendingAssessment([ex('spoken', { spoken: spoken('assessed') })]), false);
  assert.equal(firstUndecided(list.map(e => e.kind === 'spoken' ? e : { ...e, feedback: { is_correct: true } })), undefined);
});

test('polling backs off and stays bounded', () => {
  assert.deepEqual([0, 1, 2, 5, 50].map(pollDelayMs), [3000, 4000, 5000, 8000, 8000]);
});

test('moving an item swaps neighbours and never leaves the list', () => {
  const order = ['a', 'b', 'c'];
  assert.deepEqual(moveItem(order, 0, 1), ['b', 'a', 'c']);
  assert.deepEqual(moveItem(order, 2, -1), ['a', 'c', 'b']);
  assert.equal(moveItem(order, 0, -1), order);
  assert.equal(moveItem(order, 2, 1), order);
  assert.equal(moveItem(order, 9, 1), order);
  assert.deepEqual(order, ['a', 'b', 'c']);       // never mutates its input
});

test('the debrief reports each state as it really is and counts only scored questions', () => {
  assert.equal(outcomeOf(ex('multiple_choice', { feedback: { is_correct: false } })), 'incorrect');
  assert.equal(outcomeOf(ex('fill_blank', { fill: { is_correct: true } })), 'correct');
  assert.equal(outcomeOf(ex('spoken', { spoken: spoken('pending') })), 'pending');
  assert.equal(outcomeOf(ex('spoken', { spoken: spoken('skipped') })), 'skipped');
  assert.equal(outcomeOf(ex('spoken', { spoken: spoken('failed') })), 'failed');
  assert.equal(outcomeOf(ex('spoken', { spoken: spoken('receiving') })), 'unanswered');
  const view = exercises => ({ exercises });
  const right = { is_correct: true }, wrong = { is_correct: false };
  assert.equal(scoreLine(view([ex('multiple_choice', { feedback: right }), ex('spoken', { spoken: spoken('pending') }), ex('fill_blank', { fill: wrong }), ex('sequencing', { sequence: right })])), '2 of 3 correct');
  assert.equal(scoreLine(view([ex('multiple_choice', { feedback: right }), ex('fill_blank'), ex('sequencing')])), '1 of 1 correct so far');
});

test('recorder, gap and prompt helpers', () => {
  assert.equal(pickRecorderType(t => t === 'audio/mp4'), 'audio/mp4');
  assert.equal(pickRecorderType(() => false), undefined);
  assert.equal(pickRecorderType(() => true), 'audio/webm;codecs=opus');
  assert.equal(cleanGap('  fresh   herbs \n'), 'fresh herbs');
  assert.equal(cleanGap('x'.repeat(200)).length, 80);
  assert.deepEqual(splitPrompt('It comes with fresh ___.'), ['It comes with fresh ', '.']);
  assert.deepEqual(splitPrompt('No gap'), ['No gap', '']);
  assert.equal(clock(0), '0:00'); assert.equal(clock(65.9), '1:05');
});

// ---- Milestone 03: library, saved sessions, progress -------------------------------------------------------------------------------
import { lessonAction, statusLine, whenLabel, lengthLabel, progressLine, resumePosition } from '../web/lib/listening-core.ts';

const item = extra => ({ lesson_id: 'l', slug: 's', title: 'T', topic: 't', cefr: 'B1', listen_for: 'x', accents: [], duration_ms: 79_000, status: 'new',
  attempt_id: null, answered: 0, total: 4, correct: null, scored: null, spoken_status: null, last_activity_at: null, completed_count: 0, ...extra });

test('a lesson card says what is true and offers the right action, never a silent restart', () => {
  assert.deepEqual([lessonAction(item()), lessonAction(item({ status: 'in_progress' })), lessonAction(item({ status: 'completed' }))], ['Start', 'Continue', 'See results']);
  assert.equal(statusLine(item()), 'New');
  assert.equal(statusLine(item({ status: 'in_progress', answered: 2 })), 'In progress · 2 of 4 answered');
  assert.equal(statusLine(item({ status: 'completed', correct: 2, scored: 3 })), 'Finished · 2 of 3 correct');
  assert.equal(statusLine(item({ status: 'completed', correct: null, scored: null })), 'Finished');
});

test('dates are plain and lengths are rounded honestly', () => {
  const noon = new Date(2026, 10, 4, 12).getTime();
  assert.equal(whenLabel(new Date(2026, 10, 4, 1).toISOString(), noon), 'Today');
  assert.equal(whenLabel(new Date(2026, 10, 3, 23).toISOString(), noon), 'Yesterday');
  assert.match(whenLabel(new Date(2026, 9, 20).toISOString(), noon), /20/);
  assert.equal(whenLabel('not a date', noon), '');
  assert.equal(lengthLabel(79_000), 'About 1 min'); assert.equal(lengthLabel(56_000), 'About 1 min'); assert.equal(lengthLabel(20_000), 'Under 1 min');
});

test('progress only states what happened', () => {
  const base = { lessons_available: 2, lessons_completed: 0, attempts_completed: 0, questions_correct: 0, questions_scored: 0, recent: [] };
  assert.equal(progressLine(base), 'Nothing finished yet. Pick a lesson to begin.');
  assert.equal(progressLine({ ...base, lessons_completed: 1 }), '1 of 2 lessons finished');
  assert.equal(progressLine({ ...base, lessons_available: 1, lessons_completed: 1 }), '1 of 1 lesson finished');
});

test('a returning learner skips the intro only when an answer is already saved, and resumes at the first open question', () => {
  const list = [ex('multiple_choice', { position: 1, feedback: { is_correct: true } }), ex('spoken', { position: 2 }), ex('fill_blank', { position: 3 })];
  assert.deepEqual(resumePosition(list), { started: true, next: 2 });
  assert.deepEqual(resumePosition([ex('multiple_choice', { position: 1 })]), { started: false, next: 1 });
  assert.deepEqual(resumePosition([ex('multiple_choice', { position: 1, feedback: { is_correct: false } })]), { started: true, next: null });
});

import { PROVIDER_DISCLOSURE } from '../web/lib/listening-core.ts';
test('the recording screen discloses the external provider and what happens to the audio', () => {
  assert.match(PROVIDER_DISCLOSURE, /external AI provider/);
  assert.match(PROVIDER_DISCLOSURE, /OpenAI/);
  assert.match(PROVIDER_DISCLOSURE, /temporary audio is deleted as soon as it has been transcribed/);
  assert.ok(PROVIDER_DISCLOSURE.length < 260);          // concise enough to read before recording
});

import { listeningEnabled } from '../web/lib/listening-flag.ts';
test('the 2.0 pages are off unless the flag is exactly "true"', () => {
  for (const value of [undefined, '', 'false', 'TRUE', '1', 'yes', ' true']) assert.equal(listeningEnabled({ NEXT_PUBLIC_AUDLI_LISTENING: value }), false, String(value));
  assert.equal(listeningEnabled({ NEXT_PUBLIC_AUDLI_LISTENING: 'true' }), true);
  assert.equal(listeningEnabled({}), false);
});

import { cutoverEnabled, cutoverRedirects, cutoverBeforeFiles, cutoverAfterFiles } from '../web/lib/cutover.ts';
test('the landing cutover needs BOTH flags and is otherwise completely inert', () => {
  const on = { NEXT_PUBLIC_AUDLI_LISTENING: 'true', NEXT_PUBLIC_AUDLI_LISTENING_HOME: 'true' };
  assert.equal(cutoverEnabled(on), true);
  assert.deepEqual(cutoverRedirects(on), []);                                                   // "/" is never redirected: it serves the 2.0 home
  assert.deepEqual(cutoverBeforeFiles(on), [{ source: '/', destination: '/home' }]);
  assert.deepEqual(cutoverAfterFiles(on), [{ source: '/classic', destination: '/' }]);
  for (const env of [{}, { NEXT_PUBLIC_AUDLI_LISTENING: 'true' }, { NEXT_PUBLIC_AUDLI_LISTENING_HOME: 'true' }, { ...on, NEXT_PUBLIC_AUDLI_LISTENING_HOME: 'yes' }, { ...on, NEXT_PUBLIC_AUDLI_LISTENING: '1' }]) {
    assert.equal(cutoverEnabled(env), false);
    assert.deepEqual([cutoverRedirects(env), cutoverBeforeFiles(env), cutoverAfterFiles(env)], [[], [], []]);          // never move 1.x unless fully enabled
  }
});

import { dailyView, dailyButton, dailyHref, untilNext, streakLine } from '../web/lib/daily-core.ts';

const lesson = (status, extra = {}) => ({ lesson_id: 'L1', status, title: 't', cefr: 'A2', accents: ['american'], duration_ms: 90_000, ...extra });

test('the daily card shows start, resume, completed or unavailable and never a lesson on an unavailable day', () => {
  assert.equal(dailyView({ status: 'unavailable', lesson: null, completed_today: false }), 'unavailable');
  assert.equal(dailyView({ status: 'unavailable', lesson: lesson('new'), completed_today: false }), 'unavailable');
  assert.equal(dailyView({ status: 'ready', lesson: lesson('new'), completed_today: false }), 'start');
  assert.equal(dailyView({ status: 'ready', lesson: lesson('in_progress'), completed_today: false }), 'resume');
  assert.equal(dailyView({ status: 'ready', lesson: lesson('completed'), completed_today: true }), 'completed');
  assert.deepEqual(['start', 'resume', 'completed'].map(dailyButton), ['Start challenge', 'Resume challenge', 'Review results']);
  assert.equal(dailyHref({ lesson: lesson('new') }), '/listening/L1');
  assert.equal(dailyHref({ lesson: null }), null);
});

test('the countdown and streak lines are plain and never negative', () => {
  const now = Date.parse('2026-11-10T10:00:00Z');
  assert.equal(untilNext('2026-11-10T13:20:00Z', now), '3 h 20 min');
  assert.equal(untilNext('2026-11-10T13:00:00Z', now), '3 h');
  assert.equal(untilNext('2026-11-10T10:25:00Z', now), '25 min');
  assert.equal(untilNext('2026-11-10T09:00:00Z', now), '0 min');
  assert.equal(streakLine({ current: 0, longest: 0, days_completed: 0 }), 'Finish today’s challenge to start a streak');
  assert.equal(streakLine({ current: 3, longest: 3, days_completed: 3 }), '3-day streak');
  assert.equal(streakLine({ current: 2, longest: 5, days_completed: 9 }), '2-day streak · best 5');
  assert.equal(streakLine({ current: 0, longest: 4, days_completed: 4 }), 'Best streak 4 days');
});
