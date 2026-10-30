import { test, expect, type Page } from '@playwright/test';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';
import { installVoice, promptWav, probe, endAudio, type Probe } from './voice-fixture';
type MascotFrame = { count:number; white:number; bounds:number[]; width:number; height:number; angle:number|null };
type MascotCapture = { frames:MascotFrame[]; raf:number };

async function setup(page: Page, returning=false, autoPlayback=true, recovery?: 'exercise' | 'closing', delayIntroduction=false, delayCue=false, missingInterests=false, accentRequest?:string, accentConsent=false, loseAttemptResponse=false, accentFallback=false) {
  await installVoice(page,{autoPlayback,delayBody:delayIntroduction?'/introduction-audio':delayCue?'/recognition/acknowledgment-audio':undefined});
  let value:LessonCheckpoint|null=null,exerciseState='LISTENING',pendingAttempt=false;
  const requests:string[]=[],spoken:string[]=[],phases:string[]=[];
  let failedGeneration = false;
  let accentReplies = 0, lostResponse = false;
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
      if(!value)value={id:'structured',revision:0,status:'active',phase:'WELCOME',prompt:accentConsent?'Want to practice Australian English for the next passage?':'Hi, Maya. How has your day been?',pending:null,exercise:null,elapsed_seconds:0,remaining_seconds:600,server_time:new Date().toISOString(),focus:'details',exercises_completed:0,strength:null,improvement_focus:null,encouragement:'Thanks for listening, Maya.'};
      return route.fulfill({json:snapshot()});
    }
    if(path.startsWith('/api/lessons/')){
      if(path.endsWith('/introduction-audio'))return audio(value!.introduction_prompt!);
      if(path.endsWith('/introduction-heard')){value!.introduction_pending=false;value!.revision++;}
      if(path.endsWith('/audio'))return audio(value!.prompt!);
      if(path.endsWith('/end'))value!.status='paused';
      if(path.endsWith('/resume'))value!.status='active';
      if(path.endsWith('/attempts')){
        value!.pending={text:accentConsent?(accentReplies?'Yes, I’d like to try that.':'Maybe'):value!.phase==='WELCOME'?"I'm good, and you?":accentRequest??'I enjoy science',confidence:.99,uncertainty:[]};value!.revision++;
        if(loseAttemptResponse&&!lostResponse){lostResponse=true;return route.abort('internetdisconnected');}
      }
      if(path.endsWith('/answer')){
        const welcome=value!.phase==='WELCOME';value!.pending=null;
        if(accentConsent){accentReplies++;phase(accentReplies===1?'REVIEW':'REVIEW_ACK',accentReplies===1?'Would you like Australian English for the next new passage? You can say yes or no.':'Perfect, we’ll practice Australian English for the next new passage only.');}
        else phase(welcome?'WELCOME_ACK':'REVIEW_ACK',welcome?'Good to hear. Doing well, thanks for asking!':accentRequest?'I’ll remember that accent preference. Accent-specific recordings aren’t available yet, so today’s audio will use our usual voice.':missingInterests?'We can continue with today’s listening.':'I’ll keep science in mind as we choose our listening topics.');
      }
      if(path.endsWith('/heard')){
        if(value!.phase==='WELCOME_ACK')phase(returning?'REVIEW':'TRANSITION',returning?(accentRequest?'Last time, we listened to work meetings. You demonstrated strength in following the main idea. You mentioned Australian English; accent-specific recordings aren’t available yet. What would you enjoy listening to today?':'Last time, we listened to work meetings. You demonstrated strength in following the main idea. What would you enjoy listening to today?'):'Today, let’s practice catching important details. Here we go.');
        else if(value!.phase==='REVIEW_ACK')phase('TRANSITION','Today, let’s practice catching important details. Here we go.');
        else if(value!.phase==='TRANSITION')phase('EXERCISE',null);
        else if(value!.phase==='CLOSING'){phase('COMPLETED',null);value!.status='completed';}
      }
      if(path.endsWith('/exercise')){
        if(recovery && value!.exercises_completed===1 && !failedGeneration){failedGeneration=true;value!.elapsed_seconds=150;value!.remaining_seconds=450;return route.fulfill({status:503,json:{detail:'I couldn’t get the next listening passage ready. Choose Retry conversation to continue; your lesson progress is saved.'}});}
        if(recovery==='closing' && failedGeneration){value!.elapsed_seconds=550;value!.remaining_seconds=50;phase('CLOSING','You caught the main idea today. Thanks for listening with me.');}
        else {if(failedGeneration){clip.id='recovered-clip';clip.topic='Baking bread';clip.audio_url='/api/exercises/recovered-clip/audio';clip.completed_attempt_id=null;exerciseState='LISTENING';}value!.exercise=clip;value!.introduction_pending=true;value!.introduction_prompt=value!.exercises_completed?'Now, let’s try another recording.':accentFallback?'I couldn’t use the requested accent for this recording, so it uses our usual passage voice. Listen carefully, then tell me what you understood.':'Let’s listen to the first recording.';value!.revision++;}
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
  for(let i=0;i<45;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.locator('.completion-summary').isVisible())return;}
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
}

// AUD-95: exercise policy rejection explicitly; ordinary voice fixtures allow play().
async function blockedGreeting(page: Page) {
  await page.evaluate(() => {
    const normal = HTMLMediaElement.prototype.play;
    const gate = { allow: false, pending: false, calls: [] as { src: string; active: boolean }[], release: undefined as (() => void) | undefined };
    (window as unknown as { greetingGate: typeof gate }).greetingGate = gate;
    HTMLMediaElement.prototype.play = function () {
      gate.calls.push({ src: this.src, active: navigator.userActivation.isActive });
      if (!gate.allow) return Promise.reject(new DOMException('Private browser policy detail', 'NotAllowedError'));
      if (gate.pending) return new Promise<void>(resolve => { gate.release = resolve; }).then(() => normal.call(this));
      return normal.call(this);
    };
  });
}
async function allowGreeting(page: Page, pending = false) {
  await page.evaluate(pending => { const gate = (window as unknown as { greetingGate: { allow: boolean; pending: boolean } }).greetingGate; gate.allow = true; gate.pending = pending; }, pending);
}
async function greetingCalls(page: Page) {
  return page.evaluate(() => (window as unknown as { greetingGate: { calls: { src: string; active: boolean }[] } }).greetingGate.calls);
}

test('AUD-95 delayed greeting resumes the same audio directly from a tap without another request', async ({page}) => {
  const fixture = await setup(page, false, false); await blockedGreeting(page); await page.clock.install();
  let release: (() => void) | undefined;
  await page.route('**/api/lessons/structured/audio', async route => { await new Promise<void>(resolve => { release = resolve; }); await route.fallback(); });
  await page.getByRole('button', {name: 'Start today’s session'}).click();
  await expect.poll(() => !!release).toBe(true); await page.clock.runFor(6000); release!();
  const button = page.getByRole('button', {name: 'Tap to play Audli', exact: true}); await expect(button).toBeVisible();
  await expect(page.getByRole('status').filter({hasText: 'Audli’s voice is ready'})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Retry conversation'})).toHaveCount(0);
  const first = (await greetingCalls(page))[0]; const requests = fixture.requests.length;
  expect((await probe(page)).mediaCalls).toBe(1); expect((await probe(page)).recorders).toBe(0);
  expect((await probe(page)).revoked).not.toContain(first.src);
  await allowGreeting(page); await button.click(); await expect(button).toHaveCount(0);
  const calls = await greetingCalls(page); expect(calls).toHaveLength(2); expect(calls[1]).toEqual({src: first.src, active: true});
  expect(fixture.requests).toHaveLength(requests); expect(fixture.requests.filter(path => path === '/api/lessons/start')).toHaveLength(1);
  expect(fixture.requests.filter(path => path.endsWith('/audio'))).toHaveLength(1);
  expect((await probe(page)).recorders).toBe(0); expect(fixture.snapshot().revision).toBe(0);
  await endAudio(page); await expect.poll(async () => (await probe(page)).recorders).toBe(1);
  await page.getByRole('button', {name: 'End', exact: true}).click(); await page.getByRole('button', {name: 'End session', exact: true}).click();
});

test('AUD-95 repeated playback denial stays friendly and End releases retained audio', async ({page}) => {
  const fixture = await setup(page, false, false); await blockedGreeting(page);
  await page.getByRole('button', {name: 'Start today’s session'}).click();
  const button = page.getByRole('button', {name: 'Tap to play Audli', exact: true}); await expect(button).toBeVisible();
  const src = (await greetingCalls(page))[0].src;
  await button.click(); await expect.poll(async () => (await greetingCalls(page)).length).toBe(2); await expect(button).toBeVisible();
  await expect(page.locator('body')).not.toContainText('Private browser policy detail');
  await page.getByRole('button', {name: 'End', exact: true}).click(); await page.getByRole('button', {name: 'End session', exact: true}).click(); await expect(button).toHaveCount(0);
  expect((await probe(page)).revoked).toContain(src); expect((await probe(page)).recorders).toBe(0);
  expect(fixture.requests.filter(path => path.endsWith('/attempts'))).toHaveLength(0);
  expect(fixture.requests.filter(path => path === '/api/lessons/start')).toHaveLength(1);
});

test('AUD-95 concurrent recovery taps and late play completion cannot survive cancellation', async ({page}) => {
  const fixture = await setup(page, false, false); await blockedGreeting(page);
  await page.getByRole('button', {name: 'Start today’s session'}).click();
  const button = page.getByRole('button', {name: 'Tap to play Audli', exact: true}); await expect(button).toBeVisible(); await allowGreeting(page, true);
  await button.dblclick(); expect(await greetingCalls(page)).toHaveLength(2);
  await page.getByRole('button', {name: 'End', exact: true}).click(); await page.getByRole('button', {name: 'End session', exact: true}).click();
  await page.evaluate(() => (window as unknown as {greetingGate:{release?:()=>void}}).greetingGate.release?.());
  await expect(button).toHaveCount(0); expect((await probe(page)).recorders).toBe(0);
  expect(fixture.requests.filter(path => path.endsWith('/attempts'))).toHaveLength(0);
});

test('AUD-95 microphone denial remains separate and creates no lesson or playback recovery', async ({page}) => {
  const fixture = await setup(page);
  await page.evaluate(() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Private microphone detail', 'NotAllowedError'); }; });
  await page.getByRole('button', {name: 'Start today’s session'}).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('Audli needs microphone access');
  await expect(page.locator('body')).not.toContainText('Private microphone detail');
  await expect(page.getByRole('button', {name: 'Tap to play Audli'})).toHaveCount(0);
  expect(fixture.requests.filter(path => path === '/api/lessons/start')).toHaveLength(0);
});

for (const suspended of [true, false]) test(`AUD-95 AudioContext ${suspended ? 'suspension times out' : 'rejection is sanitized'} without uploading`, async ({page}) => {
  const fixture = await setup(page, false, false); await page.clock.install();
  await page.evaluate(suspended => { AudioContext.prototype.resume = () => suspended ? new Promise<void>(() => {}) : Promise.reject(new DOMException('Private context detail', 'InvalidStateError')); }, suspended);
  await page.getByRole('button', {name: 'Start today’s session'}).click(); await expect.poll(async () => (await probe(page)).plays.length).toBe(1);
  await endAudio(page); await expect.poll(async () => (await probe(page)).mediaCalls).toBe(2);
  if (suspended) await page.clock.runFor(5100);
  await expect(page.locator('main').getByRole('alert')).toContainText('Microphone turn detection could not start');
  await expect(page.locator('body')).not.toContainText('Private context detail');
  expect((await probe(page)).tracks).toBe(2); expect((await probe(page)).recorders).toBe(0);
  expect(fixture.requests.filter(path => path.endsWith('/attempts'))).toHaveLength(0);
});

for(const lost of [false,true])test(`accent clarification stays voice driven${lost?' after a committed upload loses its response':''}`,async({page})=>{
  const fixture=await setup(page,false,true,undefined,false,false,false,undefined,true,lost);
  await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(let i=0;i<45;i++){
    await page.clock.runFor(1000);await page.waitForTimeout(30);
    if(await page.getByRole('button',{name:'Retry conversation'}).count())break;
    if(await page.getByRole('heading',{name:'Great job!'}).count())break;
  }
  if(lost){
    await expect(page.getByRole('button',{name:'Retry conversation'})).toBeVisible();
    expect(fixture.snapshot().pending?.text).toBe('Maybe');
    await page.getByRole('button',{name:'Retry conversation'}).click();
    for(let i=0;i<45;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Great job!'}).count())break;}
  }
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
  expect(fixture.requests.filter(path=>path==='/api/lessons/structured/attempts')).toHaveLength(2);
  expect(fixture.spoken.filter(text=>text.includes('You can say yes or no'))).toHaveLength(1);
  expect(fixture.spoken.filter(text=>text.includes('Want to practice Australian'))).toHaveLength(1);
  expect(fixture.spoken).toContain('Perfect, we’ll practice Australian English for the next new passage only.');
  expect(fixture.spoken.some(text=>text.includes('usual passage voice'))).toBe(false);
});

test('accepted accent fallback speaks the actual recording outcome and preserves lesson completion',async({page})=>{
  const fixture=await setup(page,false,true,undefined,false,false,false,undefined,true,false,true);
  await runToCompletion(page);
  expect(fixture.spoken).toContain('Perfect, we’ll practice Australian English for the next new passage only.');
  expect(fixture.spoken.filter(text=>text.includes('couldn’t use the requested accent for this recording'))).toHaveLength(1);
  expect(fixture.spoken.some(text=>text.includes('accent voice is unavailable'))).toBe(false);
  expect(fixture.requests.filter(path=>path==='/api/lessons/structured/attempts')).toHaveLength(2);
});

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
  await expect(page.locator('textarea')).toHaveCount(0);expect((await probe(page)).clicks).toBe(1);
  await expect(page.locator('.completion-summary')).toContainText('following the main idea');
  await page.reload();await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();expect((await probe(page)).plays).toHaveLength(0);
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
  for(let i=0;i<45;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Great job!'}).count())break;}
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
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
  for(let i=0;i<30;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Great job!'}).count())break;}
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
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
  for(let i=0;i<15;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Great job!'}).count())break;}
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
});

for (const reducedMotion of [false, true]) test(`celebration and results share one Success screen with ${reducedMotion ? 'reduced' : 'normal'} motion`, async ({ page }) => {
  await page.emulateMedia({ reducedMotion: reducedMotion ? 'reduce' : 'no-preference' });
  const assets: string[] = [];
  page.on('request', request => { if (/\/audli\/(audli-mascot\.riv|rive\.wasm)/.test(request.url())) assets.push(request.url()); });
  const fixture = await setup(page);
  await runToCompletion(page);
  if (reducedMotion) expect(assets).toEqual([]);
  // One screen: the heading, the statistics and the way out are all there at once; no intermediate step.
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeFocused();
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue to results' })).toHaveCount(0);
  if (reducedMotion) await expect(page.locator('canvas')).toHaveCount(0);
  else await expect(page.locator('canvas')).toHaveCount(1);
  // Nothing dismisses it: not time, not network activity.
  await page.clock.runFor(60000);
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await expect(page.locator('.completion-summary')).toBeVisible();
  const calls = fixture.requests.length;
  await page.clock.runFor(5000);
  expect(fixture.requests.length).toBe(calls);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  if (reducedMotion) await expect(page.locator('canvas')).toHaveCount(0);
  expect((await probe(page)).plays).toHaveLength(0);
  await page.getByRole('button', { name: 'Return Home' }).click();
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
});

for (const asset of ['audli-mascot.riv', 'rive.wasm']) test(`static celebration reaches results when ${asset} fails`, async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.route('**/audli/' + asset, route => route.abort());
  await setup(page); await runToCompletion(page);
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  await expect(page.locator('.lesson-wrapup .session-presence')).toHaveCount(0);
  await expect(page.locator('canvas')).toHaveCount(0);
  await page.getByRole('button', { name: 'Return Home' }).click();
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
});

for (const width of [320, 390, 430]) test(`large transparent one-shot celebration at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 844 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const fixture = await setup(page);
  let releaseAsset = () => {};
  const ready = new Promise<void>(resolve => { releaseAsset = resolve; });
  await page.route('**/audli/audli-mascot.riv', async route => { await ready; await route.continue(); });
  await page.clock.install();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
  for (let i = 0; i < 45; i++) {
    await page.clock.runFor(200); await page.waitForTimeout(30);
    if (await page.getByRole('heading', { name: 'Great job!' }).count()) break;
  }
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeFocused();
  await expect(page.locator('.lesson-celebration .wordmark')).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // The statistics and Return Home share the screen with Pip, fully reachable without scrolling the page sideways.
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeVisible();
  await expect(page.locator('.audli-mascot > svg')).toBeVisible();
  const canvas = page.locator('canvas');
  await canvas.evaluate(node => {
    const c = node as HTMLCanvasElement;
    const capture: MascotCapture = { frames: [], raf: 0 };
    (window as unknown as { mascotCapture: MascotCapture }).mascotCapture = capture;
    const sample = () => {
      if (!c.isConnected || !c.width || !c.height) return;
      const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data;
      let count = 0, white = 0, minX = c.width, minY = c.height, maxX = -1, maxY = -1, bx = 0, by = 0, bn = 0, rx = 0, ry = 0, rn = 0;
      for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
        const j = (y * c.width + x) * 4, r = d[j], g = d[j + 1], b = d[j + 2];
        if (d[j + 3] <= 100) continue;
        if (Math.min(r, g, b) > 200) white++;
        count++; minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
        if (b > r * 1.5 && b > g * 1.3) { bx += x; by += y; bn++; }
        if (r > g * 1.4 && r > b * 1.4) { rx += x; ry += y; rn++; }
      }
      if (count) {
        if (d[3] !== 0 || d[d.length - 1] !== 0) throw new Error('Opaque Rive background');
        capture.frames.push({ count, white, bounds: [minX, minY, maxX, maxY], width: c.width, height: c.height, angle: bn && rn ? Math.atan2(ry / rn - by / bn, rx / rn - bx / bn) : null });
      }
      if (c.isConnected) capture.raf = requestAnimationFrame(sample);
    };
    capture.raf = requestAnimationFrame(sample);
  });
  const audioCalls = fixture.spoken.length;
  const plays = (await probe(page)).plays.length;
  releaseAsset();
  await expect(page.locator('.audli-mascot > svg')).toHaveCSS('visibility', 'hidden');
  const box = (await canvas.boundingBox())!;
  expect(box.width).toBeGreaterThan(width * .9);
  expect(box.height).toBeCloseTo(box.width, 0);
  expect(Math.abs(box.x + box.width / 2 - width / 2)).toBeLessThan(2);
  await page.screenshot({ path: `../docs/screenshots/AUD-65-celebration-${width}.png` });
  for (let i = 0; i < 35; i++) { await page.clock.runFor(100); await page.waitForTimeout(35); }
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  const samples = await page.evaluate(() => (window as unknown as { mascotCapture: MascotCapture }).mascotCapture.frames);
  expect(samples.length).toBeGreaterThan(10);
  for (const frame of samples) {
    // The scaled artboard is clipped to its presentation box; all artwork must stay inside it.
    const inset = frame.width * (10 / 120);
    expect(frame.bounds[0]).toBeGreaterThan(inset); expect(frame.bounds[1]).toBeGreaterThan(inset);
    expect(frame.bounds[2]).toBeLessThan(frame.width - inset); expect(frame.bounds[3]).toBeLessThan(frame.height - inset);
  }
  expect(samples[0].bounds[2] - samples[0].bounds[0]).toBeGreaterThan(width * .6);
  const angles = samples.map(frame => frame.angle).filter((angle): angle is number => angle !== null);
  let rotation = 0;
  for (let i = 1; i < angles.length; i++) { const delta = angles[i] - angles[i - 1]; rotation += Math.atan2(Math.sin(delta), Math.cos(delta)); }
  expect(Math.abs(rotation)).toBeGreaterThan(5.2); expect(Math.abs(rotation)).toBeLessThan(7.2);
  expect(fixture.spoken.length).toBe(audioCalls);
  expect((await probe(page)).plays.length).toBe(plays);
  await page.clock.runFor(5000);
  // Success stays until the learner chooses to leave.
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeInViewport();
  await page.screenshot({ path: `../docs/screenshots/AUD-65-results-${width}.png` });
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
  for(let i=0;i<15;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Great job!'}).count())break;}
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
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
  for(let i=0;i<25;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Great job!'}).count())break;}
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
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
  for(let i=0;i<25;i++){await page.clock.runFor(1000);await page.waitForTimeout(30);if(await page.getByRole('heading',{name:'Great job!'}).count())break;}
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
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


test('returning review remembers accents with one question and honestly acknowledges unsupported requests',async({page})=>{
  const fixture=await setup(page,true,true,undefined,false,false,true,'A Scottish accent please');
  await runToCompletion(page);
  const review=fixture.spoken.find(text=>text.startsWith('Last time'))!;
  expect(review).toContain('Australian English');expect(review.match(/\?/g)).toHaveLength(1);
  expect(fixture.spoken).toContain('I’ll remember that accent preference. Accent-specific recordings aren’t available yet, so today’s audio will use our usual voice.');
  expect(fixture.requests.filter(path=>path==='/api/lessons/structured/attempts')).toHaveLength(2);
  expect(fixture.phases).toContain('REVIEW_ACK');expect(fixture.phases).toContain('TRANSITION');
});

for (const skip of ['system preference', 'hung animation', 'app preference']) test(`Success shows the statistics at once with ${skip}`, async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await setup(page);
  if (skip === 'hung animation') await page.setViewportSize({ width: 320, height: 568 });
  if (skip === 'app preference') {
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('checkbox', { name: 'Reduce motion', exact: true }).check();
    await page.getByRole('button', { name: 'Home', exact: true }).click();
    await runToCompletion(page);
    await expect(page.locator('canvas')).toHaveCount(0);
    await expect(page.locator('.completion-summary')).toBeVisible();
    return;
  }
  let releaseAsset = () => {};
  const ready = new Promise<void>(resolve => { releaseAsset = resolve; });
  await page.route('**/audli/audli-mascot.riv', async route => { await ready; await route.abort(); });
  await page.clock.install();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
  for (let i = 0; i < 45; i++) {
    await page.clock.runFor(200); await page.waitForTimeout(30);
    if (await page.locator('.lesson-celebration').count()) break;
  }
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  // The animation is still initialising (or never will), yet nothing is hidden or waiting on it.
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeInViewport();
  if (skip === 'system preference') await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  releaseAsset();
  await page.clock.runFor(20000);
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await expect(page.locator('.completion-summary')).toBeVisible();
});
