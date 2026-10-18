import { test, expect } from '@playwright/test';
import { setup, typeReply } from './homework-fixture';
import { probe, promptWav } from './voice-fixture';

test('homework failure stays visible until an explicit action', async ({ page }) => {
  const state = await setup(page, true);
  await expect(page.getByLabel('Your reply', { exact: true })).toBeVisible();
  await page.route('**/api/homework/assignment/decision/text', route => route.abort('internetdisconnected'));
  await typeReply(page, 'yes please');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('.homework-recovery [role=alert]')).toContainText('connection');
  await page.waitForTimeout(1200);
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  expect(state.closings).toBe(0);
});

test('saved skipped homework remains visible without closing or navigation', async ({ page }) => {
  const state = await setup(page, true);
  await typeReply(page, 'no thanks');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  await page.waitForTimeout(1200);
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  expect(state.closings).toBe(0);
});

test('completion has no second standalone Nice work mascot', async ({ page }) => {
  await setup(page, true);
  await expect(page.locator('.lesson-wrapup')).toBeVisible();
  await expect(page.getByText('Nice work.', { exact: true })).toHaveCount(0);
});

test('answered preferences cannot re-enter after duplicate completion, rerender or remount', async ({ page }) => {
  const state = await setup(page, false, { autoPlayback: false });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  let offers = 0, answers = 0, spokenAnswers = 0;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const plan = { revision: 1, checkin: { available: true, prompt: 'Keep your topics or try something different?' } };
  const completed = { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, elapsed_seconds: 600 };
  await page.route('**/api/lessons/start', route => route.fulfill({ json: completed }));
  // Deliberately offer every time: client progression must also reject re-entry.
  await page.route('**/api/lessons/lesson/topic-checkin', route => { offers++; return route.fulfill({ json: { offered: true, plan } }); });
  await page.route('**/api/plan/checkin/audio', route => route.fulfill({ contentType: 'audio/wav', body: promptWav() }));
  await page.route('**/api/plan/checkin/text', async route => {
    answers++; await held;
    await route.fulfill({ json: { ...plan, revision: 2, checkin: { available: false } } });
  });
  await page.route('**/api/plan/checkin/attempts', route => { spokenAnswers++; return route.fulfill({ json: plan }); });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).dblclick();
  await expect(page.getByLabel('Your topic preference')).toBeVisible();
  await expect.poll(async () => (await probe(page)).plays.length).toBe(1);
  await page.getByLabel('Your topic preference').fill('science');
  await page.locator('.topic-checkin').getByRole('button', { name: 'Send', exact: true }).dblclick();
  await expect.poll(() => answers).toBe(1);
  await page.locator('audio').evaluate(audio => { for (let i = 0; i < 3; i++) audio.dispatchEvent(new Event('ended')); });
  await page.setViewportSize({ width: 393, height: 852 });
  await expect(page.getByLabel('Your topic preference')).toBeVisible();
  release();
  await expect(page.getByLabel('Your topic preference')).toHaveCount(0);
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  await page.getByRole('button', { name: 'Return Home' }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).dblclick();
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  await expect(page.getByLabel('Your topic preference')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  expect(offers).toBe(1); expect(answers).toBe(1); expect(spokenAnswers).toBe(0);
  expect(state.closings).toBe(0);
});

test('repeated audio endings and elapsed timers cannot dismiss assigned homework', async ({ page }) => {
  const state = await setup(page, true);
  await typeReply(page, 'yes please');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  await expect.poll(() => state.confirmations).toBe(1);
  await page.locator('audio').evaluate(audio => { for (let i = 0; i < 3; i++) audio.dispatchEvent(new Event('ended')); });
  await page.clock.install();
  await page.clock.runFor(60000);
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  expect(state.decisions).toBe(1); expect(state.closings).toBe(0);
  await page.getByRole('button', { name: 'Finish lesson', exact: true }).dblclick();
  await page.clock.runFor(25000);
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  expect(state.closings).toBe(1);
});
