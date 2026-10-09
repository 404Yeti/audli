import { test, expect, type Page } from '@playwright/test';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';
import { installVoice, promptWav, probe, endAudio, type Probe } from './voice-fixture';

async function setup(page: Page, returning=false, autoPlayback=true, recovery?: 'exercise' | 'closing', delayIntroduction=false, delayCue=false, missingInterests=false) {
  await installVoice(page,{autoPlayback,delayBody:delayIntroduction?'/introduction-audio':delayCue?'/recognition/acknowledgment-audio':undefined});
  let value:LessonCheckpoint|null=null,exerciseState='LISTENING',pendingAttempt=false;
  const requests:string[]=[],spoken:string[]=[],phases:string[]=[];
  let failedGeneration = false;
  const clip={id:'clip',audio_url:'/api/exercises/clip/audio',topic:'Pottery making',completed_attempt_id:null as string|null};
  const snapshot=()=>({...value!});
  const phase=(next:LessonCheckpoint['phase'],prompt:string|null)=>{value!.phase=next;value!.prompt=prompt;value!.revision++;phases.push(next);};
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;requests.push(path);
    const audio=(text:string)=>{spoken.push(text);return route.fulfill({contentType:'audio/wav',body:promptWav()});};
    if(path==='/api/auth/config')return route.fulfill({json:{mode:'local'}});
    if(path==='/api/profile')return route.fulfill({json:{destination:'session_ready',profile:{name:'Maya',goal:'Meetings',completed_attempts:returning?2:0},provider:'openai'}});
    if(path==='/api/history')return route.fulfill({json:[]});
    if(path==='/api/exercises/current')return route.fulfill({json:value?.exercise??null});
    if(path==='/api/recognition/acknowledgment-audio')return audio(({assessment:'Okay, I heard you.',followup:'Okay, I’ve heard that.',reflection:'Let me keep that in mind.'})[route.request().postDataJSON().cue as 'assessment'|'followup'|'reflection']);
    if(path==='/api/lessons/current')return route.fulfill({json:value?.status==='completed'?null:value});
    if(path==='/api/lessons/start'){
      if(!value)value={id:'structured',revision:0,status:'active',phase:'WELCOME',prompt:'Hi, Maya. How has your day been?',pending:null,exercise:null,elapsed_seconds:0,remaining_seconds:600,server_time:new Date().toISOString(),focus:'details',exercises_completed:0,strength:null,improvement_focus:null,encouragement:'Thanks for listening, Maya.'};
      return route.fulfill({json:snapshot()});
    }
    if(path.startsWith('/api/lessons/')){
      if(path.endsWith('/introduction-audio'))return audio(value!.introduction_prompt!);
      if(path.endsWith('/introduction-heard')){value!.introduction_pending=false;value!.revision++;}
      if(path.endsWith('/audio'))return audio(value!.prompt!);
      if(path.endsWith('/end'))value!.status='paused';
      if(path.endsWith('/resume'))value!.status='active';
      if(path.endsWith('/attempts')){value!.pending={text:value!.phase==='WELCOME'?"I'm good, and you?":'I enjoy science',confidence:.99,uncertainty:[]};value!.revision++;}
      if(path.endsWith('/answer')){const welcome=value!.phase==='WELCOME';value!.pending=null;phase(welcome?'WELCOME_ACK':'REVIEW_ACK',welcome?'Good to hear. Doing well, thanks for asking!':missingInterests?'We can continue with today’s listening.':'I’ll keep science in mind as we choose our listening topics.');}
      if(path.endsWith('/heard')){
        if(value!.phase==='WELCOME_ACK')phase(returning?'REVIEW':'TRANSITION',returning?'Last time, we listened to work meetings. You demonstrated strength in following the main idea. What would you enjoy listening to today?':'Today, let’s practice catching important details. Here we go.');
        else if(value!.phase==='REVIEW_ACK')phase('TRANSITION','Today, let’s practice catching important details. Here we go.');
        else if(value!.phase==='TRANSITION')phase('EXERCISE',null);
        else if(value!.phase==='CLOSING'){phase('COMPLETED',null);value!.status='completed';}
      }
      if(path.endsWith('/exercise')){
        if(recovery && value!.exercises_completed===1 && !failedGeneration){failedGeneration=true;value!.elapsed_seconds=150;value!.remaining_seconds=450;return route.fulfill({status:503,json:{detail:'I couldn’t get the next listening passage ready. Choose Retry conversation to continue; your lesson progress is saved.'}});}
        if(recovery==='closing' && failedGeneration){value!.elapsed_seconds=550;value!.remaining_seconds=50;phase('CLOSING','You caught the main idea today. Thanks for listening with me.');}
        else {if(failedGeneration){clip.id='recovered-clip';clip.topic='Baking bread';clip.audio_url='/api/exercises/recovered-clip/audio';clip.completed_attempt_id=null;exerciseState='LISTENING';}value!.exercise=clip;value!.introduction_pending=true;value!.introduction_prompt=value!.exercises_completed?'Now, let’s try another recording.':'Let’s listen to the first recording.';value!.revision++;}
      }
      if(path.endsWith('/advance')){value!.exercise=null;value!.exercises_completed++;value!.strength='following the main idea';
        if(recovery && value!.exercises_completed===1){value!.elapsed_seconds=120;phase('EXERCISE',null);}
        else {value!.elapsed_seconds=530;phase('CLOSING','Today, we listened to work meetings. You caught the main idea. Listen for dates and times next time. Thanks for listening with me.');}}
      return route.fulfill({json:snapshot()});
    }
    const conversation=()=>({state:exerciseState,cue_id:exerciseState==='LISTENING'?null:clip.completed_attempt_id?'feedback':'summary',prompt:clip.completed_attempt_id?'You understood the meeting changed; listen for the new deadline next time.':'Tell me what you understood.',active_followup:null,pending_attempt:pendingAttempt?{id:'answer',transcription:{text:'The meeting changed',confidence:.99,uncertainty:[]}}:null,result:clip.completed_attempt_id?{}:null,topic:clip.topic});
    if(path.endsWith('/conversation/listened'))exerciseState='AWAITING_SUMMARY';
    if(path.endsWith('/assess')){exerciseState='GIVING_FEEDBACK';clip.completed_attempt_id='answer';pendingAttempt=false;}
    if(path.endsWith('/ready'))exerciseState='READY_FOR_NEXT';
    if(path.endsWith('/conversation')||path.endsWith('/listened')||path.endsWith('/assess')||path.endsWith('/ready'))return route.fulfill({json:conversation()});
    if(path.endsWith('/attempts')){pendingAttempt=true;return route.fulfill({json:{id:'answer',transcription:{text:'The meeting changed',confidence:.99,uncertainty:[]}}});}
    if(path.endsWith('/coach-audio'))return audio(conversation().prompt);
    if(path.endsWith('/transcript'))return route.fulfill(clip.completed_attempt_id?{json:{title:'Meeting',script:'Completed passage'}}:{status:403,json:{detail:'Complete assessment first.'}});
    return audio('Listening passage');
  });
  await page.goto('/');await expect(page.getByRole('button',{name:'Start today’s session'})).toBeEnabled();
  return {requests,spoken,phases,snapshot};
}

async function runToCompletion(page:Page){
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<45;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Lesson completed'}).count())return;}
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
}

for(const returning of [false,true])test(`${returning?'returning':'first-time'} lesson is teacher-led from named greeting to evidence-backed completion`,async({page})=>{
  const fixture=await setup(page,returning);await runToCompletion(page);
  expect(fixture.spoken).toContain('Hi, Maya. How has your day been?');
  expect(fixture.spoken.some(text=>text.startsWith('Last time'))).toBe(returning);
  expect(fixture.requests.filter(path=>path==='/api/lessons/structured/attempts')).toHaveLength(returning?2:1);
  expect(fixture.requests.filter(path=>path.endsWith('/exercise'))).toHaveLength(1);
  expect(fixture.phases).toContain('TRANSITION');expect(fixture.phases).toContain('CLOSING');
  expect(fixture.spoken.some(text=>text.includes('thanks for asking'))).toBe(true);
  expect(fixture.spoken).not.toContain('Okay, I heard you.');
  expect(fixture.spoken).toContain('Let’s listen to the first recording.');
  await expect(page.getByText('following the main idea',{exact:true})).toBeVisible();
  await expect(page.locator('textarea')).toHaveCount(0);expect((await probe(page)).clicks).toBe(1);
  await page.reload();await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();expect((await probe(page)).plays).toHaveLength(0);
  await page.getByRole('button',{name:'Return Home'}).click();await expect(page.getByRole('heading',{name:'Good to see you, Maya.'})).toBeVisible();
});

for(const recovery of ['exercise','closing'] as const)test(`Retry conversation preserves completed work and ${recovery==='closing'?'closes when time is insufficient':'continues with one fresh exercise'}`,async({page})=>{
  const fixture=await setup(page,false,true,recovery);
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<45;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('button',{name:'Retry conversation'}).count())break;}
  await expect(page.locator('.session-error[role="alert"]')).toContainText('your lesson progress is saved');
  expect(fixture.snapshot().exercises_completed).toBe(1);expect(fixture.snapshot().exercise).toBeNull();
  const before=fixture.requests.filter(path=>path.endsWith('/assess')).length;
  await page.getByRole('button',{name:'Retry conversation'}).click();
  for(let i=0;i<45;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Lesson completed'}).count())break;}
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
  expect(fixture.snapshot().exercises_completed).toBe(recovery==='closing'?1:2);
  expect(fixture.requests.filter(path=>path.endsWith('/assess')).length-before).toBe(recovery==='closing'?0:1);
  expect(fixture.requests.filter(path=>path.endsWith('/exercise'))).toHaveLength(3);
  expect(fixture.spoken.filter(text=>text==='Hi, Maya. How has your day been?')).toHaveLength(1);
});

test('explicit End pauses welcome and refresh stays silent until a new gesture',async({page})=>{
  const fixture=await setup(page,false,false);await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  await expect(page.getByRole('status')).toHaveText('Audli is speaking');await endAudio(page);await expect(page.getByRole('status')).toHaveText('Listening to you');
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();
  await expect.poll(()=>fixture.snapshot().status).toBe('paused');await page.reload();await expect(page.getByRole('button',{name:'Start today’s session'})).toBeVisible();
  expect((await probe(page)).plays).toHaveLength(0);expect(fixture.requests.some(path=>path.endsWith('/attempts'))).toBe(false);
  await page.getByRole('button',{name:'Start today’s session'}).click();await expect(page.getByRole('status')).toHaveText('Audli is speaking');expect(fixture.snapshot().phase).toBe('WELCOME');
});

test('refresh resumes the authoritative personal checkpoint and acknowledgment waits for delayed evaluation without praise',async({page})=>{
  const fixture=await setup(page,false,true);let release:(()=>Promise<void>)|undefined;
  await page.route('**/api/lessons/structured/answer',async route=>{await new Promise<void>(resolve=>{release=async()=>{await route.fallback();resolve();};});});
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<8&&!release;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(()=>!!release).toBe(true);expect(fixture.snapshot().phase).toBe('WELCOME');
  await page.clock.runFor(1000);await expect(page.getByRole('status')).toHaveText('Thinking');
  expect(fixture.requests.some(path=>path.endsWith('/exercise'))).toBe(false);
  await page.unroute('**/api/lessons/structured/answer');await release!();
  await page.reload();
  await expect(page.locator('.session-shell')).toBeVisible();
  expect(fixture.requests.filter(path=>path==='/api/lessons/structured/attempts')).toHaveLength(1);
});

test('End during delayed reflection acknowledgment cancels playback and cannot launch a clip',async({page})=>{
  const fixture=await setup(page,true,true);let release:(()=>Promise<void>)|undefined;
  await page.route('**/api/lessons/structured/answer',async route=>{
    if(fixture.snapshot().phase!=='REVIEW')return route.fallback();
    await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({json:{...fixture.snapshot(),phase:'REVIEW_ACK',prompt:'I’ll keep science in mind.'}}).catch(()=>{});resolve();};});
  });
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<20&&!release;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(()=>!!release).toBe(true);await page.clock.runFor(1300);
  await expect.poll(()=>fixture.spoken.includes('Let me keep that in mind.')).toBe(true);
  await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();
  await expect.poll(()=>fixture.snapshot().status).toBe('paused');const count=fixture.requests.length;await release!();await page.waitForTimeout(100);
  expect(fixture.requests.length).toBe(count);expect(fixture.requests.some(path=>path.endsWith('/exercise'))).toBe(false);
  expect((await probe(page)).revoked).toEqual((await probe(page)).urls);await expect(page.getByRole('button',{name:'Start today’s session'})).toBeVisible();
});

test('refresh retries an unheard introduction and resumes the same exercise',async({page})=>{
  const fixture=await setup(page,false,true,undefined,true);
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<25;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if((await probe(page)).bodyPending)break;}
  expect((await probe(page)).bodyPending).toBe(true);
  expect(fixture.snapshot().introduction_pending).toBe(true);
  await page.reload();
  await expect.poll(async()=>(await probe(page)).bodyPending).toBe(true);
  await page.evaluate(()=> (window as unknown as {voiceProbe:Probe}).voiceProbe.releaseBody!());
  for(let i=0;i<30;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Lesson completed'}).count())break;}
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
  expect(fixture.requests.filter(path=>path.endsWith('/introduction-audio'))).toHaveLength(2);
  expect(fixture.requests.filter(path=>path.endsWith('/exercise'))).toHaveLength(1);
});

test('processing acknowledgment is delayed and deduplicated across retry; topic is visible while assessment is pending',async({page})=>{
  const fixture=await setup(page);let release:(()=>Promise<void>)|undefined,calls=0;
  await page.route('**/api/attempts/answer/assess',async route=>{
    const current=++calls;
    await new Promise<void>(resolve=>{release=async()=>{if(current===1)await route.fulfill({status:503,json:{detail:'Please retry this assessment.'}});else await route.fallback();resolve();};});
  });
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<25&&!release;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(()=>!!release).toBe(true);
  expect(fixture.spoken).not.toContain('Okay, I heard you.');
  await expect(page.getByRole('note',{name:'Exercise topic'})).toHaveText('Topic: Pottery making');
  await page.clock.runFor(1300);await expect.poll(()=>fixture.spoken.filter(text=>text==='Okay, I heard you.').length).toBe(1);
  await page.clock.runFor(100);await release!();release=undefined;
  await expect(page.getByRole('button',{name:'Retry conversation'})).toBeVisible();
  await page.getByRole('button',{name:'Retry conversation'}).click();await expect.poll(()=>!!release).toBe(true);
  await page.clock.runFor(1500);expect(fixture.spoken.filter(text=>text==='Okay, I heard you.')).toHaveLength(1);
  await release!();
  await expect(page.getByText('Topic: Pottery making',{exact:true})).toBeVisible();
  await expect(page.locator('.session-topic')).toHaveAttribute('aria-live','polite');
  for(let i=0;i<15;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Lesson completed'}).count())break;}
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
});

for(const reducedMotion of [false,true])test(`completion cards and mascot respect ${reducedMotion?'reduced':'normal'} motion`,async({page})=>{
  await page.emulateMedia({reducedMotion:reducedMotion?'reduce':'no-preference'});
  await setup(page);await runToCompletion(page);
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeFocused();
  await expect(page.locator('.completion-card')).toHaveCount(3);
  await expect(page.locator('.completion-count')).toHaveText('1');
  await expect(page.locator('.lesson-completion .audli-mascot')).toHaveAttribute('aria-hidden','true');
  const body=page.locator('.lesson-completion .mascot-body');
  const animation=await body.evaluate(node=>getComputedStyle(node).animationName);
  expect(animation).toBe(reducedMotion?'none':'completion-arrive, mascot-breathe');
  if(!reducedMotion)await page.screenshot({path:'/tmp/aud22-completion-polish.png',fullPage:true,animations:'disabled'});
  if(!reducedMotion){await page.evaluate(()=>document.documentElement.dataset.reducedMotion='true');expect(await body.evaluate(node=>getComputedStyle(node).animationName)).toBe('none');}
  expect(await page.locator('body').innerText()).not.toMatch(/\d+%|\bXP\b/);
});

test('slow optional acknowledgment audio never blocks completed assessment or coaching',async({page})=>{
  await setup(page,false,true,undefined,false,true);
  let release:(()=>Promise<void>)|undefined;
  await page.route('**/api/attempts/answer/assess',async route=>{await new Promise<void>(resolve=>{release=async()=>{await route.fallback();resolve();};});});
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<25&&!release;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(()=>!!release).toBe(true);await page.clock.runFor(1300);
  await expect.poll(async()=>(await probe(page)).bodyPending).toBe(true);
  await release!();
  for(let i=0;i<15;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Lesson completed'}).count())break;}
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
  const before=(await probe(page)).plays.length;
  await page.evaluate(()=> (window as unknown as {voiceProbe:Probe}).voiceProbe.releaseBody!());
  await page.waitForTimeout(100);
  expect((await probe(page)).plays).toHaveLength(before);
});


test('refresh after completed introduction never replays it before the saved passage',async({page})=>{
  const fixture=await setup(page);
  let release:(()=>Promise<void>)|undefined,calls=0;
  await page.route('**/api/exercises/clip/audio',async route=>{
    if(++calls>1)return route.fallback();
    await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({contentType:'audio/wav',body:promptWav()}).catch(()=>{});resolve();};});
  });
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<25&&!release;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(()=>!!release).toBe(true);
  expect(fixture.snapshot().introduction_pending).toBe(false);
  await expect(page.getByRole('note',{name:'Exercise topic'})).toHaveText('Topic: Pottery making');
  await page.reload();await release!();
  for(let i=0;i<25;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Lesson completed'}).count())break;}
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
  expect(fixture.requests.filter(path=>path.endsWith('/introduction-audio'))).toHaveLength(1);
  expect(fixture.requests.filter(path=>path.endsWith('/introduction-heard'))).toHaveLength(1);
  expect(fixture.requests.filter(path=>path.endsWith('/exercise'))).toHaveLength(1);
});


test('review extraction recovery keeps the pending reflection and does not replay questions or duplicate exercises',async({page})=>{
  const fixture=await setup(page,true,true,undefined,false,false,true);let failed=false;
  await page.route('**/api/lessons/structured/answer',async route=>{
    if(fixture.snapshot().phase==='REVIEW'&&!failed){failed=true;return route.fulfill({status:422,json:{detail:'I couldn’t pick out a topic preference. Choose Retry conversation; your lesson and saved preferences are unchanged.'}});}
    return route.fallback();
  });
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<25;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('button',{name:'Retry conversation'}).count())break;}
  await expect(page.getByRole('button',{name:'Retry conversation'})).toBeVisible();
  expect(fixture.snapshot().phase).toBe('REVIEW');expect(fixture.snapshot().pending).not.toBeNull();
  await page.getByRole('button',{name:'Retry conversation'}).click();
  for(let i=0;i<25;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Lesson completed'}).count())break;}
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
  expect(fixture.spoken).toContain('We can continue with today’s listening.');
  expect(fixture.spoken.filter(text=>text.startsWith('Last time'))).toHaveLength(1);
  expect(fixture.requests.filter(path=>path.endsWith('/attempts')&&path.includes('/lessons/'))).toHaveLength(2);
  expect(fixture.requests.filter(path=>path.endsWith('/exercise'))).toHaveLength(1);
  expect(fixture.requests.filter(path=>path.endsWith('/assess'))).toHaveLength(1);
  expect(fixture.spoken.filter(text=>text==='Let’s listen to the first recording.')).toHaveLength(1);
});


test('turn telemetry counts meaningful coaching after assessment, separately from acknowledgment playback', async ({page}) => {
  const stages: string[] = [];
  page.on('console', async message => {
    if (!message.text().startsWith('[Audli turn]')) return;
    const sample = await message.args()[1]?.jsonValue().catch(() => null);
    if (sample && typeof sample.stage === 'string') stages.push(sample.stage);
  });
  await setup(page);
  let release: (() => Promise<void>) | undefined;
  await page.route('**/api/attempts/answer/assess', async route => {
    await new Promise<void>(resolve => {release = async () => {await route.fallback();resolve();};});
  });
  await page.clock.install();
  await page.getByRole('button', {name:'Start today’s session'}).click();
  for (let i=0;i<25&&!release;i++) {await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(() => !!release).toBe(true);
  const before = stages.filter(stage => stage === 'turn_to_playback').length;
  await page.clock.runFor(1400);
  await expect.poll(() => stages.includes('acknowledgment_playback')).toBe(true);
  expect(stages.filter(stage => stage === 'turn_to_playback')).toHaveLength(before);
  await release!();
  for (let i=0;i<5;i++) {await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(() => stages.filter(stage => stage === 'turn_to_playback').length).toBeGreaterThan(before);
  expect(stages).toContain('first_audio_byte');
  expect(stages).toContain('audio_body_ready');
});


async function heldAssessment(page: Page) {
  const fixture = await setup(page, false, false);
  let release: (() => Promise<void>) | undefined;
  await page.route('**/api/attempts/answer/assess', async route => {
    await new Promise<void>(resolve => { release = async () => { await route.fallback(); resolve(); }; });
  });
  await page.clock.install();
  await page.getByRole('button', {name:'Start today’s session'}).click();
  for (let i=0;i<30&&!release;i++) {
    await page.clock.runFor(1000);
    await page.waitForTimeout(30);
    if (!release && await page.locator('audio').evaluate(element => !(element as HTMLAudioElement).paused)) await endAudio(page);
  }
  await expect.poll(() => !!release).toBe(true);
  await page.clock.runFor(1400);
  await expect.poll(() => fixture.spoken.includes('Okay, I heard you.')).toBe(true);
  await expect.poll(() => page.locator('audio').evaluate(element => (element as HTMLAudioElement).paused)).toBe(false);
  return {fixture, release:release!, coachRequests:()=>fixture.requests.filter(path=>path.endsWith('/coach-audio')).length};
}

test('saved coaching prepares during acknowledgment but plays serially once', async ({page}) => {
  // Mock microphone/providers and virtual time: scheduling evidence, not acoustic latency.
  const timings: {stage:string;elapsedMs:number}[]=[];
  page.on('console',async message=>{
    if(!message.text().startsWith('[Audli turn]'))return;
    const sample=await message.args()[1]?.jsonValue().catch(()=>null);
    if(sample && ['acknowledgment_duration','coaching_readiness','coaching_wait_after_ack'].includes(sample.stage))timings.push(sample);
  });
  const {fixture,release,coachRequests}=await heldAssessment(page);
  const before=coachRequests(), plays=(await probe(page)).plays.length;
  const readyBefore=timings.filter(sample=>sample.stage==='coaching_readiness').length;
  await release();
  await expect.poll(coachRequests).toBe(before+1);
  await expect.poll(()=>timings.filter(sample=>sample.stage==='coaching_readiness').length).toBeGreaterThan(readyBefore);
  await page.clock.runFor(5000);await page.waitForTimeout(100);
  expect((await probe(page)).plays).toHaveLength(plays);
  expect(fixture.requests.some(path=>path.endsWith('/ready')||path.endsWith('/advance'))).toBe(false);
  await endAudio(page);
  await expect.poll(async()=>(await probe(page)).plays.length).toBe(plays+1);
  expect(coachRequests()).toBe(before+1);
  await expect.poll(()=>timings.some(sample=>sample.stage==='coaching_wait_after_ack')).toBe(true);
  console.log('Controlled virtual-time coaching handoff:',JSON.stringify(timings));
  expect(fixture.requests.some(path=>path.endsWith('/ready'))).toBe(false);
  await endAudio(page);
  await expect.poll(()=>fixture.requests.filter(path=>path.endsWith('/ready')).length).toBe(1);
});

test('slow prepared coaching waits after acknowledgment without skipping feedback',async({page})=>{
  const {fixture,release,coachRequests}=await heldAssessment(page);
  let finish: (()=>Promise<void>)|undefined;
  await page.route('**/api/exercises/clip/coach-audio',async route=>{
    await new Promise<void>(resolve=>{finish=async()=>{await route.fallback();resolve();};});
  });
  const plays=(await probe(page)).plays.length;
  await release();await expect.poll(()=>!!finish).toBe(true);
  await endAudio(page);await page.clock.runFor(3000);await page.waitForTimeout(100);
  expect((await probe(page)).plays).toHaveLength(plays);
  expect(fixture.requests.some(path=>path.endsWith('/ready'))).toBe(false);
  await finish!();await expect.poll(async()=>(await probe(page)).plays.length).toBe(plays+1);
  expect(coachRequests()).toBe(2); // summary and feedback, one each
});

test('failed background coaching retries without repeating saved assessment',async({page})=>{
  const {fixture,release}=await heldAssessment(page);
  let requests=0;
  await page.route('**/api/exercises/clip/coach-audio',async route=>{
    requests++;
    if(requests===1)return route.fulfill({status:503,json:{detail:'Speech unavailable. Retry conversation.'}});
    await route.fallback();
  });
  await release();await expect.poll(()=>requests).toBe(1);
  await expect(page.getByRole('button',{name:'Retry conversation'})).toHaveCount(0);
  await endAudio(page);await expect(page.getByRole('button',{name:'Retry conversation'})).toBeVisible();
  expect(fixture.snapshot().exercise?.completed_attempt_id).toBe('answer');
  await page.getByRole('button',{name:'Retry conversation'}).click();
  await expect.poll(()=>requests).toBe(2);
  await expect.poll(()=>fixture.spoken.filter(text=>text.startsWith('You understood')).length).toBe(1);
  expect(fixture.requests.filter(path=>path.endsWith('/assess'))).toHaveLength(1);
});

for(const interrupt of ['end','stale','reload'] as const)test(`prepared coaching respects ${interrupt}`,async({page})=>{
  const {fixture,release,coachRequests}=await heldAssessment(page);
  const before=coachRequests();await release();await expect.poll(coachRequests).toBe(before+1);
  await page.waitForTimeout(100);
  const plays=(await probe(page)).plays.length;
  if(interrupt==='end'){
    await page.getByRole('button',{name:'End',exact:true}).click();
    await page.getByRole('button',{name:'End session',exact:true}).click();
    await page.clock.runFor(2000);
    expect((await probe(page)).plays).toHaveLength(plays);
    await expect.poll(()=>fixture.snapshot().status).toBe('paused');
  }else if(interrupt==='stale'){
    await page.route('**/api/lessons/structured',route=>route.fulfill({json:{...fixture.snapshot(),revision:fixture.snapshot().revision+1}}));
    await endAudio(page);
    await expect(page.getByRole('button',{name:'Retry conversation'})).toBeVisible();
    expect((await probe(page)).plays).toHaveLength(plays);
  }else{
    await page.reload();
    // Unexpected refresh restores the saved active conversation automatically.
    await expect.poll(async()=>(await probe(page)).plays.length).toBe(1);
    await page.waitForTimeout(100);
    expect((await probe(page)).plays).toHaveLength(1);
    expect(fixture.spoken.filter(text=>text==='Okay, I heard you.')).toHaveLength(1);
    expect(fixture.requests.filter(path=>path.endsWith('/assess'))).toHaveLength(1);
  }
  expect(fixture.requests.some(path=>path.endsWith('/ready')||path.endsWith('/advance'))).toBe(false);
});

for(const interrupt of [false,true])test(`opening reciprocity plays before advancing${interrupt?' and End discards late audio':''}`,async({page})=>{
  const fixture=await setup(page,false,false);
  const stages:string[]=[];
  page.on('console',async message=>{
    if(!message.text().startsWith('[Audli turn]'))return;
    const value=await message.args()[1]?.jsonValue().catch(()=>null);
    if(value?.stage)stages.push(value.stage);
  });
  let release:(()=>Promise<void>)|undefined;
  await page.route('**/api/lessons/structured/audio',async route=>{
    if(fixture.snapshot().phase!=='WELCOME_ACK')return route.fallback();
    await new Promise<void>(resolve=>{release=async()=>{await route.fallback().catch(()=>{});resolve();};});
  });
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  await expect(page.getByRole('status')).toHaveText('Audli is speaking');await endAudio(page);
  for(let i=0;i<15&&!release;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);}
  await expect.poll(()=>!!release).toBe(true);
  expect(fixture.snapshot().phase).toBe('WELCOME_ACK');
  expect(fixture.requests.some(path=>path.endsWith('/heard'))).toBe(false);
  await expect.poll(()=>stages.includes('upload_transcription')&&stages.includes('checkin_response')).toBe(true);
  const plays=(await probe(page)).plays.length;
  if(interrupt){
    await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();
    await expect.poll(()=>fixture.snapshot().status).toBe('paused');await release!();
    await page.waitForTimeout(100);expect((await probe(page)).plays).toHaveLength(plays);
    expect(fixture.requests.some(path=>path.endsWith('/heard'))).toBe(false);
  }else{
    await release!();await expect.poll(async()=>(await probe(page)).plays.length).toBe(plays+1);
    expect(fixture.spoken.at(-1)).toContain('Doing well, thanks for asking');
    expect(fixture.snapshot().phase).toBe('WELCOME_ACK');
    await expect.poll(()=>stages.includes('turn_to_playback')).toBe(true);
    expect(stages).toContain('tts_readiness');
    await endAudio(page);await expect.poll(()=>fixture.snapshot().phase).toBe('TRANSITION');
    expect(fixture.requests.filter(path=>path.endsWith('/heard'))).toHaveLength(1);
    await expect.poll(async()=>(await probe(page)).plays.length).toBe(plays+2);
  }
});
