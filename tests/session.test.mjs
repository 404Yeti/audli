import assert from 'node:assert/strict';
import { test } from 'node:test';
import { shouldAutoRecord, feedbackOutcome, microphonePermission } from '../web/lib/session.ts';

test('only fresh summary and follow-up cues start recording', () => {
  for (const phase of ['AWAITING_SUMMARY','AWAITING_FOLLOWUP']) {
    assert.equal(shouldAutoRecord(phase,'clip:cue',null,false),true);
    assert.equal(shouldAutoRecord(phase,'clip:cue','clip:cue',false),false);
    assert.equal(shouldAutoRecord(phase,'clip:cue',null,true),false);
    assert.equal(shouldAutoRecord(phase,null,null,false),false);
  }
  for (const phase of ['LISTENING','RECORDING','TRANSCRIBING','REVIEWING_RECORDING','REVIEWING_TRANSCRIPT','ASSESSING','ASSESSING_FOLLOWUP','GIVING_FEEDBACK','READY_FOR_NEXT']) {
    assert.equal(shouldAutoRecord(phase,'clip:cue',null,false),false);
  }
});
test('unknown evidence stays distinct; a contradiction is never labeled understood', () => {
  assert.equal(feedbackOutcome(['unknown'],[]),'Gap');
  assert.equal(feedbackOutcome([],['wrong']),'Misunderstood');
  assert.equal(feedbackOutcome(['unknown'],['wrong']),'Misunderstood');
  assert.equal(feedbackOutcome(undefined,[]),'Understood');
});
test('permission preflight immediately releases every track without recording', async () => {
  let stopped = 0;
  await microphonePermission({async getUserMedia(constraints) {
    assert.deepEqual(constraints,{audio:true});
    return {getTracks: () => [{stop(){stopped++;}},{stop(){stopped++;}}]};
  }});
  assert.equal(stopped,2);
});
test('denied and unsupported microphones provide actionable fallback', async () => {
  await assert.rejects(microphonePermission(undefined), /localhost or HTTPS/);
  await assert.rejects(microphonePermission({async getUserMedia(){throw new Error('denied');}}), /spoken audio file/);
});
