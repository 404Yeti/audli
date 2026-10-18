import { test, expect, type Page } from '@playwright/test';
import { lifecycle, countTopicEntries, recordPhases, phaseNow, type Options } from './lifecycle-fixture';
import { typeReply } from './homework-fixture';

/** Browser lifecycle regressions (AUD-68). The mock server mirrors the lesson/planner contracts;
 *  these establish ordering and plumbing, not evaluator or provider accuracy. */
async function begin(page: Page, options: Options = {}) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const entries = await countTopicEntries(page);
  const phases = await recordPhases(page);
  const fixture = await lifecycle(page, options);
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  return { ...fixture, entries, phases };
}
const start = (page: Page) => page.getByRole('button', { name: 'Start today’s session' }).click();
const topic = (page: Page) => page.getByLabel('Your topic preference');
const toWrapup = async (page: Page) => { await start(page); await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('WRAPUP'); };
const inOrder = (log: string[], expected: string[]) => {
  let at = 0;
  for (const phase of expected) { at = log.indexOf(phase, at); expect(at, `${phase} in ${log.join('>')}`).toBeGreaterThanOrEqual(0); }
};

test('full lifecycle: preferences once in setup, then each phase in order, ending only on explicit return', async ({ page }) => {
  const { server, entries, phases } = await begin(page, { due: true });
  await start(page);
  await expect(topic(page)).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'TOPIC_SETUP');
  await topic(page).fill('science');
  await page.locator('.topic-checkin').getByRole('button', { name: 'Send', exact: true }).click();
  await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('WRAPUP');
  await expect(page.getByRole('heading', { name: 'Lesson completed' })).toBeVisible();
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  await typeReply(page, 'no thanks');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  await page.getByRole('button', { name: 'Finish lesson', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'FINISHED');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await page.getByRole('button', { name: 'Return Home' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'HOME');
  inOrder(await phases(), ['TOPIC_SETUP', 'SMALL_TALK', 'HOMEWORK_PREFLIGHT', 'FEEDBACK', 'WRAPUP', 'FINISHED', 'HOME']);
  const log = await phases();
  expect(log.filter(phase => phase === 'TOPIC_SETUP')).toHaveLength(1);
  expect(await entries()).toBe(1);
  expect(server.topicTexts).toBe(1);
  expect(server.offers).toBe(0); // The completion-time claim endpoint is never called.
  expect(server.resolved).toBe('answered');
  expect(server.farewells).toBe(1);
});

test('no preference UI when none is due', async ({ page }) => {
  const { server, entries, phases } = await begin(page, { due: false });
  await toWrapup(page);
  expect(await entries()).toBe(0); expect(server.offers).toBe(0);
  expect(await phases()).not.toContain('TOPIC_SETUP');
});

test('repeated preference submission saves exactly once', async ({ page }) => {
  const { server } = await begin(page, { due: true });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/plan/checkin/text', async route => { await held; await route.fallback(); });
  await start(page);
  await topic(page).fill('science');
  const send = page.locator('.topic-checkin').getByRole('button', { name: 'Send', exact: true });
  await send.dblclick(); await topic(page).press('Enter');
  release();
  await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('WRAPUP');
  expect(server.topicTexts).toBe(1); expect(server.topicAnswers).toBe(0);
});

test('duplicate completion, late audio events and page lifecycle events cannot reopen preferences or finish early', async ({ page }) => {
  const { server, entries } = await begin(page, { due: false });
  await toWrapup(page);
  await page.locator('audio').evaluate(audio => { for (let i = 0; i < 3; i++) audio.dispatchEvent(new Event('ended')); });
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    window.dispatchEvent(new Event('focus'));
  });
  await page.clock.install(); await page.clock.runFor(90000);
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'WRAPUP');
  expect(await entries()).toBe(0); expect(server.farewells).toBe(0); expect(server.starts).toBe(1);
});

test('a delayed farewell cannot skip ahead; finishing is explicit and happens once', async ({ page }) => {
  const { server } = await begin(page, { due: false });
  await toWrapup(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/lessons/lesson/farewell-audio', async route => { await held; await route.fallback(); });
  await page.getByRole('button', { name: 'Finish lesson', exact: true }).dblclick();
  await expect(page.getByRole('button', { name: 'Finishing…' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'WRAPUP');
  release();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'FINISHED');
  expect(server.farewells).toBe(1);
});

test('remount and restoration resume wrap-up, then the final card, without preferences or a second celebration', async ({ page }) => {
  const { server, entries, phases } = await begin(page, { due: true });
  await start(page);
  await topic(page).fill('science');
  await page.locator('.topic-checkin').getByRole('button', { name: 'Send', exact: true }).click();
  await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('WRAPUP');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'WRAPUP');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await page.getByRole('button', { name: 'Finish lesson', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'FINISHED');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'FINISHED');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Great job!' })).toHaveCount(0);
  expect(await entries()).toBe(1); expect(server.offers).toBe(0); expect(server.topicTexts).toBe(1);
  expect((await phases()).filter(phase => phase === 'SUCCESS').length).toBeLessThanOrEqual(1);
});

test('an unanswered preference interrupted in setup is restored in setup, never after the lesson', async ({ page }) => {
  const { server, entries, phases } = await begin(page, { due: true });
  await start(page);
  await expect(topic(page)).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
  expect(server.resolved).toBeNull();
  await start(page);
  await expect(topic(page)).toBeVisible();
  await page.getByRole('button', { name: 'Keep my topics' }).click();
  await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('WRAPUP');
  const log = await phases();
  expect(log.lastIndexOf('TOPIC_SETUP')).toBeLessThan(log.indexOf('SMALL_TALK'));
  expect(await entries()).toBe(2); expect(server.offers).toBe(0); expect(server.resolved).toBe('answered');
  await expect(page.locator('.topic-checkin')).toHaveCount(0);
});

for (const [outcome, reply, expected] of [
  ['accepted', 'yes please', 'Science in everyday life'],
  ['declined', 'no thanks', 'No homework today. Your next lesson is ready when you are.'],
  ['unavailable', 'yes please', 'No suitable listening assignment is available right now.'],
] as const) test(`homework ${outcome}: decision persists once and the final card stays`, async ({ page }) => {
  const { server, homework, entries } = await begin(page, { due: false });
  if (outcome === 'unavailable') await page.route('**/api/homework/assignment/decision/text', route => { homework.decisions++; homework.status = 'unavailable'; return route.fulfill({ json: { id: 'assignment', lesson_id: 'lesson', status: 'unavailable', message: 'No suitable listening assignment is available right now. Your lesson is saved.', objective: 'Listen.' } }); });
  await toWrapup(page);
  await typeReply(page, reply);
  await page.getByRole('button', { name: 'Send', exact: true }).dblclick();
  await expect(page.getByText(expected).first()).toBeVisible();
  expect(homework.decisions).toBe(1);
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'WRAPUP');
  await expect(page.locator('.completion-summary')).toBeVisible();
  expect(await entries()).toBe(0); expect(server.offers).toBe(0);
});

test('in-app history traversal cannot step the lesson backwards', async ({ page }) => {
  // The app has no routes: leaving its first history entry exits the app itself, which no state can prevent.
  // Entries it or the browser adds (bfcache, extensions, OAuth return) must be inert.
  const { entries } = await begin(page, { due: false });
  await toWrapup(page);
  await page.evaluate(() => new Promise<void>(resolve => {
    addEventListener('popstate', () => resolve(), { once: true });
    history.pushState({ stale: true }, '', '/?return=1'); history.back();
  }));
  await page.evaluate(() => { history.forward(); history.back(); });
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'WRAPUP');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Great job!' })).toHaveCount(0);
  expect(await entries()).toBe(0);
});

test('the final card waits for the learner; Return Home is explicit and the next lesson starts clean', async ({ page }) => {
  const { server, entries } = await begin(page, { due: true });
  await start(page);
  await page.getByRole('button', { name: 'Keep my topics' }).click();
  await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('WRAPUP');
  await page.getByRole('button', { name: 'Finish lesson', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'FINISHED');
  await page.clock.install(); await page.clock.runFor(10 * 60 * 1000);
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'FINISHED');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await page.getByRole('button', { name: 'Return Home' }).click();
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
  await start(page);
  await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('WRAPUP');
  expect(server.starts).toBe(2); expect(await entries()).toBe(1); expect(server.topicSkips + server.topicTexts).toBe(1);
});
