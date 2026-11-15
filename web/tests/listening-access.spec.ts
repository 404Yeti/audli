import { test, expect, type Page, type Route } from '@playwright/test';
import { freshAttempt, lessonCard } from './listening-fixture';

// AUD-120: lesson access on slow and unreliable connections, in a real browser against the real FastAPI + SQLite acceptance server.
// Only the network is degraded (delays, dropped responses, injected 4xx/5xx); every success is answered by the real API.
test.skip(!process.env.AUDLI_LISTENING_E2E, 'run with listening.playwright.config.ts');

const TITLE = 'Ordering food at a restaurant';
const CURRENT = '**/api/listening/lessons/*/attempts/current';
const START = '**/api/listening/lessons/*/attempts';
const ATTEMPT = '**/api/listening/attempts/*';
const ready = (page: Page) => page.getByRole('button', { name: 'Start exercises' }).or(page.getByRole('button', { name: 'Listen again' }));
const alert = (page: Page) => page.getByRole('alert').filter({ hasText: 'couldn’t load your lesson' });
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/** The lesson's URL, after the server and learner state of earlier tests has been settled. */
async function lessonUrl(page: Page): Promise<string> {
  await page.goto('/listening');
  await lessonCard(page, new RegExp(TITLE)).click();
  await expect(page.getByRole('heading', { name: TITLE })).toBeVisible({ timeout: 120_000 });      // the first visit compiles the route in the dev server
  await freshAttempt(page);
  return page.url();
}
const bodyOf = (route: Route) => (route.request().postDataJSON() as { client_request_id: string }).client_request_id;
const noAttemptYet = (page: Page) => page.route(CURRENT, route => route.fulfill({ json: { attempt_id: null } }));      // forces the "start" path

test.describe.serial('lesson access on poor connections (AUD-120)', () => {
  test('a slow network shows a labelled wait and then the lesson, never an error', async ({ page }) => {
    const url = await lessonUrl(page);
    const slow = async (route: Route) => { await sleep(2500); await route.continue(); };
    await page.route(CURRENT, slow); await page.route(ATTEMPT, slow);
    await page.goto(url);
    await expect(page.getByRole('status').filter({ hasText: 'Getting your lesson ready' })).toBeVisible();
    await expect(ready(page)).toBeVisible();
    await expect(alert(page)).toHaveCount(0);
  });

  test('a transient gateway failure is retried automatically, without the learner doing anything', async ({ page }) => {
    const url = await lessonUrl(page);
    let calls = 0;      // the dev server runs effects twice (StrictMode), so prove the retry by "two failures, still loads", not by an exact count
    await page.route(CURRENT, route => ++calls <= 2 ? route.fulfill({ status: 503, json: { detail: 'Sign-in verification is unavailable. Please retry.' } }) : route.continue());
    await page.goto(url);
    await expect(ready(page)).toBeVisible();
    expect(calls).toBeGreaterThanOrEqual(3);
  });

  test('an HTML gateway page (the proxy could not reach the API) is also retried, then recovers', async ({ page }) => {
    const url = await lessonUrl(page);
    let calls = 0;
    await page.route(CURRENT, route => ++calls <= 2 ? route.fulfill({ status: 502, contentType: 'text/html', body: '<html>Bad gateway</html>' }) : route.continue());
    await page.goto(url);
    await expect(ready(page)).toBeVisible();
    expect(calls).toBeGreaterThanOrEqual(3);
  });

  test('a refusal is final: no retry, a plain message and a support reference', async ({ page }) => {
    const url = await lessonUrl(page);
    let calls = 0;
    await page.route(CURRENT, route => { calls++; return route.fulfill({ status: 404, headers: { 'X-Audli-Reference': 'abc123def456' }, json: { detail: 'Not found.' } }); });
    await page.goto(url);
    await expect(alert(page)).toBeVisible();
    await expect(alert(page)).toContainText('not available to your account');
    await expect(alert(page)).toContainText('Support reference: abc123def456');
    await expect(alert(page)).not.toContainText(/404|Not found/);
    const seen = calls;
    await sleep(3500);                        // longer than every retry delay: a refusal must not be retried
    expect(calls).toBe(seen);
    expect(seen).toBeLessThanOrEqual(2);      // 1, or 2 when the dev server double-runs the effect
  });

  test('a request that never answers gives up with a friendly message instead of loading forever, and Try again recovers', async ({ page }) => {
    test.setTimeout(170_000);
    const url = await lessonUrl(page);
    await page.route(CURRENT, () => { /* never answered */ });
    await page.goto(url);
    await expect(page.getByRole('status').filter({ hasText: 'Getting your lesson ready' })).toBeVisible();
    await expect(alert(page)).toBeVisible({ timeout: 90_000 });
    await expect(alert(page)).toContainText('slow or unstable connection');
    await expect(alert(page)).toContainText(/Support reference: C-[0-9a-f]{10}/);
    await page.unroute(CURRENT);
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(ready(page)).toBeVisible();
  });

  test('an interrupted start (the response is lost) is reconciled by re-reading the attempt, not by writing again', async ({ page }) => {
    const url = await lessonUrl(page);
    const posts: string[] = [];
    let started = false;      // "no attempt yet" until the first write has been seen; afterwards the real server answers
    await page.route(CURRENT, route => started ? route.continue() : route.fulfill({ json: { attempt_id: null } }));
    await page.route(START, async route => {
      posts.push(bodyOf(route)); started = true;
      await route.fetch();                  // the server did create (or resume) the attempt …
      await route.abort('connectionreset');  // … but the browser never saw the answer
    });
    await page.goto(url);
    await expect(ready(page)).toBeVisible();
    await expect(alert(page)).toHaveCount(0);
    expect(posts).toHaveLength(1);           // adopted through the read; no second write
  });

  test('if the attempt cannot be found after a lost response, the start is replayed ONCE with the same id (the server replays it)', async ({ page }) => {
    const url = await lessonUrl(page);
    const posts: string[] = [];
    await noAttemptYet(page);
    await page.route(START, async route => {
      posts.push(bodyOf(route));
      if (posts.length === 1) { await route.fetch(); await route.abort('connectionreset'); } else await route.continue();
    });
    await page.goto(url);
    await expect(ready(page)).toBeVisible();
    expect(posts).toHaveLength(2);
    expect(posts[1]).toBe(posts[0]);
  });

  test('a request id the server says belongs to another lesson is forgotten and replaced once: the lesson opens', async ({ page }) => {
    const url = await lessonUrl(page);
    const posts: string[] = [];
    await noAttemptYet(page);
    await page.route(START, route => {
      posts.push(bodyOf(route));
      return posts.length === 1
        ? route.fulfill({ status: 409, headers: { 'X-Audli-Error-Code': 'request_id_conflict', 'X-Audli-Reference': 'conflict0001' }, json: { detail: 'This request id was already used for a different lesson.' } })
        : route.continue();
    });
    await page.goto(url);
    await expect(ready(page)).toBeVisible();
    expect(posts).toHaveLength(2);
    expect(posts[1]).not.toBe(posts[0]);
    const stale = await page.evaluate(() => Object.keys(sessionStorage).filter(key => key.startsWith('audli.listening.request.')));
    expect(stale).toEqual([]);
  });

  test('any other 409 is never retried: one write, a plain message', async ({ page }) => {
    const url = await lessonUrl(page);
    let posts = 0;
    await noAttemptYet(page);
    await page.route(START, route => { posts++; return route.fulfill({ status: 409, headers: { 'X-Audli-Error-Code': 'conflict' }, json: { detail: 'Something else.' } }); });
    await page.goto(url);
    await expect(alert(page)).toBeVisible();
    expect(posts).toBe(1);
  });

  test('a refresh in the middle of starting reuses the same request id, so the server never makes two attempts', async ({ page }) => {
    const url = await lessonUrl(page);
    const posts: string[] = [];
    await noAttemptYet(page);
    let first = true;
    await page.route(START, async route => {
      posts.push(bodyOf(route));
      if (first) { first = false; await sleep(2000); await route.abort('aborted'); } else await route.continue();
    });
    await page.goto(url);
    await expect.poll(() => posts.length).toBe(1);
    await page.reload();                      // the learner refreshes while the first request is still in flight
    await expect(ready(page)).toBeVisible();
    expect(posts[posts.length - 1]).toBe(posts[0]);
  });

  test('two tabs opening the same lesson at once both load it, on one attempt', async ({ page, context }) => {
    const url = await lessonUrl(page);
    const second = await context.newPage();
    const seen = new Set<string>();
    for (const tab of [page, second]) tab.on('request', request => { const m = request.url().match(/\/api\/listening\/attempts\/([0-9a-f-]{36})$/); if (m) seen.add(m[1]); });
    await Promise.all([page.goto(url), second.goto(url)]);
    await expect(ready(page)).toBeVisible();
    await expect(ready(second)).toBeVisible();
    expect(seen.size).toBe(1);
    await second.close();
  });

  test('switching lessons never reuses a request id and never shows the previous lesson’s error', async ({ page }) => {
    const url = await lessonUrl(page);
    await page.route(CURRENT, route => route.fulfill({ status: 503, json: { detail: 'down' } }));
    await page.goto(url);
    await expect(alert(page)).toBeVisible({ timeout: 40_000 });
    await page.unroute(CURRENT);
    await page.goto('/listening');
    await lessonCard(page, /At the pharmacy/).click();
    await expect(page.getByRole('heading', { name: /pharmacy/i })).toBeVisible();
    await expect(alert(page)).toHaveCount(0);
  });
});
