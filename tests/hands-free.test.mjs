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

test('meaningful latency excludes acknowledgment/retry and resets on cancellation or a new turn', async () => {
  const { speechLatency } = await import('../web/lib/turn-timing.ts');
  let now=1000; const timing=speechLatency(()=>now);
  assert.equal(timing.playback('meaningful'),null);
  timing.begin(100); assert.equal(timing.playback('acknowledgment').elapsedMs,900);
  now=1300; assert.equal(timing.playback('retry').elapsedMs,1200);
  now=2200; assert.deepEqual(timing.playback('meaningful'),{kind:'meaningful',elapsedMs:2100,boundary:'last_detected_voice'});
  assert.equal(timing.playback('meaningful'),null); // Duplicate playing/replay.
  timing.begin(2000);timing.reset();assert.equal(timing.playback('meaningful'),null);
  timing.begin(2100);timing.begin(2150);assert.equal(timing.playback('meaningful').elapsedMs,50);
});

test('first audio byte is distinct from full delivery; streaming failure remains a failure', async () => {
  const { measuredAudioBody } = await import('../web/lib/turn-timing.ts');
  const original=console.debug,environment=process.env.NODE_ENV,logs=[];
  console.debug=(...values)=>logs.push(values);
  try {
    process.env.NODE_ENV='development';
    let controller;
    const response=new Response(new ReadableStream({start(value){controller=value;}}),{headers:{'Content-Type':'audio/wav'}});
    const task=measuredAudioBody(response);
    controller.enqueue(new Uint8Array([1,2]));await new Promise(resolve=>setTimeout(resolve,0));
    assert.deepEqual(logs.map(value=>value[1].stage),['first_audio_byte']);
    controller.enqueue(new Uint8Array([3]));controller.close();
    const blob=await task;assert.equal(blob.type,'audio/wav');assert.deepEqual([...new Uint8Array(await blob.arrayBuffer())],[1,2,3]);
    assert.deepEqual(logs.map(value=>value[1].stage),['first_audio_byte','audio_body_ready']);
    logs.length=0;
    await measuredAudioBody(new Response(null));assert.deepEqual(logs,[]);
    const abort=new DOMException('Cancelled','AbortError');
    await assert.rejects(measuredAudioBody(new Response(new ReadableStream({start(c){c.error(abort);}}))),error=>error===abort);
    assert.deepEqual(logs,[]);
  } finally { console.debug=original;if(environment===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=environment; }
});

test('turn detection preserves a 1.5-second mid-answer pause before continuation', () => {
  const detect=turnDetector(0);
  for(let now=50;now<=500;now+=50)assert.equal(detect(now,.04).end,false);
  for(let now=550;now<=2000;now+=50)assert.equal(detect(now,0).end,false);
  // At 2000ms a 1200ms silence threshold would already end this answer:
  // elapsed >= 2000 and silence since last voice == 1500ms.
  // The current 1800ms threshold lets the learner finish the thought.
  for(let now=2050;now<=2500;now+=50)assert.equal(detect(now,.04).end,false);
  for(let now=2550;now<4300;now+=50)assert.equal(detect(now,0).end,false);
  assert.equal(detect(4300,0).end,true);
});
