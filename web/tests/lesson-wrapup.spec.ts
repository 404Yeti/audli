import { test, expect } from '@playwright/test';
import { setup, typeReply } from './homework-fixture';
import { probe, promptWav } from './voice-fixture';

for (const width of [320, 390, 1440]) test(`inline lesson wrap-up and typed topic choice at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 950 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const state = await setup(page, true);
  await expect(page.locator('.lesson-wrapup')).toBeVisible();
  await expect(page.getByLabel('Your reply', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Find optional listening' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Hear invitation' })).toHaveCount(0);
  await expect(page.locator('audio')).toHaveCount(1);
  await page.screenshot({ path: `../docs/screenshots/AUD-61-wrapup-offer-${width}.png`, fullPage: true });
  await typeReply(page, 'yes about science');
  await page.getByRole('button', { name: 'Send', exact: true }).focus(); await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'SUCCESS');
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  await expect.poll(() => state.confirmations).toBe(1);
  await expect(page.getByRole('button', { name: 'Hear assignment' })).toBeEnabled();
  expect(state.decisions).toBe(1); expect((await probe(page)).overlaps).toBe(0);
  // Capture the settled hands-free layout; keyboard focus is covered separately.
  await page.locator('.homework-card').evaluate(node => (node as HTMLElement).blur());
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
  await typeReply(page, 'no thanks');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  expect(state.voiceRequests).toBe(0); expect(state.decisions).toBe(1);
  expect((await probe(page)).tracks).toBe(1); expect((await probe(page)).overlaps).toBe(0);
});

test('three-lesson topic check-in is asked in setup, before the lesson; completion and reload never ask it', async ({ page }) => {
  const state = await setup(page);
  let offers = 0, answers = 0, due = true;
  const plan = () => ({ revision: 1, checkin: { available: due, after_lesson: 3, prompt: 'Would you like to keep your listening topics, or try something different?', pending: null } });
  await page.route('**/api/plan', route => route.fulfill({ json: plan() }));
  await page.route('**/api/lessons/lesson/topic-checkin', route => { offers++; return route.fulfill({ json: { offered: true, plan: plan() } }); });
  await page.route('**/api/plan/checkin/audio', route => route.fulfill({ contentType: 'audio/wav', body: promptWav() }));
  await page.route('**/api/plan/checkin/attempts', route => route.fulfill({ json: { ...plan(), revision: 2 } }));
  await page.route('**/api/plan/checkin/answer', route => { answers++; due = false; return route.fulfill({ json: plan() }); });
  const lessonStarts: string[] = [];
  await page.route('**/api/lessons/start', route => { lessonStarts.push(due ? 'unresolved' : 'resolved'); return route.fulfill({ json: { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, elapsed_seconds: 600, encouragement: 'Thanks for listening.' } }); });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
  await expect.poll(() => answers).toBe(1);
  await expect(page.locator('.lesson-wrapup')).toBeVisible({ timeout: 15000 });
  expect(lessonStarts).toEqual(['resolved']); // The lesson begins only after the preference is resolved.
  await expect.poll(() => state.confirmations).toBe(1);
  expect(offers).toBe(0); expect(state.voiceRequests).toBe(1);
  expect((await probe(page)).overlaps).toBe(0);
  await page.reload(); await expect(page.locator('.lesson-wrapup')).toBeVisible();
  expect(offers).toBe(0); expect(answers).toBe(1); expect(state.spoken).toBe(1);
});

test('network failure leaves compact recovery and saved lesson available', async ({ page }) => {
  await setup(page, true);
  await page.route('**/api/homework/assignment/decision/text', route => route.abort('internetdisconnected'));
  await typeReply(page, 'yes please');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Lesson completed|Great job!/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeEnabled();
});

test('repeated typed submission joins one saved decision without duplicate requests', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const state = await setup(page, true);
  let decisions = 0;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/homework/assignment/decision/text', async route => {
    decisions++; await held; state.status = 'skipped'; // The server persists the decision.
    await route.fulfill({ json: { id: 'assignment', lesson_id: 'lesson', status: 'skipped', objective: 'Listen.' } });
  });
  await typeReply(page, 'no thanks');
  await page.getByRole('button', { name: 'Send', exact: true }).dblclick();
  await expect.poll(() => decisions).toBe(1);
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  release();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  expect(decisions).toBe(1);
});

test('completion dismisses a pending End dialog before the inline wrap-up', async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const completed = { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, elapsed_seconds: 600, encouragement: 'Thanks for listening.' };
  await page.route('**/api/lessons/start', route => route.fulfill({ json: { ...completed, phase: 'CLOSING', status: 'active', revision: 1, prompt: 'Thanks for listening today.' } }));
  await page.route('**/api/lessons/lesson/audio', route => route.fulfill({ contentType: 'audio/wav', body: promptWav() }));
  await page.route('**/api/lessons/lesson/heard', async route => { await held; await route.fulfill({ json: completed }); });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
  await page.getByRole('button', { name: 'End', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  release();
  await expect(page.getByRole('heading', { name: /Lesson completed|Great job!/ })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeEnabled();
});
