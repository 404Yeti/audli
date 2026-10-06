import { test, expect, type Page } from '@playwright/test';

const difficulty = { speech_rate: .75, duration_seconds: 45, vocabulary_level: 'simple', information_density: 2 };
async function setup(page: Page, denied = false) {
  await page.addInitScript(({ denied }) => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async () => {
      if (denied) throw new DOMException('Denied', 'NotAllowedError');
      return { getTracks: () => [{ stop() {} }] };
    } });
    class Recorder {
      static isTypeSupported() { return true; }
      state = 'inactive'; mimeType = 'audio/webm';
      ondataavailable: ((event: {data: Blob}) => void) | null = null;
      onstop: (() => void) | null = null;
      start() { this.state = 'recording'; }
      stop() {
        this.state = 'inactive';
        queueMicrotask(() => { this.ondataavailable?.({data:new Blob(['synthetic recording'],{type:this.mimeType})}); this.onstop?.(); });
      }
    }
    Object.defineProperty(window, 'MediaRecorder', {value:Recorder});
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
  }, { denied });
  let turn = 0;
  let failUpload = false;
  let phase = 'LISTENING';
  let cue: string | null = null;
  let result: object | null = null;
  const conversation = () => ({ state:phase, cue_id:cue, prompt: cue === 'feedback' ? 'You caught the main idea. We’ll keep the next clip at this pace.' : cue === 'followup' ? 'Why did the time change?' : cue ? 'Tell me what you understood.' : null, active_followup:cue === 'followup' ? {id:'followup',question:'Why did the time change?'} : null, pending_attempt:null, followups_asked:turn, result });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/profile')) return route.fulfill({json:{profile:{name:'Robert',goal:'work conversations',completed_attempts:0,difficulty},provider:'openai'}});
    if (path.endsWith('/current') || path === '/api/exercises') return route.fulfill({json:{id:'clip',audio_url:'/api/listening-audio',difficulty,completed_attempt_id:null}});
    if (path.endsWith('/conversation/listened')) { phase='AWAITING_SUMMARY';cue='summary';return route.fulfill({json:conversation()}); }
    if (path.endsWith('/conversation')) return route.fulfill({json:conversation()});
    if (path.endsWith('/coach-audio')) return route.fulfill({json:{audio_url:'/api/coach-voice'}});
    if (path.endsWith('/attempts')) {
      if (failUpload) { failUpload=false;return route.fulfill({status:503,json:{detail:'Upload failed. Your recording is safe; retry.'}}); }
      return route.fulfill({json:{id:'answer',transcription:{text:'The meeting time changed',confidence:.9,uncertainty:[],source:'openai'}}});
    }
    if (path.endsWith('/assess')) {
      turn++;
      if (turn === 1) { phase='AWAITING_FOLLOWUP';cue='followup'; }
      else { phase='GIVING_FEEDBACK';cue='feedback';result={evaluation:{overall:null,main_idea:1,details:null,vocabulary:null,inference:null,feedback:'Safe feedback',understood:['main idea'],insufficient_evidence:['reason'],misunderstood:[]},adaptation:{reason:'Incomplete evidence keeps difficulty stable.'}}; }
      return route.fulfill({json:conversation()});
    }
    if (path.endsWith('/ready')) { phase='READY_FOR_NEXT';return route.fulfill({json:conversation()}); }
    if (path.endsWith('/transcript')) return route.fulfill({json:{title:'Original passage',script:'Only available after assessment.'}});
    return route.fulfill({status:200,contentType:'audio/wav',body:Buffer.alloc(44)});
  });
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Start training'})).toBeEnabled();
  return { failNextUpload() { failUpload=true; } };
}
async function finishCue(page: Page) {
  const audio = page.getByLabel('Hear Audli’s message');
  await expect(audio).toBeVisible();
  await audio.evaluate(el => el.dispatchEvent(new Event('ended')));
}
async function listen(page: Page) {
  await page.getByRole('button',{name:'Start training'}).click();
  await page.getByRole('button',{name:'Listen to the lesson'}).click();
  await page.getByLabel('Play listening exercise').evaluate(el => el.dispatchEvent(new Event('ended')));
  await finishCue(page);
  await expect(page.getByRole('button',{name:'Finish',exact:true})).toBeVisible();
}
test('Home matches Figma geometry and contains only available navigation', async ({page}) => {
  await setup(page);
  await expect(page.locator('.lesson-card')).toHaveCSS('border-radius','26px');
  const card = await page.locator('.lesson-card').boundingBox();
  expect(card?.width).toBe(326);
  expect(card?.height).toBe(342);
  await expect(page.getByRole('button',{name:'Plan',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Settings',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Show transcript'})).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  const assets = await page.locator('main img').evaluateAll(images => images.map(image => ({src:(image as HTMLImageElement).getAttribute('src'),loaded:(image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0,width:image.getBoundingClientRect().width,height:image.getBoundingClientRect().height})));
  expect(assets.every(asset => asset.loaded)).toBe(true);
  expect(assets.find(asset => asset.src === '/audli/greeting.svg')?.width).toBeCloseTo(42.24,1);
  expect(assets.filter(asset => asset.src !== '/audli/greeting.svg').every(asset => asset.width === 20 && asset.height === 20)).toBe(true);
  await page.screenshot({path:'/tmp/audli-home.png',fullPage:true});
  await page.setViewportSize({width:320,height:740});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
});
test('automatic capture, retained upload retry, evidence follow-up, feedback and next', async ({page}) => {
  const session = await setup(page);
  await listen(page);
  session.failNextUpload();
  await page.getByRole('button',{name:'Finish',exact:true}).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('Upload failed');
  await expect(page.getByLabel('Review your recording')).toBeVisible();
  await page.getByRole('button',{name:'Retry transcription'}).click();
  await page.getByRole('button',{name:'That’s what I said'}).click();
  await expect(page.getByText('NOT ENOUGH EVIDENCE',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Show transcript'})).toHaveCount(0);
  await finishCue(page);
  await page.getByRole('button',{name:'Finish',exact:true}).click();
  await page.getByRole('button',{name:'That’s what I said'}).click();
  await expect(page.getByRole('button',{name:'Keep going'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Show transcript'})).toBeVisible();
  await finishCue(page);
  await page.getByRole('button',{name:'Keep going'}).click();
  await expect(page.getByRole('heading',{name:'Let’s train your ears.'})).toBeVisible();
});
test('Cancel discards recording and cue replay cannot restart it', async ({page}) => {
  await setup(page); await listen(page);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await finishCue(page);
  await expect(page.getByRole('button',{name:'Finish',exact:true})).toHaveCount(0);
  await expect(page.getByLabel('Review your recording')).toHaveCount(0);
  await page.getByRole('button',{name:'Start recording',exact:true}).click();
  await expect(page.getByRole('button',{name:'Finish',exact:true})).toBeVisible();
});
test('denied microphone offers retry and spoken file fallback', async ({page}) => {
  await setup(page,true);
  await page.getByRole('button',{name:'Start training'}).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('Microphone access was denied');
  await page.getByRole('button',{name:'Continue with an audio file'}).click();
  await page.getByRole('button',{name:'Listen to the lesson'}).click();
  await page.getByLabel('Play listening exercise').evaluate(el => el.dispatchEvent(new Event('ended')));
  await expect(page.getByText('Use an existing spoken recording')).toBeVisible();
});
test('reduced motion keeps SVG pieces at rest', async ({page}) => {
  await page.emulateMedia({reducedMotion:'reduce'});
  await setup(page);
  await page.getByRole('button',{name:'Start training'}).click();
  await page.getByRole('button',{name:'Listen to the lesson'}).click();
  await expect(page.locator('.piece.left')).toHaveCSS('transform','none');
});
test('119-second limit finishes and transcribes automatically', async ({page}) => {
  await setup(page);
  await page.clock.install();
  await listen(page);
  await page.clock.fastForward(119000);
  await expect(page.getByRole('heading',{name:'Did I hear you correctly?'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Finish',exact:true})).toHaveCount(0);
});
test('empty recognition is unknown and cannot be confirmed', async ({page}) => {
  await setup(page);
  await page.route('**/api/exercises/clip/attempts', route => route.fulfill({json:{id:'empty',transcription:{text:'',confidence:null,uncertainty:['Too little recognized speech.'],source:'openai'}}}));
  await listen(page);
  await page.getByRole('button',{name:'Finish',exact:true}).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('We didn’t hear anything');
  await expect(page.getByRole('button',{name:'That’s what I said'})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Show transcript'})).toHaveCount(0);
});
test('connection failure can be retried without replacing the lesson', async ({page}) => {
  await setup(page);
  await page.route('**/api/profile',route => route.fulfill({status:502,contentType:'text/html',body:'Unavailable'}));
  await page.reload();
  await expect(page.locator('main').getByRole('alert')).toContainText('could not reach the server');
  await page.unroute('**/api/profile');
  await page.getByRole('button',{name:'Retry connection'}).click();
  await expect(page.getByRole('button',{name:'Start training'})).toBeEnabled();
});
test('failed cue audio preserves the issued question and manual microphone control', async ({page}) => {
  await setup(page);
  await page.route('**/api/exercises/clip/coach-audio',route => route.fulfill({status:503,json:{detail:'Voice unavailable'}}));
  await page.getByRole('button',{name:'Start training'}).click();
  await page.getByRole('button',{name:'Listen to the lesson'}).click();
  await page.getByLabel('Play listening exercise').evaluate(el => el.dispatchEvent(new Event('ended')));
  await expect(page.getByText('Audli’s voice is unavailable.',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Retry audio'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Start recording',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Start recording',exact:true}).click();
  await expect(page.getByRole('button',{name:'Finish',exact:true})).toBeVisible();
});

test('Figma recording motion runs through the shared loop with local SVG geometry', async ({page}) => {
  await setup(page); await listen(page);
  await page.waitForTimeout(700);
  const first = await page.locator('.piece.left').evaluate(el => getComputedStyle(el).transform);
  await page.waitForTimeout(1200);
  const later = await page.locator('.piece.left').evaluate(el => getComputedStyle(el).transform);
  expect(first).not.toBe(later);
  await page.waitForTimeout(600);
  expect(await page.locator('.piece.left img').evaluate(el => el.getBoundingClientRect().width)).toBeGreaterThan(20);
  await page.screenshot({path:'/tmp/audli-recording.png',fullPage:true});
});
