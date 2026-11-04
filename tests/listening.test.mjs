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
