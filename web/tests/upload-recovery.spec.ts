import { test, expect } from '@playwright/test';
import { setupDelivery, until, FAST_POLICY } from './delivery-fixture';
import { probe } from './voice-fixture';

async function begin(page: Parameters<typeof setupDelivery>[0]) {
  await page.clock.install();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
}
const failure = (page: Parameters<typeof setupDelivery>[0]) => page.locator('[data-delivery="failed"]');

test('repeated failures retain one exercise ID; Send again and replay do not regenerate TTS', async ({ page }) => {
  const f = await setupDelivery(page, 'exercise', ['down','down','down','ok']);
  await begin(page);
  await until(page, () => failure(page).isVisible());
  expect(f.uploadRequests).toHaveLength(3);
  const id = f.uploadRequests[0].uploadId;
  expect(id).toMatch(/^[a-f0-9]{16}$/);
  expect(f.uploadRequests.every(request => request.uploadId === id)).toBe(true);
  await expect(failure(page)).toContainText('while this page stays open');
  const audioBefore = f.audioRequests(), plays = (await probe(page)).plays.length;
  await page.getByRole('button', { name: 'Replay question', exact: true }).click();
  await until(page, async () => (await probe(page)).plays.length > plays);
  expect(f.audioRequests()).toBe(audioBefore);
  const before = f.uploadRequests.length;
  await page.getByRole('button', { name: 'Send again', exact: true }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await until(page, () => f.uploadRequests.length > before);
  expect(f.uploadRequests.length).toBe(before + 1);
  expect(f.uploadRequests.at(-1)?.uploadId).toBe(id);
  expect(f.transcriptions).toBe(1);
  expect(f.count('POST', '/coach-audio')).toBe(1);
});

test('lost check-in response stays ambiguous, and Send again never overwrites it', async ({ page }) => {
  const f = await setupDelivery(page, 'checkin', ['lost','ok']);
  await begin(page);
  await until(page, () => failure(page).isVisible());
  expect(f.uploadRequests).toHaveLength(1); expect(f.transcriptions).toBe(1);
  const audioBefore=f.audioRequests();
  await expect(page.getByRole('button',{name:'Resume saved lesson'})).toBeVisible();
  await page.getByRole('button',{name:'Send again',exact:true}).click();
  await until(page, () => failure(page).isVisible());
  expect(f.uploadRequests).toHaveLength(1); expect(f.transcriptions).toBe(1); expect(f.audioRequests()).toBe(audioBefore);
  expect(f.count('POST','/answer')).toBe(0);
});

test('Record again starts a new capture without replaying the check-in question', async ({ page }) => {
  const f = await setupDelivery(page,'checkin',['down','down','down','ok']);
  await begin(page); await until(page,()=>failure(page).isVisible());
  const audioBefore=f.audioRequests(), recorded=(await probe(page)).recorders;
  await page.getByRole('button',{name:'Record again',exact:true}).click();
  await until(page,async()=>(await probe(page)).recorders>recorded);
  expect(f.audioRequests()).toBe(audioBefore);
  expect(f.uploadRequests).toHaveLength(3);
});

test('pagehide stops delivery; pageshow retains audio for explicit Send again', async ({ page }) => {
  const f=await setupDelivery(page,'exercise',['hang','ok']);
  await begin(page); await until(page,()=>f.uploadRequests.length===1);
  await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
  await until(page,()=>failure(page).isVisible());
  await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
  expect(f.uploadRequests).toHaveLength(1);
  await page.getByRole('button',{name:'Send again',exact:true}).click();
  await until(page,()=>f.uploadRequests.length===2);
  expect(f.uploadRequests[1].uploadId).toBe(f.uploadRequests[0].uploadId);
});

test('reload explains the lost in-memory recording before replay or capture', async ({ page }) => {
  const f=await setupDelivery(page,'checkin',['hang']);
  await begin(page); await until(page,()=>f.uploadRequests.length===1);
  const audioBefore=f.audioRequests();
  await page.reload();
  await expect(page.locator('[data-delivery="unsent"]')).toContainText('recording is no longer available');
  expect(f.audioRequests()).toBe(audioBefore);
  expect((await probe(page)).recorders).toBe(0);
});

test('disabled policy keeps the legacy single-send recovery', async ({ page }) => {
  const f=await setupDelivery(page,'checkin',['down'],{policy:{enabled:false}});
  await begin(page);
  await until(page,()=>page.getByRole('button',{name:'Retry conversation',exact:true}).isVisible());
  expect(f.uploadRequests).toHaveLength(1);
  await expect(failure(page)).toHaveCount(0);
});


test('slow sends and bounded retry waits have separate polite progress states', async ({ page }) => {
  const f=await setupDelivery(page,'exercise',['hang'],{policy:{...FAST_POLICY,backoff_ms:[3000,3000]}});
  await begin(page);
  await until(page,()=>page.locator('[data-delivery="sending"]').isVisible());
  await expect(page.locator('[data-delivery="sending"]')).toContainText('Sending your answer');
  await until(page,()=>page.locator('[data-delivery="retrying"]').isVisible());
  await expect(page.locator('[data-delivery="retrying"]')).toContainText('2 of 3');
  expect(f.uploadRequests).toHaveLength(1);
});

test('another writer at exactly R+1 is never treated as our check-in commit', async ({ page }) => {
  let changed=false;
  const f=await setupDelivery(page,'checkin',['down'],{otherWriter:()=>{
    if(changed)return;changed=true;f.lesson.revision++;f.lesson.pending={text:'A different answer',confidence:.99,uncertainty:[]} as never;
  }});
  await begin(page);await until(page,()=>failure(page).isVisible());
  expect(f.uploadRequests).toHaveLength(1);expect(f.transcriptions).toBe(0);
  expect(f.count('POST','/answer')).toBe(0);
});
