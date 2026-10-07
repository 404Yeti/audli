import { test, expect, type Page } from '@playwright/test';
import { installVoice, promptWav, probe, endAudio } from './voice-fixture';

async function setup(page: Page, options: Parameters<typeof installVoice>[1] = {}) {
  await installVoice(page,options);
  let phase='LISTENING', cue:string|null=null, pending:object|null=null, result:object|null=null;
  let turns=0, id=1, completed=false, failUpload=false, uncertain=false, loseUpload=false;
  const requests:{path:string;body:unknown}[]=[];
  const exercise=()=>({id:'clip'+id,audio_url:'/api/exercises/clip'+id+'/audio',completed_attempt_id:completed?'answer':null});
  const state=()=>({state:phase,cue_id:cue,prompt:cue==='feedback'?'You caught the main idea. Next time, listen for the reasons.':cue==='followup'?'Why did the meeting time change?':cue?'Tell me what you understood.':null,active_followup:cue==='followup'?{id:'followup'}:null,pending_attempt:pending,result});
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname; requests.push({path,body:route.request().postData()});
    if(path==='/api/auth/config')return route.fulfill({json:{mode:'local'}});
    if(path==='/api/profile')return route.fulfill({json:{destination:'session_ready',profile:{name:'Robert',goal:'work conversations',completed_attempts:completed?1:0},provider:'openai'}});
    if(path==='/api/history')return route.fulfill({json:completed?[{id:'answer',exercise_id:'clip'+id,created_at:'2026-10-07T08:00:00Z',evaluation:{feedback:'You caught the main idea. Next time, listen for the reasons.'},adaptation:{focus:'details'}}]:[]});
    if(path==='/api/exercises/current')return route.fulfill({json:exercise()});
    if(path==='/api/exercises'){id++;phase='LISTENING';cue=null;pending=null;result=null;completed=false;turns=0;return route.fulfill({json:exercise()});}
    if(path.endsWith('/conversation/listened')){phase='AWAITING_SUMMARY';cue='summary';return route.fulfill({json:state()});}
    if(path.endsWith('/conversation'))return route.fulfill({json:state()});
    if(path.endsWith('/coach-audio'))return route.fulfill({json:{audio_url:'/api/coach-voice'}});
    if(path.endsWith('/attempts')){
      if(failUpload){failUpload=false;return route.fulfill({status:503,json:{detail:'Upload failed. Your recording is safe.'}});}
      pending={id:'answer'+turns,transcription:{text:'The meeting time changed',confidence:uncertain?.2:.9,uncertainty:uncertain?['Uncertain']:[],source:'openai'}};uncertain=false;
      if(loseUpload){loseUpload=false;return route.fulfill({status:503,json:{detail:'Response lost after recognition was saved.'}});}
      return route.fulfill({json:pending});
    }
    if(path.endsWith('/assess')){turns++;pending=null;if(turns===1){phase='AWAITING_FOLLOWUP';cue='followup';}else{phase='GIVING_FEEDBACK';cue='feedback';completed=true;result={evaluation:{feedback:'Safe final feedback'},adaptation:{reason:'Stable'}};}return route.fulfill({json:state()});}
    if(path.endsWith('/ready')){phase='READY_FOR_NEXT';completed=true;return route.fulfill({json:state()});}
    if(path.endsWith('/transcript'))return route.fulfill(completed?{json:{title:'Original passage',script:'Only after completed assessment.'}}:{status:403,json:{detail:'Complete assessment first.'}});
    return route.fulfill({contentType:'audio/wav',body:promptWav()});
  });
  await page.goto('/'); await expect(page.getByRole('button',{name:'Start today’s session'})).toBeEnabled();
  return {requests,failNextUpload(){failUpload=true;},loseNextUploadResponse(){loseUpload=true;},uncertainNext(){uncertain=true;}};
}
async function state(page:Page,value:string){await expect(page.locator('.session-shell .audli-mascot')).toHaveAttribute('data-state',value);}
async function beginListening(page:Page){
  await page.getByRole('button',{name:'Start today’s session'}).click();await state(page,'Speaking');
  await endAudio(page);await expect.poll(async()=>(await probe(page)).plays.length).toBe(2);await endAudio(page);await state(page,'Listening');
}
async function finishTurn(page:Page){await page.clock.runFor(2700);await state(page,'Speaking');}

test('approved Home is mascot-centered with four working destinations and no metrics',async({page})=>{
  await setup(page);await expect(page.getByRole('heading',{name:'Good to see you, Robert.'})).toBeVisible();
  await expect(page.locator('.audli-mascot svg')).toBeVisible();await expect(page.getByRole('button',{name:'Read transcript'})).toHaveCount(0);
  for(const [name,heading] of [['Review','What your ears are learning'],['Plan','Your listening plan'],['Settings','Settings']]){await page.getByRole('button',{name,exact:true}).click();await expect(page.getByRole('heading',{name:heading,exact:true})).toBeVisible();}
  await page.getByRole('button',{name:'Home',exact:true}).click();await page.setViewportSize({width:320,height:740});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(320);
  await page.screenshot({path:'/tmp/aud17-home.png',fullPage:true});
});
test('normal session completes with zero clicks after Start and automatically handles a follow-up',async({page})=>{
  const fixture=await setup(page,{autoPlayback:true});await page.clock.install();
  await page.getByRole('button',{name:'Start today’s session'}).click();await state(page,'Speaking');
  await page.clock.fastForward(600000);
  for(let i=0;i<16;i++){await page.clock.runFor(1000);await page.waitForTimeout(20);if(await page.getByRole('heading',{name:'Nice work today.'}).count())break;}
  await expect(page.getByRole('heading',{name:'Nice work today.'})).toBeVisible();
  expect((await probe(page)).clicks).toBe(1);expect(fixture.requests.filter(x=>x.path.endsWith('/assess'))).toHaveLength(2);
  expect(fixture.requests.some(x=>x.path.endsWith('/ready'))).toBe(true);await expect(page.locator('textarea')).toHaveCount(0);
});
test('Speaking Listening Thinking follow server cues and transcript stays gated through follow-up',async({page})=>{
  const fixture=await setup(page);await page.clock.install();let release:(()=>Promise<void>)|undefined;
  await page.route('**/api/attempts/answer0/assess',async route=>{await new Promise<void>(resolve=>{release=async()=>{await route.fallback();resolve();};});});
  await beginListening(page);await page.clock.runFor(2700);await state(page,'Thinking');await expect.poll(()=>!!release).toBe(true);
  await expect(page.getByRole('button',{name:'Read transcript'})).toHaveCount(0);await release!();await state(page,'Speaking');
  await endAudio(page);await state(page,'Listening');await finishTurn(page);
  expect(fixture.requests.filter(x=>x.path.endsWith('/assess'))).toHaveLength(2);
  await expect(page.locator('.session-transcript')).not.toHaveAttribute('open','');
  await page.locator('.session-transcript summary').click();await page.getByRole('button',{name:'Read transcript'}).click();await expect(page.getByText('Only after completed assessment.')).toBeVisible();
});
test('uncertain recognition speaks retry and listens again without exposing ASR or a form',async({page})=>{
  const fixture=await setup(page);fixture.uncertainNext();await page.clock.install();await beginListening(page);await finishTurn(page);
  expect(fixture.requests.filter(x=>x.path.endsWith('/assess'))).toHaveLength(0);expect(fixture.requests.some(x=>x.path==='/api/recognition/retry-audio')).toBe(true);
  await expect(page.locator('textarea')).toHaveCount(0);await expect(page.getByText('The meeting time changed',{exact:true})).toHaveCount(0);
  await endAudio(page);await state(page,'Listening');await finishTurn(page);expect(fixture.requests.filter(x=>x.path.endsWith('/assess'))).toHaveLength(1);
});
test('Replay rewinds current speech without duplicate transitions or assessment',async({page})=>{
  const fixture=await setup(page);await page.getByRole('button',{name:'Start today’s session'}).click();await state(page,'Speaking');
  const before=await probe(page);await page.getByRole('button',{name:'↻ Replay'}).click();expect((await probe(page)).plays).toEqual(before.plays);
  expect(fixture.requests.some(x=>x.path.endsWith('/conversation/listened'))).toBe(false);await endAudio(page);await expect.poll(async()=>(await probe(page)).plays.length).toBe(2);
});
test('End confirms exit, stops capture, and never uploads a discarded response',async({page})=>{
  const fixture=await setup(page);await page.clock.install();await beginListening(page);
  await page.getByRole('button',{name:'End',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'End session',exact:true}).click();
  await expect(page.getByRole('button',{name:'Start today’s session'})).toBeVisible();await page.clock.runFor(5000);
  expect((await probe(page)).tracks).toBeGreaterThan(1);expect(fixture.requests.some(x=>x.path.endsWith('/attempts'))).toBe(false);
  expect((await probe(page)).revoked).toEqual((await probe(page)).urls);
});
test('failed transcription retains the original recording for retry',async({page})=>{
  const fixture=await setup(page);await page.clock.install();await beginListening(page);fixture.failNextUpload();await page.clock.runFor(2700);
  await expect(page.locator('main').getByRole('alert')).toContainText('recording is safe');const count=(await probe(page)).recorders;
  await page.getByRole('button',{name:'Retry conversation'}).click();await state(page,'Speaking');await endAudio(page);await state(page,'Speaking');
  expect((await probe(page)).recorders).toBe(count);expect(fixture.requests.filter(x=>x.path.endsWith('/attempts'))).toHaveLength(2);
});
test('119 second recording limit remains authoritative for continuous voice',async({page})=>{
  const fixture=await setup(page,{continuousVoice:true});await page.clock.install();await beginListening(page);await page.clock.fastForward(119000);await state(page,'Speaking');
  expect(fixture.requests.filter(x=>x.path.endsWith('/attempts'))).toHaveLength(1);
});
test('countdown tracks actual elapsed time and no zero is shown before server completion',async({page})=>{
  await setup(page);await page.clock.install();await page.getByRole('button',{name:'Start today’s session'}).click();await state(page,'Speaking');
  await expect(page.getByLabel('Session time remaining')).toHaveText('10 min left');await page.clock.fastForward(60000);await expect(page.getByLabel('Session time remaining')).toHaveText('9 min left');
  await page.clock.fastForward(600000);await expect(page.getByLabel('Session time remaining')).toHaveText('Finishing this conversation');await expect(page.getByRole('heading',{name:'Nice work today.'})).toHaveCount(0);
});
test('refresh restores pending answer and original session timing without rerecording',async({page})=>{
  const fixture=await setup(page);await page.clock.install();await beginListening(page);
  await page.route('**/api/attempts/answer0/assess',route=>route.fulfill({status:503,json:{detail:'Assessment unavailable'}}));
  await page.clock.runFor(2700);await expect(page.locator('main').getByRole('alert')).toContainText('Assessment unavailable');await page.clock.fastForward(60000);
  await page.unroute('**/api/attempts/answer0/assess');await page.reload();await state(page,'Speaking');await expect(page.getByLabel('Session time remaining')).toHaveText('9 min left');
  expect(fixture.requests.filter(x=>x.path.endsWith('/attempts'))).toHaveLength(1);
});
test('denied microphone is recoverable and does not generate or start a session',async({page})=>{
  const fixture=await setup(page,{denied:true});await page.getByRole('button',{name:'Start today’s session'}).click();await expect(page.locator('main').getByRole('alert')).toContainText('Microphone access was denied');
  expect(fixture.requests.some(x=>x.path.endsWith('/conversation/listened'))).toBe(false);await expect(page.getByRole('button',{name:'Start today’s session'})).toBeEnabled();
});
test('TTS failure leaves the server-issued question recoverable',async({page})=>{
  const fixture=await setup(page);await page.route('**/api/exercises/clip1/coach-audio',route=>route.fulfill({status:503,json:{detail:'Voice unavailable'}}));
  await page.getByRole('button',{name:'Start today’s session'}).click();await state(page,'Speaking');await endAudio(page);await expect(page.locator('main').getByRole('alert')).toContainText('Voice unavailable');
  expect(fixture.requests.some(x=>x.path.endsWith('/assess'))).toBe(false);await expect(page.getByRole('button',{name:'Retry conversation'})).toBeVisible();
});
test('reduced motion keeps static poses and understandable state labels',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});await setup(page);await expect(page.locator('.mascot-body')).toHaveCSS('animation-name','none');
  await page.getByRole('button',{name:'Start today’s session'}).click();await state(page,'Speaking');await expect(page.getByRole('status')).toHaveText('Audli is speaking');await expect(page.locator('.sound-waves')).toHaveCSS('animation-name','none');
});
test('completed Review exposes only server-eligible transcript, with no score dashboard',async({page})=>{
  await setup(page);await page.clock.install();await beginListening(page);await finishTurn(page);await endAudio(page);await state(page,'Listening');await finishTurn(page);await page.clock.fastForward(600000);await endAudio(page);
  await expect(page.getByRole('heading',{name:'Nice work today.'})).toBeVisible();await page.getByRole('button',{name:'Review today'}).click();await page.getByRole('button',{name:'Read transcript'}).click();
  await expect(page.getByText('Only after completed assessment.')).toBeVisible();await expect(page.getByText('Observed comprehension:',{exact:false})).toHaveCount(0);
});
test('persisted learner-confirmed correction survives resuming an interrupted assessment',async({page})=>{
  await setup(page);let submitted:Record<string,unknown>|undefined;
  await page.route('**/api/exercises/clip1/conversation',route=>route.fulfill({json:{state:'ASSESSING',cue_id:'summary',prompt:'Tell me what you understood.',active_followup:null,result:null,pending_attempt:{id:'saved',transcription:{text:'ASR mistake',confidence:.9,uncertainty:[]},confirmed_text:'The learner explicitly corrected this before interruption.'}}}));
  await page.route('**/api/attempts/saved/assess',route=>{submitted=route.request().postDataJSON();return route.fulfill({status:503,json:{detail:'Provider unavailable'}});});
  await page.getByRole('button',{name:'Start today’s session'}).click();await expect.poll(()=>submitted?.text).toBe('The learner explicitly corrected this before interruption.');expect(submitted?.hands_free).toBe(false);await expect(page.locator('textarea')).toHaveCount(0);
});
test('exit confirmation traps keyboard focus and Escape restores End focus',async({page})=>{
  await setup(page);await page.getByRole('button',{name:'Start today’s session'}).click();await state(page,'Speaking');await page.getByRole('button',{name:'End',exact:true}).click();
  await expect(page.getByRole('button',{name:'End session',exact:true})).toBeFocused();await page.keyboard.press('Shift+Tab');await expect(page.getByRole('button',{name:'Stay here'})).toBeFocused();await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'End session',exact:true})).toBeFocused();await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'End',exact:true})).toBeFocused();
});
test('lost transcription response resumes saved answer and records fresh audio for the follow-up',async({page})=>{
  const fixture=await setup(page);fixture.loseNextUploadResponse();await page.clock.install();await beginListening(page);await page.clock.runFor(2700);await expect(page.locator('main').getByRole('alert')).toContainText('recognition was saved');
  await page.getByRole('button',{name:'Retry conversation'}).click();await state(page,'Speaking');await endAudio(page);await state(page,'Listening');await finishTurn(page);
  expect((await probe(page)).recorders).toBe(2);expect(fixture.requests.filter(x=>x.path.endsWith('/attempts'))).toHaveLength(2);expect(fixture.requests.filter(x=>x.path.endsWith('/assess'))).toHaveLength(2);
});
test('End cancels a delayed assessment continuation and discards late coaching',async({page})=>{
  const fixture=await setup(page);await page.clock.install();let release:(()=>Promise<void>)|undefined;
  await page.route('**/api/attempts/answer0/assess',async route=>{await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({json:{state:'GIVING_FEEDBACK',cue_id:'feedback',prompt:'Late coaching',result:{},pending_attempt:null,active_followup:null}}).catch(()=>{});resolve();};});});
  await beginListening(page);await page.clock.runFor(2700);await expect.poll(()=>!!release).toBe(true);await page.getByRole('button',{name:'End',exact:true}).click();await page.getByRole('button',{name:'End session',exact:true}).click();
  const count=fixture.requests.length;await release!();await page.waitForTimeout(150);expect(fixture.requests.length).toBe(count);await expect(page.getByRole('button',{name:'Start today’s session'})).toBeVisible();await expect(page.getByText('Late coaching')).toHaveCount(0);expect((await probe(page)).revoked).toEqual((await probe(page)).urls);
});
