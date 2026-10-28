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


test('manual Send again after the kill switch keeps the prior exercise identity with telemetry off', async () => {
  const c=fakeEnv(), blob=audio(), ids=[], attempts=new Map();let stt=0;
  globalThis.fetch=async(url,init)=>{
    if(url==='/api/upload-policy')return Response.json(enabled ? {enabled:true,...defaults,max_sends:1} : {enabled:false});
    const id=init.headers.get('X-Audli-Upload-Id');ids.push(id);
    if(!attempts.has(id)){stt++;attempts.set(id,{id:'first',transcription:{text:'meaning'}});throw new TypeError('lost response');}
    return Response.json(attempts.get(id));
  };
  const lifetime=new LessonLifetime(),operation=lifetime.begin();
  const first=await run(c,deliverExercise(operation,'e',blob,()=>{},{env:c.env}));
  assert.equal(first.error.name,'DeliveryFailed');
  enabled=false;uploadPolicy.reset();
  const second=await run(c,deliverExercise(operation,'e',blob,()=>{},{env:c.env}));
  assert.equal(second.result.id,'first');assert.match(ids[0],/^[a-f0-9]{16}$/);
  assert.deepEqual(ids,[ids[0],ids[0]]);assert.equal(stt,1);lifetime.close();
});

test('a fresh recording first sent while disabled keeps the legacy no-telemetry header behavior', async () => {
  enabled=false;let posts=0;
  globalThis.fetch=async(url,init)=>{
    if(url==='/api/upload-policy')return Response.json({enabled:false});
    posts++;assert.equal(init.headers.has('X-Audli-Upload-Id'),false);
    return Response.json({id:'legacy',transcription:{text:'meaning'}});
  };
  const lifetime=new LessonLifetime(),operation=lifetime.begin();
  assert.equal((await deliverExercise(operation,'e',audio(),()=>{})).id,'legacy');
  assert.equal(posts,1);lifetime.close();
});

test('202 processing polls exact operation without resending audio',async()=>{
  const c=fakeEnv(),blob=audio();let posts=0,reads=0;
  const ref={operation_id:'00000000-0000-4000-8000-000000000001',kind:'upload'};
  globalThis.fetch=async(url)=>{
    if(url==='/api/upload-policy')return Response.json({enabled:true,...defaults});
    if(url.endsWith('/attempts')){posts++;return Response.json(ref,{status:202});}
    reads++;return Response.json({...ref,status:reads<3?'processing':'complete',...(reads<3?{}:{result:{id:'accepted',transcription:{text:'synthetic'}}})});
  };
  const lifetime=new LessonLifetime(),operation=lifetime.begin();
  const r=await run(c,deliverExercise(operation,'e',blob,()=>assert.fail('recording discarded'),{env:c.env}));
  assert.equal(r.result.id,'accepted');assert.equal(posts,1);assert.equal(reads,3);lifetime.close();
});

test('uncertain processing retains identity; Send again checks status without POST',async()=>{
  const c=fakeEnv(),blob=audio();let posts=0,complete=false;
  const ref={operation_id:'00000000-0000-4000-8000-000000000001',kind:'upload'};
  globalThis.fetch=async(url)=>{
    if(url==='/api/upload-policy')return Response.json({enabled:true,...defaults});
    if(url.endsWith('/attempts')){posts++;return Response.json(ref,{status:202});}
    return Response.json({...ref,status:complete?'complete':'uncertain',...(complete?{result:{id:'accepted'}}:{})});
  };
  const lifetime=new LessonLifetime(),operation=lifetime.begin();
  const first=await run(c,deliverExercise(operation,'e',blob,()=>assert.fail(),{env:c.env}));
  assert.equal(first.error.reason,'ambiguous');assert.equal(posts,1);
  complete=true;enabled=false;
  const second=await run(c,deliverExercise(operation,'e',blob,()=>assert.fail(),{env:c.env}));
  assert.equal(second.result.id,'accepted');assert.equal(posts,1);lifetime.close();
});

test('mismatched processing receipt cannot adopt another recording',async()=>{
  const c=fakeEnv();let posts=0;
  globalThis.fetch=async(url)=>{
    if(url==='/api/upload-policy')return Response.json({enabled:true,...defaults});
    if(url.endsWith('/attempts')){posts++;return Response.json({operation_id:'00000000-0000-4000-8000-000000000001',kind:'upload'},{status:202});}
    return Response.json({operation_id:'00000000-0000-4000-8000-000000000002',kind:'upload',status:'complete',result:{id:'wrong'}});
  };
  const lifetime=new LessonLifetime();
  const r=await run(c,deliverExercise(lifetime.begin(),'e',audio(),()=>assert.fail(),{env:c.env}));
  assert.equal(r.error.reason,'ambiguous');assert.equal(posts,1);lifetime.close();
});

for (const stop of ['background','cancel','deadline']) test(`processing ${stop} retains audio and never resends`,async()=>{
  const c=fakeEnv(),blob=audio(),lifetime=new LessonLifetime(),operation=lifetime.begin();let posts=0,discarded=0;
  const ref={operation_id:'00000000-0000-4000-8000-000000000001',kind:'upload'};
  globalThis.fetch=async(url)=>{
    if(url==='/api/upload-policy')return Response.json({enabled:true,...defaults});
    if(url.endsWith('/attempts')){posts++;return Response.json(ref,{status:202});}
    return Response.json({...ref,status:'processing'});
  };
  const promise=deliverExercise(operation,'e',blob,()=>discarded++,{env:c.env});promise.catch(()=>{});
  await c.advance(1000);
  if(stop==='background')c.setHidden(true);
  if(stop==='cancel')operation.cancel();
  const r=await run(c,promise);
  assert.ok(r.error);assert.equal(posts,1);assert.equal(discarded,0);
  assert.equal(r.error.name,stop==='cancel'?'AbortError':'DeliveryFailed');
  assert.equal(c.timers,0);lifetime.close();
});

test('repeated uncertain status keeps recovery controls and processing indication without more sends',async()=>{
  const c=fakeEnv(),blob=audio(),lifetime=new LessonLifetime();let posts=0,discarded=0;
  const ref={operation_id:'00000000-0000-4000-8000-000000000001',kind:'upload'};
  globalThis.fetch=async(url)=>{
    if(url==='/api/upload-policy')return Response.json({enabled:true,...defaults});
    if(url.endsWith('/attempts')){posts++;return Response.json(ref,{status:202});}
    return Response.json({...ref,status:'uncertain'});
  };
  for(let i=0;i<3;i++){
    const states=[];
    const r=await run(c,deliverExercise(lifetime.begin(),'e',blob,()=>discarded++,{env:c.env,onState:s=>states.push(s.kind)}));
    assert.equal(r.error.name,'DeliveryFailed');assert.equal(r.error.reason,'ambiguous');
    assert.ok(states.includes('processing'));assert.equal(states.at(-1),'failed');
  }
  assert.equal(posts,1);assert.equal(discarded,0);lifetime.close();
});
