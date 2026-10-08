import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { installVoice, promptWav } from './voice-fixture';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';

test('real backend multi-exercise lesson plays each introduction and displays persisted topics', async ({ page }) => {
  test.skip(process.env.AUDLI_INTEGRATED_LESSON !== '1', 'Run separately using lesson-media.playwright.config.ts');
  await installVoice(page,{realPlayback:true});
  await page.addInitScript(() => {
    const blobs = new WeakMap<Blob,string>(), sources = new Map<string,string>();
    const trace: { event:string; path:string; time:number }[] = [];
    (window as unknown as {mediaTrace:typeof trace}).mediaTrace = trace;
    const fetch = window.fetch.bind(window), create = URL.createObjectURL.bind(URL);
    window.fetch = async (...args) => {
      const response = await fetch(...args), blob = response.blob.bind(response);
      response.blob = async () => {const data = await blob();blobs.set(data,String(args[0]));return data;};
      return response;
    };
    URL.createObjectURL = value => {const url = create(value);if(value instanceof Blob)sources.set(url,blobs.get(value)??'');return url;};
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function() {
      const path = sources.get(this.src)??this.src;
      this.addEventListener('playing',()=>trace.push({event:'playing',path,time:performance.now()}),{once:true});
      this.addEventListener('ended',()=>trace.push({event:'ended',path,time:performance.now()}),{once:true});
      return play.call(this);
    };
  });
  // Replace only microphone transport with a valid fixture WAV. All phase, exercise,
  // assessment, transcript and TTS requests reach the real API and persistence.
  const recording = promptWav();
  for(let sample=0;sample<(recording.length-44)/2;sample++) {
    recording.writeInt16LE(Math.round(3000*Math.sin(2*Math.PI*220*sample/8000)),44+sample*2);
  }
  await page.route('**/api/**/attempts', async route => {
    const request = route.request(), fields: Record<string,string> = {};
    for(const key of ['revision','followup_id']) {
      const match = request.postData()?.match(new RegExp(`name="${key}"\\r\\n\\r\\n([^\\r]+)`));
      if(match)fields[key]=match[1];
    }
    const response = await page.request.post(request.url(),{multipart:{...fields,
      audio:{name:'response.wav',mimeType:'audio/wav',buffer:recording}}});
    await route.fulfill({response});
  });
  const trace = () => page.evaluate(()=>(window as unknown as {mediaTrace:{event:string;path:string;time:number}[]}).mediaTrace);
  const snapshots: LessonCheckpoint[] = [];
  page.on('response', async response => {
    if(response.ok() && /\/api\/lessons\/[^/]+\/exercise$/.test(response.url()))snapshots.push(await response.json());
  });
  await page.goto('/');await page.getByRole('button',{name:'Start today’s session'}).click();
  for(const [index,topic] of ['Pottery making','Baking bread'].entries()) {
    await expect.poll(async()=>(await trace()).filter(item=>item.event==='playing'&&item.path.endsWith('/introduction-audio')).length,{timeout:150000}).toBe(index+1);
    await expect(page.getByRole('note',{name:'Exercise topic'})).toHaveText(`Topic: ${topic}`);
    await page.screenshot({path:`/tmp/aud22-topic-${index+1}-introduction.png`,fullPage:true});
    const value = snapshots.filter(item=>item.exercise)[index];
    expect(value.exercise!.topic).toBe(topic);
    expect((await page.request.get(`/api/exercises/${value.exercise!.id}/transcript`)).status()).toBe(403);
    await expect.poll(async()=>(await trace()).filter(item=>item.event==='playing'&&/\/exercises\/[^/]+\/audio$/.test(item.path)).length,{timeout:60000}).toBe(index+1);
    await expect(page.getByRole('note',{name:'Exercise topic'})).toHaveText(`Topic: ${topic}`);
    await page.screenshot({path:`/tmp/aud22-topic-${index+1}-listening.png`,fullPage:true});
  }
  await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible({timeout:150000});
  await expect(page.locator('.completion-count')).toHaveText('2');
  await expect(page.locator('.completion-results')).toContainText('The larger bag of flour reduced the cost');
  const media = await trace();
  const introductions = media.filter(item=>item.path.endsWith('/introduction-audio'));
  expect(introductions.map(item=>item.event)).toEqual(['playing','ended','playing','ended']);
  const coaching = media.filter(item=>item.event==='ended'&&item.path.endsWith('/coach-audio'));
  const passages = media.filter(item=>item.event==='playing'&&/\/exercises\/[^/]+\/audio$/.test(item.path));
  expect(passages).toHaveLength(2);
  expect(introductions[1].time).toBeLessThan(passages[0].time);
  expect(coaching[1].time).toBeLessThan(introductions[2].time); // summary cue then final coaching
  expect(introductions[3].time).toBeLessThan(passages[1].time);
  const completed = await page.request.get(`/api/lessons/${snapshots[0].id}`);
  expect((await completed.json()).exercises_completed).toBe(2);
  await writeFile('/tmp/aud22-native-media-trace.json',JSON.stringify({media,snapshots,completed:await completed.json()},null,2));
  await page.screenshot({path:'/tmp/aud22-native-completion.png',fullPage:true});
  await page.reload();await expect(page.getByRole('heading',{name:'Lesson completed'})).toBeVisible();
  expect(await trace()).toEqual([]); // completed introductions and closing never replay
});
