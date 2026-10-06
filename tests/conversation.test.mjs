import assert from 'node:assert/strict';
import { test } from 'node:test';
import { conversationReducer, canRespond, scoreLabel } from '../web/lib/conversation.ts';

test('server checkpoints resume recognition review with the right response context', () => {
  const initial = { phase: 'LISTENING', awaiting: 'AWAITING_SUMMARY' };
  const resumed = conversationReducer(initial, { type: 'server', phase: 'ASSESSING_FOLLOWUP', hasPending: true });
  assert.deepEqual(resumed, { phase: 'REVIEWING_TRANSCRIPT', awaiting: 'AWAITING_FOLLOWUP' });
  const recording = conversationReducer(resumed, { type: 'phase', phase: 'RECORDING' });
  assert.equal(recording.awaiting, 'AWAITING_FOLLOWUP');
  const question = conversationReducer(recording, { type: 'server', phase: 'AWAITING_FOLLOWUP', hasPending: false });
  assert.equal(question.phase, 'AWAITING_FOLLOWUP');
});

test('recording is unavailable during listening, processing, and completed feedback', () => {
  for (const phase of ['LISTENING', 'ASSESSING', 'ASSESSING_FOLLOWUP', 'TRANSCRIBING', 'GIVING_FEEDBACK', 'READY_FOR_NEXT']) {
    assert.equal(canRespond(phase), false);
  }
  assert.equal(canRespond('AWAITING_SUMMARY'), true);
  assert.equal(canRespond('AWAITING_FOLLOWUP'), true);
});

test('unknown scores are never displayed as zero; a confirmed zero stays zero', () => {
  assert.equal(scoreLabel(null), 'Not enough evidence');
  assert.equal(scoreLabel(undefined), 'Not enough evidence');
  assert.equal(scoreLabel(0), '0%');
  assert.equal(scoreLabel(1), '100%');
});

test('next exercise resets conversation state', () => {
  assert.deepEqual(conversationReducer({ phase: 'READY_FOR_NEXT', awaiting: 'AWAITING_FOLLOWUP' }, { type: 'reset' }),
    { phase: 'LISTENING', awaiting: 'AWAITING_SUMMARY' });
});
