import { test, expect, type BrowserContext, type Page } from '@playwright/test';
import { lifecycle, phaseNow } from './lifecycle-fixture';

/** AUD-47 phase 4: Home is driven by GET /api/eligibility. The mock server stands in for app/lesson_limit.py;
 *  these tests establish browser behavior (calm states, revalidation, skew, tabs, 429), not backend policy. */
test.use({ locale: 'en-US', timezoneId: 'UTC' });

const HOUR = 3600000, MIN = 60000;
type Body = Record<string, unknown>;
const stamp = (ms: number) => new Date(ms).toISOString();
function body(extra: Body = {}, now = Date.now()): Body {
  return { mode: 'enforce', enforced: true, eligible: false, reason: 'cooldown', server_time: stamp(now), next_available_at: stamp(now + 18 * HOUR + 42 * MIN),
    retry_after_seconds: 67320, last_started_at: stamp(now - 5 * HOUR), open_lesson: null, last_lesson: { id: 'done', status: 'completed' }, lesson_max_seconds: 600, ...extra };
}
const ready = (now = Date.now()): Body => body({ eligible: true, reason: 'eligible', next_available_at: null, retry_after_seconds: null }, now);
const open = (now: number, remaining: number, status = 'active'): Body => body({ eligible: false, reason: 'lesson_in_progress', next_available_at: null,
  open_lesson: { id: 'lesson', status, started_at: stamp(now - 10 * MIN + remaining), expires_at: stamp(now + remaining), grace_ends_at: stamp(now + remaining + 3 * MIN) } }, now);

type Answer = { status?: number; json?: unknown; text?: string };
async function serve(target: Page | BrowserContext, answer: () => Answer) {
  const seen = { count: 0 };
  await target.route('**/api/eligibility', route => {
    seen.count++;
    const { status = 200, json, text } = answer();
    return text !== undefined ? route.fulfill({ status, contentType: 'text/plain', body: text }) : route.fulfill({ status, json });
  });
  return seen;
}
const startButton = (page: Page) => page.getByRole('button', { name: 'Start today’s session' });
const card = (page: Page) => page.locator('.cooldown-card');
/** Error banners only: Next.js keeps an empty role=alert route announcer on every page. */
const errors = (page: Page) => page.locator('.session-error');
const shot = async (page: Page, name: string) => {
  if (process.env.AUDLI_SCREENSHOTS !== '1') return;
  await page.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });  // Dev-server badge only.
  await page.screenshot({ path: `../docs/screenshots/AUD-47-${name}.png` });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.screenshot({ path: `../docs/screenshots/AUD-47-${name}-scrolled.png` });
};
async function home(page: Page, options: Parameters<typeof lifecycle>[1] = {}) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const fixture = await lifecycle(page, options);
  return fixture;
}
async function reload(page: Page) { await page.reload(); await expect(page.getByRole('heading', { level: 1 })).toContainText('Good to see you'); }

test('cooldown is a calm countdown to the next lesson, with no Start button and no error', async ({ page }) => {
  await home(page); await serve(page, () => ({ json: body() }));
  await reload(page);
  await expect(card(page).getByRole('heading')).toHaveText('Today’s lesson is done. Nice work.');
  await expect(card(page).locator('.cooldown-countdown')).toHaveText(/^18 h 4[12] min$/);
  await expect(card(page)).toContainText('until your next lesson');
  await expect(card(page)).toContainText(/It opens (today|tomorrow) around \d{1,2}:\d{2} [AP]M\./);
  await expect(card(page).getByRole('status')).toContainText('Your next lesson opens in 18 hours');
  await expect(startButton(page)).toHaveCount(0);
  await expect(errors(page)).toHaveCount(0);
  await expect(page.getByText(/locked|denied|blocked|too many|limit reached/i)).toHaveCount(0);
  await shot(page, 'cooldown');
});

test('review, transcripts, progress and plan stay reachable during cooldown', async ({ page }) => {
  await home(page); await serve(page, () => ({ json: body() }));
  await reload(page);
  await card(page).getByRole('button', { name: 'Review what we covered' }).click();
  await expect(page.getByRole('button', { name: 'Review', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.home-view')).toHaveCount(0);
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await card(page).getByRole('button', { name: 'See your listening plan' }).click();
  await expect(page.getByRole('button', { name: 'Plan', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('an expired lesson says its work was saved and is not counted as finished', async ({ page }) => {
  await home(page); await serve(page, () => ({ json: body({ last_lesson: { id: 'x', status: 'expired' } }) }));
  await reload(page);
  await expect(card(page).getByRole('heading')).toHaveText('Today’s lesson time has ended');
  await expect(card(page)).toContainText('We saved what you did. It isn’t counted as a finished lesson');
  await expect(card(page).getByRole('button', { name: 'Review what we covered' })).toBeVisible();
  await expect(errors(page)).toHaveCount(0);
  await shot(page, 'expired');
});

test('an open lesson offers to continue and explains that the ten minutes keep running', async ({ page }) => {
  await home(page); await serve(page, () => ({ json: open(Date.now(), 6 * MIN, 'paused') }));
  await reload(page);
  await expect(page.getByRole('heading', { name: 'Your lesson is waiting' })).toBeVisible();
  await expect(page.getByText(/About [56] minutes left/)).toBeVisible();
  await expect(page.getByText('The 10 minutes keep running while you’re away')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue today’s lesson' })).toBeVisible();
  await shot(page, 'resume');
});

test('enforce + eligible shows Start with the pause explanation; off, observe, absent and garbage answers keep today\'s Home', async ({ page }) => {
  await home(page);
  let answer: Answer = { json: ready() };
  await serve(page, () => answer);
  await reload(page);
  await expect(startButton(page)).toBeVisible();
  await expect(page.getByText('keeps running, even if you pause')).toBeVisible();
  await shot(page, 'ready');
  const legacy: Answer[] = [
    { json: body({ mode: 'off', enforced: false, eligible: true, reason: 'eligible', next_available_at: null }) },
    { json: body({ mode: 'observe', enforced: false }) },  // would-refuse is only logged; the browser must show nothing
    { status: 404, json: { detail: 'Not Found' } }, { status: 503, text: 'upstream down' }, { json: [] }, { json: { mode: 'enforce' } },
  ];
  for (const item of legacy) {
    answer = item; await reload(page);
    await expect(startButton(page)).toBeEnabled();
    await expect(card(page)).toHaveCount(0);
    await expect(page.getByText('keeps running, even if you pause')).toHaveCount(0);
    await expect(errors(page)).toHaveCount(0);
  }
});

test('Home revalidates on focus, visibility, reconnect and back/forward restore, coalescing bursts', async ({ page }) => {
  await home(page); const seen = await serve(page, () => ({ json: body() }));
  await reload(page); await expect(card(page)).toBeVisible();
  let before = seen.count;
  const fire = async (script: () => void) => {
    await page.waitForTimeout(1150); await page.evaluate(script);
    await expect.poll(() => seen.count).toBeGreaterThan(before); before = seen.count;
  };
  await fire(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' }); document.dispatchEvent(new Event('visibilitychange')); });
  await fire(() => { window.dispatchEvent(new Event('focus')); });
  await fire(() => { window.dispatchEvent(new Event('online')); });
  await fire(() => { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); });
  await page.waitForTimeout(1150);
  await page.evaluate(() => { for (let i = 0; i < 5; i++) window.dispatchEvent(new Event('focus')); });
  await page.waitForTimeout(400);
  expect(seen.count).toBe(before + 1);
});

test('the countdown reaching zero only asks the server; Start appears when the server says so', async ({ page }) => {
  await home(page);
  let eligible = false;
  const seen = await serve(page, () => {
    const now = Date.now();
    return { json: eligible ? ready(now) : body({ next_available_at: stamp(now + 2200), retry_after_seconds: 3 }, now) };
  });
  await reload(page);
  await expect(card(page)).toBeVisible();
  // The browser's own timer passes the shown deadline several times; the server keeps saying "not yet".
  const start = seen.count;
  for (let i = 0; i < 12; i++) { await page.waitForTimeout(500); await expect(startButton(page)).toHaveCount(0); }
  expect(seen.count).toBeGreaterThan(start + 1);
  eligible = true;
  await expect(startButton(page)).toBeVisible({ timeout: 8000 });
  await expect(card(page)).toHaveCount(0);
});

test('a wrong device clock cannot change the countdown or unlock Start', async ({ page }) => {
  await home(page); await serve(page, () => ({ json: body({}, Date.parse('2026-10-21T12:00:00Z')) }));
  await page.addInitScript(() => {
    const Real = Date, offset = 5 * 86400000;
    class Skewed extends Real {
      constructor(...args: unknown[]) { if (args.length) super(...(args as [number])); else super(Real.now() + offset); }
      static now() { return Real.now() + offset; }
    }
    (window as unknown as { Date: typeof Date }).Date = Skewed as unknown as typeof Date;
  });
  await reload(page);
  await expect(card(page).locator('.cooldown-countdown')).toHaveText(/^18 h 4[12] min$/);
  // Judged from server time (2026-10-21 12:00Z) and the instant, so the skewed device date cannot mislabel it.
  await expect(card(page)).toContainText('It opens tomorrow around 6:42 AM.');
  await expect(startButton(page)).toHaveCount(0);
});

test('another tab starting or finishing a lesson updates this tab at once', async ({ page, context }) => {  // The second tab only announces on the shared channel.
  await home(page);
  let current: Body = body();
  const seen = await serve(page, () => ({ json: current }));
  await reload(page);
  await expect(card(page)).toBeVisible();
  const other = await context.newPage();
  await other.goto('/favicon.ico');
  const before = seen.count;
  current = ready();
  await other.evaluate(() => { const channel = new BroadcastChannel('audli-eligibility:local'); channel.postMessage({ at: Date.now() }); channel.close(); });
  await expect(startButton(page)).toBeVisible();
  expect(seen.count).toBeGreaterThan(before);
});

test('a start refused with 429 lesson_cooldown lands on the calm countdown, not an error screen', async ({ page }) => {
  await home(page);
  let current: Body = ready();
  await serve(page, () => ({ json: current }));
  await page.route('**/api/lessons/start', route => {
    current = body({}, Date.now());  // Another device started first.
    return route.fulfill({ status: 429, headers: { 'Retry-After': '67320' }, json: { detail: 'You’ve finished today’s listening lesson.',
      error: { code: 'lesson_cooldown', server_time: stamp(Date.now()), next_available_at: stamp(Date.now() + 18 * HOUR), retry_after_seconds: 67320 } } });
  });
  await reload(page);
  await startButton(page).click();
  await expect(card(page)).toBeVisible();
  await expect(errors(page)).toHaveCount(0);
  await expect(startButton(page)).toHaveCount(0);
  await expect(page.getByText('Retry connection')).toHaveCount(0);
});

test('Start re-asks the server first: a stale "ready" Home never opens the classroom in cooldown', async ({ page }) => {
  await home(page);
  let current: Body = ready();
  await serve(page, () => ({ json: current }));
  let starts = 0;
  await page.route('**/api/lessons/start', route => { starts++; return route.fallback(); });
  await reload(page);
  await expect(startButton(page)).toBeVisible();
  current = body();  // Changed on another device after this page loaded.
  await startButton(page).click();
  await expect(card(page)).toBeVisible();
  expect(starts).toBe(0);
  expect(await phaseNow(page)).not.toBe('LESSON');
});

test('time expiring mid-lesson: the saved lesson is shown as ended, work is kept, no generic error', async ({ page }) => {
  await home(page);
  let current: Body = ready();
  await serve(page, () => ({ json: current }));
  await page.route('**/api/lessons/lesson/audio', route => {
    current = body({ last_lesson: { id: 'lesson', status: 'expired' } });
    return route.fulfill({ status: 429, headers: { 'Retry-After': '86000' }, json: { detail: 'Today’s 10-minute lesson time is up.',
      error: { code: 'lesson_time_expired', server_time: stamp(Date.now()), next_available_at: stamp(Date.now() + 23 * HOUR), retry_after_seconds: 86000 } } });
  });
  await page.route('**/api/lessons/lesson', route => route.request().method() === 'GET' ? route.fulfill({ json: { id: 'lesson', revision: 4, status: 'expired', phase: 'EXERCISE',
    prompt: null, pending: null, exercise: null, introduction_pending: false, introduction_prompt: null, elapsed_seconds: 640, remaining_seconds: 0, server_time: stamp(Date.now()),
    focus: 'details', exercises_completed: 1, strength: null, improvement_focus: null, encouragement: 'Thanks.' } }) : route.fallback());
  await reload(page);
  await startButton(page).click();
  await expect(card(page).getByRole('heading')).toHaveText('Today’s lesson time has ended');
  await expect(errors(page)).toHaveCount(0);
  await expect(page.getByText('We saved what you did')).toBeVisible();
  await card(page).getByRole('button', { name: 'Review what we covered' }).click();
  await expect(page.getByRole('button', { name: 'Review', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('time expiring mid-lesson while the server moves it to its closing sequence continues into the existing wrap-up', async ({ page }) => {
  const { server } = await home(page);
  const current: Body = ready();
  await serve(page, () => ({ json: current }));
  let refused = false;
  await page.route('**/api/lessons/lesson/audio', route => {
    if (!refused) {
      refused = true;
      // The server settled this lesson into CLOSING (completed work only) before refusing the next practice step.
      Object.assign(server.lesson as object, { phase: 'CLOSING', revision: 3, prompt: 'Thanks for listening today.' });
      return route.fulfill({ status: 429, json: { detail: 'Time is up.', error: { code: 'lesson_time_expired', server_time: stamp(Date.now()), next_available_at: stamp(Date.now() + 23 * HOUR), retry_after_seconds: 82800 } } });
    }
    return route.fallback();
  });
  await reload(page);
  await startButton(page).click();
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'HOMEWORK', { timeout: 15000 });
  await expect(page.getByRole('heading', { name: 'Lesson completed' })).toBeVisible();
  await expect(errors(page)).toHaveCount(0);
  expect(server.heard).toBeGreaterThan(0);
});

test('ending a lesson explains that the ten minutes keep running, in enforce mode only', async ({ page }) => {
  await home(page);
  let current: Body = ready();
  await serve(page, () => ({ json: current }));
  await reload(page);
  await startButton(page).click();
  await page.getByRole('button', { name: 'End', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('Today’s 10-minute lesson time keeps running while you’re away.');
  await shot(page, 'end-dialog');
  await page.getByRole('button', { name: 'Stay here' }).click();
  current = body({ mode: 'off', enforced: false, eligible: true, reason: 'eligible', next_available_at: null });
  await page.getByRole('button', { name: 'End', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('Your saved progress will be here when you return.');
});
