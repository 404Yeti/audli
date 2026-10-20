import { test, expect, type Page } from '@playwright/test';
import { lifecycle, countTopicEntries, recordPhases, phaseNow, type Options } from './lifecycle-fixture';
import { typeReply } from './homework-fixture';

/** Browser lifecycle regressions (AUD-68). The mock server mirrors the lesson/planner contracts;
 *  these establish ordering and plumbing, not evaluator or provider accuracy.
 *  Sequence under test: small talk → previous-homework query → lesson → class feedback → new-homework
 *  query → one Success screen (Pip's celebration + final stats) → explicit Return Home. */
async function begin(page: Page, options: Options & { motion?: boolean } = {}) {
  if (!options.motion) await page.emulateMedia({ reducedMotion: 'reduce' });
  const entries = await countTopicEntries(page);
  const phases = await recordPhases(page);
  const fixture = await lifecycle(page, options);
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  return { ...fixture, entries, phases };
}
const start = (page: Page) => page.getByRole('button', { name: 'Start today’s session' }).click();
const topic = (page: Page) => page.getByLabel('Your topic preference');
const phaseIs = (page: Page, phase: string) => expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', phase);
const toHomework = async (page: Page) => { await start(page); await expect.poll(() => phaseNow(page), { timeout: 30000 }).toBe('HOMEWORK'); };
const decide = async (page: Page, reply = 'no thanks') => { await typeReply(page, reply); await page.getByRole('button', { name: 'Send', exact: true }).click(); };
const toSuccess = async (page: Page, reply = 'no thanks') => { await toHomework(page); await decide(page, reply); await phaseIs(page, 'SUCCESS'); };
const inOrder = (log: string[], expected: string[]) => {
  let at = 0;
  for (const phase of expected) { at = log.indexOf(phase, at); expect(at, `${phase} in ${log.join('>')}`).toBeGreaterThanOrEqual(0); }
};

test('full lifecycle in the requested order, ending only on explicit Return Home', async ({ page }) => {
  const { server, homework, entries, phases } = await begin(page, { due: true, motion: true });
  await start(page);
  await expect(topic(page)).toBeVisible();
  await phaseIs(page, 'TOPIC_SETUP');
  await topic(page).fill('science');
  await page.locator('.topic-checkin').getByRole('button', { name: 'Send', exact: true }).click();
  await phaseIs(page, 'HOMEWORK');
  // Class feedback has been spoken; the new-homework query is on screen and nothing after it has started.
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Great job!' })).toHaveCount(0);
  await expect(page.locator('.completion-summary')).toHaveCount(0);
  await page.waitForTimeout(1500);
  await phaseIs(page, 'HOMEWORK');
  await decide(page, 'no thanks');
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  expect(homework.decisions).toBe(1);
  await phaseIs(page, 'SUCCESS');
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Finish lesson' })).toHaveCount(0);
  await expect.poll(() => server.farewells).toBe(1);
  await page.clock.install(); await page.clock.runFor(10 * 60 * 1000);
  await phaseIs(page, 'SUCCESS');
  await page.getByRole('button', { name: 'Return Home' }).click();
  await phaseIs(page, 'HOME');
  const log = await phases();
  inOrder(log, ['TOPIC_SETUP', 'SMALL_TALK', 'HOMEWORK_PREFLIGHT', 'FEEDBACK', 'HOMEWORK', 'SUCCESS', 'HOME']);
  expect(server.homeworkReviews).toBe(1);
  expect(log.filter(phase => phase === 'TOPIC_SETUP')).toHaveLength(1);
  expect(await entries()).toBe(1); expect(server.topicTexts).toBe(1); expect(server.offers).toBe(0);
  expect(server.resolved).toBe('answered'); expect(server.farewells).toBe(1);
});

test('no preference UI when none is due', async ({ page }) => {
  const { server, entries, phases } = await begin(page, { due: false });
  await toHomework(page);
  expect(await entries()).toBe(0); expect(server.offers).toBe(0);
  expect(await phases()).not.toContain('TOPIC_SETUP');
});

test('Keep my topics completes the check-in once and the lesson follows', async ({ page }) => {
  const { server, entries } = await begin(page, { due: true });
  await start(page);
  await page.getByRole('button', { name: 'Keep my topics' }).dblclick();
  await phaseIs(page, 'HOMEWORK');
  expect(server.topicTexts).toBe(1); expect(server.resolved).toBe('answered'); expect(await entries()).toBe(1);
});

test('repeated preference submission saves exactly once', async ({ page }) => {
  const { server } = await begin(page, { due: true });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/plan/checkin/text', async route => { await held; await route.fallback(); });
  await start(page);
  await topic(page).fill('science');
  await page.locator('.topic-checkin').getByRole('button', { name: 'Send', exact: true }).dblclick(); await topic(page).press('Enter');
  release();
  await phaseIs(page, 'HOMEWORK');
  expect(server.topicTexts).toBe(1); expect(server.topicAnswers).toBe(0);
});

test('an interrupted check-in is deferred: not asked at the next Start, still available in Plan', async ({ page }) => {
  const { server, entries, phases } = await begin(page, { due: true });
  await start(page);
  await expect(topic(page)).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
  expect(server.resolved).toBeNull();
  await start(page);
  await phaseIs(page, 'HOMEWORK');
  expect(await entries()).toBe(1); expect(server.offers).toBe(0); expect(server.resolved).toBeNull();
  const log = await phases();
  expect(log.filter(phase => phase === 'TOPIC_SETUP')).toHaveLength(1);
  expect(log.lastIndexOf('TOPIC_SETUP')).toBeLessThan(log.indexOf('SMALL_TALK'));
  await page.getByRole('button', { name: 'Return Home' }).click();
  await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your listening plan' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Share feedback' })).toBeVisible();
});

test('the celebration waits for the new homework decision; duplicate events and time cannot start it', async ({ page }) => {
  const { server, homework, entries } = await begin(page, { due: false, motion: true });
  // The autoplayed invitation listens for a spoken choice; a silent recording is not a decision, so hold it.
  await page.route('**/api/homework/assignment/decision/attempts', () => { /* never answered */ });
  await toHomework(page);
  await page.locator('audio').evaluate(audio => { for (let i = 0; i < 3; i++) audio.dispatchEvent(new Event('ended')); });
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    window.dispatchEvent(new Event('focus'));
  });
  await page.clock.install(); await page.clock.runFor(90000);
  await phaseIs(page, 'HOMEWORK');
  await expect(page.getByRole('heading', { name: 'Great job!' })).toHaveCount(0);
  expect(await entries()).toBe(0); expect(server.farewells).toBe(0); expect(server.starts).toBe(1); expect(homework.decisions).toBe(0);
});

test('a failed homework decision keeps the query on screen until it is completed or explicitly skipped', async ({ page }) => {
  const { homework } = await begin(page, { due: false });
  await toHomework(page);
  await page.route('**/api/homework/assignment/decision/text', route => route.abort('internetdisconnected'));
  await decide(page, 'yes please');
  await expect(page.locator('.homework-recovery [role=alert]')).toContainText('connection');
  await page.waitForTimeout(1500);
  await phaseIs(page, 'HOMEWORK');
  await page.unroute('**/api/homework/assignment/decision/text');
  await page.getByRole('button', { name: 'Skip homework' }).click();
  await phaseIs(page, 'SUCCESS');
  expect(homework.decisions).toBe(1); // The aborted attempt never reached the server.
});

test('a delayed farewell never gates the final card; it plays once', async ({ page }) => {
  const { server } = await begin(page, { due: false });
  await toHomework(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/lessons/lesson/farewell-audio', async route => { await held; await route.fallback(); });
  await decide(page);
  await phaseIs(page, 'SUCCESS');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await page.waitForTimeout(500);
  release();
  await expect.poll(() => server.farewells).toBe(1);
  await phaseIs(page, 'SUCCESS');
});

test('remount and restoration resume the homework query, then the final card, without repeating the decision, a celebration replay or a farewell', async ({ page }) => {
  const { server, entries, phases } = await begin(page, { due: true });
  await start(page);
  await topic(page).fill('science');
  await page.locator('.topic-checkin').getByRole('button', { name: 'Send', exact: true }).click();
  await phaseIs(page, 'HOMEWORK');
  await page.reload();
  await phaseIs(page, 'HOMEWORK');
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  await decide(page);
  await phaseIs(page, 'SUCCESS');
  await page.reload();
  await phaseIs(page, 'SUCCESS');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await page.waitForTimeout(500);
  expect(await entries()).toBe(1); expect(server.offers).toBe(0); expect(server.topicTexts).toBe(1); expect(server.farewells).toBe(0); // A restored session is silent.
  // Restoring into Success re-enters that screen, but never goes back to the homework query after the decision.
  const log = await phases();
  expect(log.lastIndexOf('HOMEWORK')).toBeLessThan(log.indexOf('SUCCESS'));
  expect(log).not.toContain('STATS');
});

test('homework off for the account: nothing to decide, Success screen directly', async ({ page }) => {
  await begin(page, { due: false, homework: false });
  await start(page);
  await phaseIs(page, 'SUCCESS');
  await expect(page.locator('.completion-summary')).toBeVisible();
});

for (const [outcome, reply, expected] of [
  ['accepted', 'yes please', 'Science in everyday life'],
  ['declined', 'no thanks', 'No homework today. Your next lesson is ready when you are.'],
  ['unavailable', 'yes please', 'No suitable listening assignment is available right now.'],
] as const) test(`homework ${outcome}: decision persists once, then the final card shows it`, async ({ page }) => {
  const { server, homework, entries } = await begin(page, { due: false });
  if (outcome === 'unavailable') await page.route('**/api/homework/assignment/decision/text', route => { homework.decisions++; homework.status = 'unavailable'; return route.fulfill({ json: { id: 'assignment', lesson_id: 'lesson', status: 'unavailable', message: 'No suitable listening assignment is available right now. Your lesson is saved.', objective: 'Listen.' } }); });
  await toHomework(page);
  await typeReply(page, reply);
  await page.getByRole('button', { name: 'Send', exact: true }).dblclick();
  await phaseIs(page, 'SUCCESS');
  await expect(page.getByText(expected).first()).toBeVisible();
  await expect(page.locator('.completion-summary')).toBeVisible();
  expect(homework.decisions).toBe(1);
  expect(await entries()).toBe(0); expect(server.offers).toBe(0);
});

test('in-app history entries cannot step the lesson backwards', async ({ page }) => {
  // The app has no routes: leaving its first history entry exits the app itself, which no state can prevent.
  // Entries the app or the browser adds (bfcache, extensions, OAuth return) must be inert.
  const { entries } = await begin(page, { due: false });
  await toSuccess(page);
  await page.evaluate(() => new Promise<void>(resolve => {
    addEventListener('popstate', () => resolve(), { once: true });
    history.pushState({ stale: true }, '', '/?return=1'); history.back();
  }));
  await phaseIs(page, 'SUCCESS');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  expect(await entries()).toBe(0);
});

test('Return Home is explicit and the next lesson starts clean', async ({ page }) => {
  const { server, homework, entries } = await begin(page, { due: true });
  await start(page);
  await page.getByRole('button', { name: 'Keep my topics' }).click();
  await phaseIs(page, 'HOMEWORK');
  await decide(page);
  await phaseIs(page, 'SUCCESS');
  await page.clock.install(); await page.clock.runFor(10 * 60 * 1000);
  await phaseIs(page, 'SUCCESS');
  await page.getByRole('button', { name: 'Return Home' }).click();
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
  homework.status = 'offered'; // The next lesson brings a fresh, undecided assignment.
  await start(page);
  await phaseIs(page, 'HOMEWORK');
  expect(server.starts).toBe(2); expect(await entries()).toBe(1);
});
