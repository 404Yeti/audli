import assert from 'node:assert/strict';
import { test } from 'node:test';
import { minutesRemaining, reliableRecognition, turnDetector } from '../web/lib/hands-free.ts';

test('countdown derives from elapsed time, rounds up, and never displays zero', () => {
  assert.equal(minutesRemaining(1000, 1000),10);
  assert.equal(minutesRemaining(1000, 60999),10);
  assert.equal(minutesRemaining(1000, 61000),9);
  assert.equal(minutesRemaining(1000, 601000),1);
  assert.equal(minutesRemaining(1000, 900000),1);
});
test('automatic recognition preserves uncertainty and the configured confidence boundary', () => {
  const value = {text:'Team changed meeting time',confidence:.65,uncertainty:[]};
  assert.equal(reliableRecognition(value),true);
  for (const patch of [{confidence:.649},{confidence:null},{text:' '},{uncertainty:['Uncertain']}]) assert.equal(reliableRecognition({...value,...patch}),false);
  assert.equal(reliableRecognition(value,.7),false);
});
test('turn detector waits for speech and a generous silence gap rather than a single quiet sample', () => {
  const detect = turnDetector(0);
  for (let now=50;now<=500;now+=50) assert.equal(detect(now,.04).end,false);
  assert.equal(detect(2000,0).end,false);
  assert.equal(detect(2300,0).end,true);
});
test('turn detector bounds silence and continuous speech, and ignores brief noise', () => {
  let detect = turnDetector(0);
  assert.equal(detect(14999,0).end,false);
  assert.equal(detect(15000,0).end,true);
  detect = turnDetector(0);
  assert.equal(detect(50,.1).end,false);
  assert.equal(detect(2000,0).end,false);
  assert.equal(detect(15000,0).end,true);
  detect = turnDetector(0);
  assert.equal(detect(119000,.1).end,true);
});

test('explicit End survives same-account relogin but never crosses account boundaries', async () => {
  const { conversationIntent } = await import('../web/lib/conversation-intent.ts');
  const original = globalThis.localStorage;
  const values = new Map();
  globalThis.localStorage = { getItem:key=>values.get(key)??null, setItem:(key,value)=>values.set(key,value), removeItem:key=>values.delete(key) };
  try {
    const a = conversationIntent('account-a:session-1','lesson');
    assert.equal(a.ended(),false);a.end();
    assert.equal(conversationIntent('account-a:session-2','lesson').ended(),true);
    assert.equal(conversationIntent('account-b:session-1','lesson').ended(),false);
    assert.equal(conversationIntent('account-a:session-1','onboarding').ended(),false);
    assert.deepEqual([...values.values()],['true']);
    conversationIntent('account-a:session-2','lesson').start();assert.equal(a.ended(),false);
  } finally { globalThis.localStorage = original; }
});
test('unavailable presentation storage conservatively prevents automatic speech', async () => {
  const { conversationIntent } = await import('../web/lib/conversation-intent.ts');
  const original = globalThis.localStorage;
  globalThis.localStorage = { getItem(){throw Error('Storage denied');},setItem(){throw Error('Storage denied');},removeItem(){throw Error('Storage denied');} };
  try { const intent=conversationIntent('account-a','onboarding');intent.end();assert.equal(intent.ended(),true);intent.start();assert.equal(intent.ended(),true); }
  finally { globalThis.localStorage = original; }
});
test('fixed-stage timing preserves values/errors and emits nothing in production', async () => {
  const { measureTurn } = await import('../web/lib/turn-timing.ts');
  const original=console.debug,environment=process.env.NODE_ENV,logs=[];
  console.debug=(...values)=>logs.push(values);
  try {
    process.env.NODE_ENV='production';assert.equal(await measureTurn('assessment',async()=>42),42);assert.deepEqual(logs,[]);
    process.env.NODE_ENV='development';const abort=new DOMException('Cancelled','AbortError');
    await assert.rejects(measureTurn('upload_transcription',async()=>{throw abort;}),error=>error===abort);
    assert.deepEqual(Object.keys(logs[0][1]).sort(),['elapsedMs','stage']);assert.equal(logs[0][1].stage,'upload_transcription');assert.equal(typeof logs[0][1].elapsedMs,'number');
  } finally { console.debug=original;if(environment===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=environment; }
});
