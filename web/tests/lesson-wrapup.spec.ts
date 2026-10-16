import { test, expect } from '@playwright/test';
import { setup } from './homework-fixture';
import { probe, promptWav } from './voice-fixture';

for (const width of [320, 390, 1440]) test(`inline lesson wrap-up and typed topic choice at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 950 });
  const state = await setup(page, true);
  await expect(page.locator('.lesson-wrapup')).toBeVisible();
  await expect(page.getByLabel('Your reply', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Find optional listening' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Hear invitation' })).toHaveCount(0);
  await expect(page.locator('audio')).toHaveCount(1);
  await page.screenshot({ path: `../docs/screenshots/AUD-61-wrapup-offer-${width}.png`, fullPage: true });
  await page.getByLabel('Your reply', { exact: true }).fill('yes about science');
  await page.getByRole('button', { name: 'Send', exact: true }).focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  await expect.poll(() => state.confirmations).toBe(1);
  expect(state.decisions).toBe(1); expect((await probe(page)).overlaps).toBe(0);
  await page.screenshot({ path: `../docs/screenshots/AUD-61-wrapup-assigned-${width}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload(); await expect(page.locator('.lesson-wrapup')).toBeVisible();
  expect(state.confirmations).toBe(1); expect(state.spoken).toBe(0);
  await page.getByRole('button', { name: 'Return Home' }).click();
  await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Watch on YouTube (opens a new tab)' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Next three lessons' })).toBeVisible();
});

test('typed refusal interrupts the microphone without submitting duplicate speech', async ({ page }) => {
  const state = await setup(page, true);
  await page.getByRole('button', { name: 'Speak', exact: true }).click();
  await expect(page.getByText('Listening for your choice…')).toBeVisible();
  await page.getByLabel('Your reply', { exact: true }).fill('no thanks');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  expect(state.voiceRequests).toBe(0); expect(state.decisions).toBe(1);
  expect((await probe(page)).tracks).toBe(1); expect((await probe(page)).overlaps).toBe(0);
});

test('three-lesson topic check-in precedes homework once; reload never speaks it', async ({ page }) => {
  const state = await setup(page);
  let offers = 0, answers = 0;
  const plan = { revision: 1, checkin: { available: true, after_lesson: 3, prompt: 'Would you like to keep your listening topics, or try something different?', pending: null } };
  await page.route('**/api/lessons/lesson/topic-checkin', route => { offers++; return route.fulfill({ json: { offered: offers === 1, plan } }); });
  await page.route('**/api/plan/checkin/audio', route => route.fulfill({ contentType: 'audio/wav', body: promptWav() }));
  await page.route('**/api/plan/checkin/attempts', route => route.fulfill({ json: { ...plan, revision: 2 } }));
  await page.route('**/api/plan/checkin/answer', route => { answers++; return route.fulfill({ json: { ...plan, checkin: { available: false } } }); });
  await page.route('**/api/lessons/start', route => route.fulfill({ json: { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, elapsed_seconds: 600, encouragement: 'Thanks for listening.' } }));
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
  await expect.poll(() => answers).toBe(1);
  await expect.poll(() => state.confirmations).toBe(1);
  expect(offers).toBe(1); expect(state.voiceRequests).toBe(1);
  expect((await probe(page)).overlaps).toBe(0);
  await page.reload(); await expect(page.locator('.lesson-wrapup')).toBeVisible();
  expect(offers).toBe(1); expect(answers).toBe(1); expect(state.spoken).toBe(1);
});

test('network failure leaves compact recovery and saved lesson available', async ({ page }) => {
  await setup(page, true);
  await page.route('**/api/homework/assignment/decision/text', route => route.abort('internetdisconnected'));
  await page.getByLabel('Your reply', { exact: true }).fill('yes please');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('.homework-card').getByRole('alert')).toContainText('your lesson is saved');
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Reload saved assignment' })).toBeEnabled();
});
