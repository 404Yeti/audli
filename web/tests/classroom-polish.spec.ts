import { test, expect } from '@playwright/test';
import { setup, typeReply } from './homework-fixture';
import { probe, promptWav } from './voice-fixture';

for (const width of [320,390,1440]) test(`adaptive classroom modes preserve audio at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:844});
  const state=await setup(page,true);
  const shell=page.locator('.session-shell');
  await expect(shell).toHaveAttribute('data-layout','interaction');
  await expect(page.getByText('Type instead',{exact:true})).toBeVisible();
  await expect(page.getByLabel('Your reply',{exact:true})).toBeHidden();
  await page.screenshot({path:`../docs/screenshots/AUD-62-interaction-${width}.png`,fullPage:true});
  await page.evaluate(()=>{(window as unknown as {classroomAudio:Element|null}).classroomAudio=document.querySelector('audio');});
  let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});let attempts=0;
  await page.route('**/api/homework/assignment/decision/attempts',async route=>{attempts++;await held;await route.fallback();});
  await page.getByRole('button',{name:'Speak',exact:true}).click();
  await expect.poll(()=>attempts).toBe(1);
  await expect(shell).toHaveAttribute('data-layout','focus');
  await expect(page.getByText('Type instead',{exact:true})).toBeVisible();
  await expect(page.locator('.homework-invitation')).toHaveCount(0);
  await page.screenshot({path:`../docs/screenshots/AUD-62-focus-${width}.png`,fullPage:true});
  release();
  await expect(shell).toHaveAttribute('data-layout','content');
  await expect(page.getByRole('button',{name:'Hear assignment'})).toBeEnabled();
  const link=page.getByRole('link',{name:'Watch on YouTube (opens a new tab)'});
  await expect(link).toBeVisible();await link.focus();await expect(link).toBeFocused();
  expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.screenshot({path:`../docs/screenshots/AUD-62-content-${width}.png`,fullPage:true});
  expect(await page.evaluate(()=>document.querySelector('audio')===(window as unknown as {classroomAudio:Element}).classroomAudio)).toBe(true);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect((await probe(page)).overlaps).toBe(0);expect(state.decisions).toBe(1);expect(state.confirmations).toBe(1);
});

test('uncertain speech gets one spoken clarification and one bounded voice correction',async({page})=>{
  const state=await setup(page,true);let attempts=0,clarifications=0;
  await page.route('**/api/homework/assignment/clarification-audio',route=>{clarifications++;return route.fulfill({contentType:'audio/wav',body:promptWav()});});
  await page.route('**/api/homework/assignment/decision/attempts',route=>++attempts===1?route.fulfill({status:422,json:{detail:'I didn’t catch your choice.'}}):route.fallback());
  await page.getByRole('button',{name:'Speak',exact:true}).click();
  await expect.poll(()=>state.confirmations,{timeout:12000}).toBe(1);expect(attempts).toBe(2);expect(clarifications).toBe(1);expect(state.decisions).toBe(1);
  expect((await probe(page)).overlaps).toBe(0);
});

test('repeated ambiguity stays optional with typed correction and no repeated spoken prompt',async({page})=>{
  const state=await setup(page,true);let attempts=0,clarifications=0;
  await page.route('**/api/homework/assignment/clarification-audio',route=>{clarifications++;return route.fulfill({contentType:'audio/wav',body:promptWav()});});
  await page.route('**/api/homework/assignment/decision/attempts',route=>{attempts++;return route.fulfill({status:422,json:{detail:'Please try your choice again or type it. Your lesson is saved.'}});});
  await page.getByRole('button',{name:'Speak',exact:true}).click();
  await expect(page.locator('.homework-card').getByRole('alert')).toContainText('Your lesson is saved',{timeout:12000});expect(attempts).toBe(2);
  await page.getByRole('button',{name:'Speak',exact:true}).click();
  await expect.poll(()=>attempts).toBe(3);await expect(page.getByRole('button',{name:'Speak',exact:true})).toBeVisible();
  expect(clarifications).toBe(1);expect(state.decisions).toBe(0);
  await typeReply(page,'no thanks');await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
});

test('backup tutor voice is disclosed without another provider request',async({page})=>{
  await setup(page,true);let audio=0;
  await page.route('**/api/homework/assignment/invitation-audio',route=>{audio++;return route.fulfill({contentType:'audio/wav',headers:{'X-Audli-Tutor-Fallback':'true','X-Audli-Tutor-Provider':'openai'},body:promptWav()});});
  await page.getByRole('button',{name:'Replay homework invitation'}).click();
  await expect(page.getByText('Backup voice used',{exact:true})).toBeVisible();
  await page.getByText('Backup voice used',{exact:true}).click();
  await expect(page.getByRole('status').filter({hasText:'usual voice was temporarily unavailable'})).toBeVisible();expect(audio).toBe(1);
});

test('large text, long titles and reduced motion preserve a reachable verified link',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});await page.setViewportSize({width:320,height:568});
  const state=await setup(page);state.status='assigned';
  await page.reload();await page.getByRole('button',{name:'Plan',exact:true}).click();
  await page.addStyleTag({content:'html { font-size: 200%; }'});
  await page.locator('.assignment-content h3').evaluate(node=>{node.textContent='Cybersecurityandverylongunbrokentitle'.repeat(20);});
  const link=page.getByRole('link',{name:'Watch on YouTube (opens a new tab)'});await link.scrollIntoViewIfNeeded();await link.focus();await expect(link).toBeFocused();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect((await page.locator('.assignment-content h3').boundingBox())!.height).toBeLessThanOrEqual(3*48+1);
});

test('WebKit records final AAC chunks and replays synthetic audio after a user gesture',async({page,browserName})=>{
  test.skip(browserName!=='webkit','Native Safari-oriented codec check runs in the WebKit project.');
  await page.route('**/native-audio-check',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="en"><title>Synthetic audio check</title><body><button>Record synthetic audio</button><audio controls playsinline></audio></body></html>'}));
  await page.goto('/native-audio-check');
  const aac = await page.evaluate(() => typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported('audio/mp4'));
  test.skip(!aac, 'This WebKit host cannot encode AAC; native iPhone codec acceptance remains pending.');
  await page.evaluate(()=>{
    const button=document.querySelector('button')!,audio=document.querySelector('audio')!;
    button.onclick=async()=>{
      const context=new AudioContext();await context.resume();
      const destination=context.createMediaStreamDestination(),oscillator=context.createOscillator(),gain=context.createGain();
      gain.gain.value=.05;oscillator.connect(gain);gain.connect(destination);
      const recorder=new MediaRecorder(destination.stream,{mimeType:'audio/mp4'}),chunks:Blob[]=[];
      recorder.ondataavailable=event=>{if(event.data.size)chunks.push(event.data);};
      recorder.onstop=()=>{
        const blob=new Blob(chunks,{type:recorder.mimeType});
        document.body.dataset.bytes=String(blob.size);document.body.dataset.mime=blob.type;
        const url=URL.createObjectURL(blob);audio.src=url;
        button.textContent='Play captured audio';button.onclick=()=>{void audio.play();};
        audio.onended=()=>{document.body.dataset.played='true';URL.revokeObjectURL(url);};
        oscillator.stop();destination.stream.getTracks().forEach(track=>track.stop());void context.close();
      };
      recorder.start(100);oscillator.start();setTimeout(()=>recorder.stop(),700);
    };
  });
  await page.getByRole('button',{name:'Record synthetic audio'}).click();
  await expect(page.getByRole('button',{name:'Play captured audio'})).toBeVisible();
  expect(await page.locator('body').getAttribute('data-mime')).toContain('audio/mp4');
  expect(Number(await page.locator('body').getAttribute('data-bytes'))).toBeGreaterThan(100);
  await page.getByRole('button',{name:'Play captured audio'}).click();
  await expect(page.locator('body')).toHaveAttribute('data-played','true');
});

test('reduced motion disables shared layout transitions without disabling voice',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});await setup(page,true);
  await expect(page.locator('.session-presence')).toHaveCSS('transition-duration','0s');
  await expect(page.locator('.mascot-body')).toHaveCSS('animation-name','none');
  await page.getByRole('button',{name:'Speak',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Science in everyday life'})).toBeVisible({timeout:10000});
  await expect(page.locator('audio')).toHaveCount(1);expect((await probe(page)).overlaps).toBe(0);
});
