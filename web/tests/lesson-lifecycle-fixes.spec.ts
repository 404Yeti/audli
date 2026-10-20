import { test, expect } from '@playwright/test';
import { setup, typeReply } from './homework-fixture';
import { endAudio, probe, promptWav } from './voice-fixture';

for (const width of [320,390]) test(`inline reply and explicit completion stay quiet at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:844});
  await page.emulateMedia({reducedMotion:'reduce'});
  const state=await setup(page,true,{autoPlayback:false});
  await expect(page.getByLabel('Your reply',{exact:true})).toBeVisible();
  await expect(page.locator('.typed-alternative, .wrapup-summary')).toHaveCount(0);
  await page.screenshot({path:`../docs/screenshots/AUD-63-inline-reply-${width}.png`,fullPage:true});
  await typeReply(page,'yes please');await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect.poll(()=>state.confirmations).toBe(1);
  // The celebration waits for the confirmation to finish playing.
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase','HOMEWORK');
  expect(state.closings).toBe(0);
  await endAudio(page);
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase','SUCCESS');
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Hear assignment'})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Finish lesson'})).toHaveCount(0);
  await expect(page.getByRole('link',{name:'Watch on YouTube (opens a new tab)'})).toBeVisible();
  expect(state.decisions).toBe(1);expect((await probe(page)).overlaps).toBe(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.reload();await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
  expect(state.closings).toBe(0);expect(state.confirmations).toBe(1);
});

for(const outcome of ['decline','discovery failure','confirmation audio failure','skip recording'] as const)test(`the celebration waits for the homework decision: ${outcome}`,async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});
  const state=await setup(page,true,{continuousVoice:true});
  if(outcome==='confirmation audio failure')await page.route('**/api/homework/assignment/confirmation-audio',route=>route.fulfill({status:503}));
  if(outcome==='discovery failure')await page.route('**/api/homework/assignment/decision/text',route=>route.abort('internetdisconnected'));
  if(outcome==='skip recording'){
    await page.getByRole('button',{name:'Speak',exact:true}).click();
    await expect(page.getByText('Listening for your choice…')).toBeVisible();
    await page.getByRole('button',{name:'Skip optional listening'}).click();
  }else{
    await typeReply(page,outcome==='confirmation audio failure'?'yes please':'no thanks');await page.getByRole('button',{name:'Send',exact:true}).click();
  }
  if(outcome==='discovery failure'||outcome==='skip recording'){
    // No decision exists: the query stays until the learner decides or explicitly skips.
    await page.waitForTimeout(1500);
    await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase','HOMEWORK');
    await expect(page.getByRole('heading',{name:'Great job!'})).toHaveCount(0);
    await page.getByRole('button',{name:'Skip homework'}).click();
  }
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase','SUCCESS');
  await expect(page.getByRole('heading',{name:'Great job!'})).toBeVisible();
  expect((await probe(page)).overlaps).toBe(0);expect(state.decisions).toBeLessThanOrEqual(1);
  await expect(page.getByRole('button',{name:'Return Home'})).toBeEnabled();
});

test('previous homework review precedes the topic and typed replies cancel only their own recording',async({page})=>{
  await setup(page,false,{continuousVoice:true});
  let phase='watched',revision=0,asked=0,transition=0;
  const view=()=>({phase,revision,prompt:phase==='watched'?'Did you watch your previous optional listening?':phase==='summary'?'What main idea stayed with you?':'What detail do you remember?'});
  await page.route('**/api/lessons/start',route=>route.fulfill({json:{id:'next',revision:0,status:'active',phase:'TRANSITION',prompt:'Today’s topic is science.',elapsed_seconds:0,focus:'details'}}));
  await page.route('**/api/lessons/next/homework-review',route=>route.fulfill({json:view()}));
  await page.route('**/api/lessons/next/homework-review/audio',route=>{asked++;return route.fulfill({contentType:'audio/wav',body:promptWav()});});
  await page.route('**/api/lessons/next/homework-review/text',route=>{phase=phase==='watched'?'summary':phase==='summary'?'detail':'done';revision++;return route.fulfill({json:view()});});
  await page.route('**/api/lessons/next/audio',route=>{transition++;return route.fulfill({contentType:'audio/wav',body:promptWav()});});
  await page.route('**/api/lessons/next/heard',route=>route.fulfill({json:{id:'next',revision:1,status:'completed',phase:'COMPLETED',focus:'details',exercises_completed:0}}));
  await page.getByRole('button',{name:'Home',exact:true}).click();await page.getByRole('button',{name:'Start today’s session'}).click();
  for(const reply of ['yes','It was about science.','I remember a useful example.']){
    await expect(page.getByRole('status').filter({hasText:'Listening to you'})).toBeVisible();
    expect(transition).toBe(0);
    await page.getByLabel('Your reply',{exact:true}).fill(reply);await page.getByRole('button',{name:'Send',exact:true}).click();
  }
  await expect.poll(()=>transition).toBe(1);expect(asked).toBe(3);expect((await probe(page)).overlaps).toBe(0);
});
