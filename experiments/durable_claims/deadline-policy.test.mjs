import assert from 'node:assert/strict';
import { test } from 'node:test';
import { deliver, DeliveryFailed } from '../../web/lib/upload-sender.ts';
import { fakeEnv, POLICY } from '../../tests/upload-fakes.mjs';

test('50-second delivery deadline never grants a 60-second claim takeover or extra sends',async()=>{
  const clock=fakeEnv();
  const recording=new Blob(['synthetic local recording']);
  const retained=recording;
  let sends=0,error;
  const result=deliver({policy:POLICY,bytes:2_000_000,env:clock.env,
    attempt:()=>{sends++;return new Promise(()=>{});}});
  result.catch(e=>error=e);
  await clock.advance(50_000);
  assert.ok(error instanceof DeliveryFailed);
  assert.equal(error.reason,'deadline');
  assert.ok(sends<=3);
  assert.equal(recording,retained);
  const final=sends;
  await clock.advance(10_001);
  assert.equal(sends,final);
  assert.equal(clock.timers,0);
});
