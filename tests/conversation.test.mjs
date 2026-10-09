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

test('prepared audio single-flights preparation and playback, including a slow body', async () => {
  const { PreparedAudio }=await import('../web/lib/prepared-audio.ts');
  const { LessonLifetime }=await import('../web/lib/api.ts');
  const lifetime=new LessonLifetime(),operation=lifetime.begin();
  let release,requests=0,plays=0,validations=0;
  const prepared=new PreparedAudio(operation,()=>{requests++;return new Promise(resolve=>{release=resolve;});});
  const play=async body=>{plays++;assert.equal(await body.text(),'coaching');};
  const validate=async()=>{validations++;};
  const a=prepared.play(play,validate),b=prepared.play(play,validate);
  assert.equal(a,b);assert.equal(requests,1);assert.equal(plays,0);
  release(new Blob(['coaching']));await Promise.all([a,b]);
  await prepared.play(play,validate);assert.equal(plays,1);assert.equal(validations,1);
  lifetime.close();
});

test('failed background preparation is observed immediately and a fresh operation can retry', async () => {
  const { PreparedAudio }=await import('../web/lib/prepared-audio.ts');
  const { LessonLifetime }=await import('../web/lib/api.ts');
  const lifetime=new LessonLifetime(),operation=lifetime.begin();let plays=0;
  const failure=new Error('Speech unavailable');
  const prepared=new PreparedAudio(operation,async()=>{throw failure;});
  await new Promise(resolve=>setTimeout(resolve,0));
  await assert.rejects(prepared.play(async()=>{plays++;},async()=>{}),error=>error===failure);
  assert.equal(plays,0);operation.release();
  const retry=new PreparedAudio(lifetime.begin(),async()=>new Blob(['retry']));
  await retry.play(async()=>{plays++;},async()=>{});assert.equal(plays,1);lifetime.close();
});

test('cancellation and stale checkpoint validation discard prepared bodies without playback', async () => {
  const { PreparedAudio }=await import('../web/lib/prepared-audio.ts');
  const { LessonLifetime }=await import('../web/lib/api.ts');
  let release,plays=0;const lifetime=new LessonLifetime(),operation=lifetime.begin();
  const prepared=new PreparedAudio(operation,()=>new Promise(resolve=>{release=resolve;}));
  lifetime.close();release(new Blob(['late']));await new Promise(resolve=>setTimeout(resolve,0));
  assert.throws(()=>prepared.play(async()=>{plays++;},async()=>{}),error=>error.name==='AbortError');
  const other=new LessonLifetime(),next=other.begin();
  const stale=new PreparedAudio(next,async()=>new Blob(['old checkpoint']));
  await assert.rejects(stale.play(async()=>{plays++;},async()=>{throw new Error('Checkpoint changed');}),/Checkpoint changed/);
  assert.equal(plays,0);other.close();
});
