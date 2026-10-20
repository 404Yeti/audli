import { test, expect, type Page } from '@playwright/test';
import { lifecycle, recordPhases, phaseNow } from './lifecycle-fixture';
import { typeReply } from './homework-fixture';

/** AUD-73 regression tests for the lesson ending: HOME → SETUP → LESSON → WRAP-UP → SUCCESS → HOME.
 *  Mock server: these establish ordering, ownership and plumbing, not provider accuracy. */
async function begin(page: Page, motion = false) {
  if (!motion) await page.emulateMedia({ reducedMotion: 'reduce' });
  const phases = await recordPhases(page);
  const fixture = await lifecycle(page, { from: 'CLOSING', due: false });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  return { ...fixture, phases };
}
const start = (page: Page) => page.getByRole('button', { name: 'Start today’s session' }).click();
const phaseIs = (page: Page, phase: string) => expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', phase);
const decide = async (page: Page, reply = 'no thanks') => { await typeReply(page, reply); await page.getByRole('button', { name: 'Send', exact: true }).click(); };
const delay = (page: Page, pattern: string, ms: number, count = { started: 0, fulfilled: 0 }) => page.route(pattern, async route => {
  count.started++;
  await new Promise(resolve => setTimeout(resolve, ms));
  await route.fallback(); count.fulfilled++;
});

test('slow requests never leave wrap-up blank or thin, and none of them gates it', async ({ page }) => {
  const { homework } = await begin(page);
  const profile = { started: 0, fulfilled: 0 };
  await delay(page, '**/api/profile', 5000, profile);
  await delay(page, '**/api/exercises/current', 5000);
  await delay(page, '**/api/lessons/lesson/homework', 3000);
  const listings: string[] = [];
  page.on('request', request => { if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/homework') listings.push(request.url()); });
  await page.evaluate(() => {
    const log: { t: number; heading: string; content: boolean }[] = [];
    (window as unknown as { wrapLog: typeof log }).wrapLog = log;
    setInterval(() => {
      if (document.documentElement.dataset.lifecyclePhase !== 'HOMEWORK') return;
      log.push({ t: Date.now(), heading: document.querySelector('h1')?.textContent?.trim() ?? '', content: Boolean(document.querySelector('[role=status], .homework-card')) });
    }, 40);
  });
  await start(page);
  await phaseIs(page, 'HOMEWORK');
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible({ timeout: 15000 });
  const log = await page.evaluate(() => (window as unknown as { wrapLog: { t: number; heading: string; content: boolean }[] }).wrapLog);
  expect(log.length).toBeGreaterThan(20);
  for (const sample of log) expect(sample, JSON.stringify(sample)).toMatchObject({ heading: 'Lesson completed', content: true });
  // The wrap-up card was on screen while the (slow) profile refresh was still outstanding: it does not gate the screen.
  expect(profile.started).toBeGreaterThan(profile.fulfilled);
  expect(listings).toEqual([]); // the lesson card no longer needs the account-wide listing
  await decide(page);
  await phaseIs(page, 'SUCCESS');
  expect(homework.decisions).toBe(1);
});

test('duplicate callbacks resolve the wrap-up once: one decision, one farewell, one Success', async ({ page }) => {
  const { server, homework, phases } = await begin(page);
  const offers: string[] = [];
  page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/lessons/lesson/homework') offers.push(request.url()); });
  await start(page);
  await phaseIs(page, 'HOMEWORK');
  await typeReply(page, 'no thanks');
  await page.getByRole('button', { name: 'Send', exact: true }).dblclick();
  await phaseIs(page, 'SUCCESS');
  await page.waitForTimeout(500);
  const settled = offers.length;
  await page.locator('audio').first().evaluate(audio => { for (let i = 0; i < 4; i++) audio.dispatchEvent(new Event('ended')); });
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online'));
  });
  await page.clock.install(); await page.clock.runFor(120000);
  await phaseIs(page, 'SUCCESS');
  await expect(page.getByRole('heading', { name: 'Great job!' })).toHaveCount(1);
  expect(homework.decisions).toBe(1);
  expect(offers.length).toBe(settled); // no signal after Success triggers another load
  await expect.poll(() => server.farewells).toBe(1);
  const log = await phases();
  expect(log.filter(phase => phase === 'SUCCESS')).toHaveLength(1);
  expect(log.lastIndexOf('HOMEWORK')).toBeLessThan(log.indexOf('SUCCESS'));
});

test('the ending order is wrap-up, one Success screen, Home: there is no separate stats screen', async ({ page }) => {
  const { phases } = await begin(page, true);
  await start(page);
  await phaseIs(page, 'HOMEWORK');
  await decide(page);
  await phaseIs(page, 'SUCCESS');
  // Celebration and statistics are on the same screen at the same time, and Return Home is the only way out.
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  await expect(page.locator('canvas')).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue to results' })).toHaveCount(0);
  await page.clock.install(); await page.clock.runFor(10 * 60 * 1000);
  await phaseIs(page, 'SUCCESS');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await page.getByRole('button', { name: 'Return Home' }).click();
  await phaseIs(page, 'HOME');
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeVisible();
  const log = await phases();
  expect(log).not.toContain('STATS');
  const wrapup = log.slice(log.indexOf('FEEDBACK')).filter((phase, i, all) => ['FEEDBACK', 'HOMEWORK', 'SUCCESS', 'HOME'].includes(phase) && phase !== all[i - 1]);
  expect(wrapup).toEqual(['FEEDBACK', 'HOMEWORK', 'SUCCESS', 'HOME']);
});

test('reload during the celebration lands on Success with its statistics, without replaying it or the farewell', async ({ page }) => {
  const { server, homework } = await begin(page, true);
  await start(page);
  await phaseIs(page, 'HOMEWORK');
  await decide(page);
  await phaseIs(page, 'SUCCESS');
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect.poll(() => server.farewells).toBe(1);
  const decisions = homework.decisions;
  await page.reload();
  await phaseIs(page, 'SUCCESS');
  await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  await expect(page.locator('.completion-summary')).toContainText('1 exercise completed');
  await expect(page.getByRole('button', { name: 'Return Home' })).toBeVisible();
  await page.waitForTimeout(500);
  expect(homework.decisions).toBe(decisions);
  expect(server.farewells).toBe(1);
  expect(await phaseNow(page)).toBe('SUCCESS');
});

test('reload during wrap-up repeats nothing that was decided and asks at most once more', async ({ page }) => {
  const { homework } = await begin(page);
  await start(page);
  await phaseIs(page, 'HOMEWORK');
  await page.reload();
  await phaseIs(page, 'HOMEWORK');
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Speak', exact: true })).toBeVisible();   // silent card with its controls
  expect(homework.decisions).toBe(0);
  await decide(page);
  await phaseIs(page, 'SUCCESS');
  await page.reload();
  await phaseIs(page, 'SUCCESS');
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).not.toContainText('Would you like');
  expect(homework.decisions).toBe(1);
});
