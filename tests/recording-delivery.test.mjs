import assert from 'node:assert/strict';
import { test, beforeEach } from 'node:test';
import { readFileSync } from 'node:fs';
import { deliverCheckin, deliverExercise, uploadPolicy, currentPolicy } from '../web/lib/recording-delivery.ts';
import { LessonLifetime, setAccessTokenProvider, installRequestTrace } from '../web/lib/api.ts';
import { fakeEnv, fakeOperation, checkinServer } from './upload-fakes.mjs';
const defaults = JSON.parse(readFileSync(new URL('./fixtures/upload_policy_cases.json',import.meta.url))).defaults;
let enabled;
beforeEach(() => {
  enabled = true; uploadPolicy.reset(); installRequestTrace(null); setAccessTokenProvider(null);
  globalThis.fetch = async url => {
    assert.equal(url, '/api/upload-policy');
    return Response.json(enabled ? { enabled: true, ...defaults } : { enabled: false });
  };
});
const audio = () => new Blob([new Uint8Array(132000)], { type:'audio/webm' });
async function run(clock, promise, ms=60000) {
  let result,error; promise.then(v=>result=v,e=>error=e); await clock.advance(ms); return {result,error};
}
test('disabled policy retains legacy one-send behavior', async () => {
  enabled=false; let sends=0;
  const operation=fakeOperation({api:async()=>{sends++;throw new TypeError('Load failed');}});
  await assert.rejects(deliverExercise(operation,'e',audio(),()=>{}),/couldn't be sent/);
  assert.equal(sends,1);
});
test('a lost check-in response stops, and Send again never overwrites ambiguous pending', async () => {
  const c=fakeEnv(), server=checkinServer(), initial=server.checkpoint(), blob=audio(); let sends=0,gets=0;
  const operation=fakeOperation({apiWith:async(url,init)=>{
    if (!init) { gets++; return server.checkpoint(); }
    sends++; server.upload(Number(init.body.get('revision'))); throw new TypeError('Load failed');
  }});
  let r=await run(c,deliverCheckin(operation,initial,blob,()=>{},null,{env:c.env}));
  assert.equal(r.error.reason,'ambiguous'); assert.equal(sends,1); assert.equal(server.transcriptions,1);
  r=await run(c,deliverCheckin(operation,server.checkpoint(),blob,()=>{},null,{env:c.env}));
  assert.equal(r.error.reason,'ambiguous'); assert.equal(sends,1); assert.equal(server.transcriptions,1); assert.equal(gets,2);
});
test('unchanged check-in resends only original revision, with one accepted STT', async () => {
  const c=fakeEnv(),server=checkinServer();let sends=0; const revisions=[];
  const operation=fakeOperation({apiWith:async(url,init)=>{
    if(!init)return server.checkpoint();
    revisions.push(Number(init.body.get('revision')));
    if(++sends<3)throw new TypeError('Load failed');
    return server.upload(revisions.at(-1));
  }});
  const r=await run(c,deliverCheckin(operation,server.checkpoint(),audio(),()=>{},null,{env:c.env}));
  assert.equal(r.result.via,'sent');assert.deepEqual(revisions,[4,4,4]);assert.equal(server.transcriptions,1);
});
test('failed reconciliation retains audio without an extra send', async () => {
  const c=fakeEnv();let sends=0,discarded=0;
  const operation=fakeOperation({apiWith:async(url,init)=>{if(init)sends++;throw new TypeError('offline');}});
  const r=await run(c,deliverCheckin(operation,checkinServer().checkpoint(),audio(),()=>discarded++,null,{env:c.env}));
  assert.equal(r.error.reason,'ambiguous');assert.equal(sends,1);assert.equal(discarded,0);
});
test('stable exercise ID is sent without telemetry; a lost response replays without a second STT',async()=>{
  const c=fakeEnv(); const ids=[]; const attempts=new Map();let stt=0;
  globalThis.fetch=async(url,init)=>{
    if(url==='/api/upload-policy')return Response.json({enabled:true,...defaults});
    const id=init.headers.get('X-Audli-Upload-Id');ids.push(id);
    if(!attempts.has(id)){stt++;attempts.set(id,{id:'attempt',transcription:{text:'meaning'}});throw new TypeError('Load failed');}
    return Response.json(attempts.get(id));
  };
  const lifetime=new LessonLifetime(),operation=lifetime.begin();
  const r=await run(c,deliverExercise(operation,'e',audio(),()=>{},{env:c.env}));
  assert.equal(r.result.id,'attempt'); assert.match(ids[0],/^[a-f0-9]{16}$/);assert.deepEqual(ids,[ids[0],ids[0]]);assert.equal(stt,1);lifetime.close();
});
test('cancellation during token acquisition prevents late network submission',async()=>{
  let token; setAccessTokenProvider(()=>new Promise(resolve=>token=resolve));
  const lifetime=new LessonLifetime(),operation=lifetime.begin(),ctl=new AbortController();let posts=0;
  globalThis.fetch=async()=>{posts++;return Response.json({});};
  const p=operation.apiWith('/exercise',{method:'POST'},ctl.signal);p.catch(()=>{});
  await new Promise(resolve=>setImmediate(resolve));ctl.abort();token('test-token');
  await assert.rejects(p,{name:'AbortError'});assert.equal(posts,0);lifetime.close();
});
test('unavailable and malformed policy fail closed',async()=>{
  for(const value of [null,{}, {enabled:true}, {enabled:'true'}]){
    uploadPolicy.reset();globalThis.fetch=async()=>Response.json(value);
    assert.equal((await currentPolicy()).enabled,false);
  }
});


test('a retained check-in cannot be redirected to another lesson at a coincident revision', async () => {
  const c=fakeEnv(), server=checkinServer(), blob=audio(), paths=[];
  const operation=fakeOperation({apiWith:async(path,init)=>{
    paths.push(path);
    if(!init)return server.checkpoint();
    throw new TypeError('offline');
  }});
  await run(c,deliverCheckin(operation,server.checkpoint(),blob,()=>{},null,{env:c.env}));
  paths.length=0;
  const r=await run(c,deliverCheckin(operation,{...server.checkpoint(),id:'different-lesson'},blob,()=>{},null,{env:c.env}));
  assert.equal(r.error.reason,'ambiguous'); assert.deepEqual(paths,[]);
});


test('an existing direct-origin opt-in fails closed instead of retrying without an idempotency header', async () => {
  const previous=process.env.NEXT_PUBLIC_AUDLI_API_ORIGIN;
  let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('must not fetch policy');};
  try {
    process.env.NEXT_PUBLIC_AUDLI_API_ORIGIN='https://example.test';
    assert.equal((await currentPolicy()).enabled,false);assert.equal(requests,0);
  } finally {
    if(previous===undefined)delete process.env.NEXT_PUBLIC_AUDLI_API_ORIGIN;
    else process.env.NEXT_PUBLIC_AUDLI_API_ORIGIN=previous;
  }
});
