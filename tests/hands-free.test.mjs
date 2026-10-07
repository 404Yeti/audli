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
