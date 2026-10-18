import { test, expect } from '@playwright/test';
import { setup, typeReply } from './homework-fixture';

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

test('the retired completion-time preference offer is never requested, even if the server still offers it', async ({ page }) => {
  const state = await setup(page, false, { autoPlayback: false });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  let offers = 0, answers = 0;
  const plan = { revision: 1, checkin: { available: false } };
  const completed = { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, elapsed_seconds: 600 };
  await page.route('**/api/lessons/start', route => route.fulfill({ json: completed }));
  // Deliberately offer every time: the client must not ask, so duplicate completion cannot reopen preferences.
  await page.route('**/api/lessons/lesson/topic-checkin', route => { offers++; return route.fulfill({ json: { offered: true, plan: { ...plan, checkin: { available: true, prompt: 'Keep your topics?' } } } }); });
  await page.route('**/api/plan/checkin/**', route => { answers++; return route.fulfill({ json: plan }); });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).dblclick();
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  await expect(page.getByLabel('Your topic preference')).toHaveCount(0);
  await page.getByRole('button', { name: 'Return Home' }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).dblclick();
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  await expect(page.getByLabel('Your topic preference')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  expect(offers).toBe(0); expect(answers).toBe(0);
  expect(state.closings).toBe(0);
});

test('repeated audio endings and elapsed timers cannot dismiss the final card with assigned homework', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const state = await setup(page, true);
  await typeReply(page, 'yes please');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'STATS');
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  await expect.poll(() => state.confirmations).toBe(1);
  await page.locator('audio').evaluate(audio => { for (let i = 0; i < 3; i++) audio.dispatchEvent(new Event('ended')); });
  await page.clock.install();
  await page.clock.runFor(10 * 60 * 1000);
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeEnabled();
  expect(state.decisions).toBe(1); expect(state.closings).toBe(0);
});
