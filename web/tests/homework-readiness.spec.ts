import { test, expect, type Page } from '@playwright/test';
import { installVoice, promptWav, probe } from './voice-fixture';

async function setup(page: Page, completion = false) {
  await installVoice(page, { autoPlayback: true });
  const state = { enabled: true, status: 'offered', pending: false, decisions: 0, checks: 0, spoken: 0, lostResponse: false };
  const assignment = () => ({ id: 'assignment', lesson_id: 'lesson', status: state.status, discovery_in_progress: state.pending,
    objective: 'Listen for the main idea and two supporting details.', message: 'No suitable listening assignment is available right now. Your lesson is saved.',
    content: ['assigned', 'completed'].includes(state.status) ? { provider: 'youtube', provider_id: 'abcdefghijk', canonical_url: 'https://www.youtube.com/watch?v=abcdefghijk', title: 'Science in everyday life', publisher: 'Synthetic acceptance fixture', duration_seconds: 180, validated_at: new Date().toISOString() } : null });
  if (completion) await page.addInitScript(() => { if (!sessionStorage.getItem('readiness-initialized')) { sessionStorage.setItem('audli-session:local', 'lesson'); sessionStorage.setItem('readiness-initialized', '1'); } });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'local' } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya', goal: 'Meetings', completed_attempts: 3 }, provider: 'demo' } });
    if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
    if (path === '/api/lessons/lesson') return route.fulfill({ json: { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, encouragement: 'Thanks for listening, Maya.' } });
    if (path === '/api/plan') return route.fulfill({ json: { revision: 1, focus: 'Notice the details that matter', readiness: 'ready', upcoming: ['Work updates', 'Travel stories', 'Science discoveries'].map((title, i) => ({ number: i + 1, title, objective: 'Listen for important details.', difficulty: { speech_rate: 1, duration_seconds: 60, vocabulary_level: 'B1', information_density: 1 } })), checkin: { available: false } } });
    if (path === '/api/homework') return route.fulfill({ json: { enabled: state.enabled, assignments: [assignment()] } });
    if (path === '/api/lessons/lesson/homework' || path === '/api/homework/assignment') return route.fulfill({ json: assignment() });
    if (path.endsWith('/invitation-audio')) { state.spoken++; return route.fulfill({ contentType: 'audio/wav', body: promptWav() }); }
    if (path.endsWith('/decision')) {
      state.decisions++; state.status = route.request().postDataJSON().accept ? 'assigned' : 'skipped';
      if (state.lostResponse) return route.abort('internetdisconnected');
      return route.fulfill({ json: assignment() });
    }
    if (path.endsWith('/validate')) { state.checks++; state.status = 'unavailable'; return route.fulfill({ json: assignment() }); }
    return route.fulfill({ json: [] });
  });
  await page.goto('/');
  if (!completion) await page.getByRole('button', { name: 'Plan', exact: true }).click();
  return state;
}

for (const width of [320, 390, 1440]) test(`release screenshots and keyboard acceptance at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 950 });
  const state = await setup(page, true);
  await expect(page.getByRole('heading', { name: 'Lesson completed' })).toBeFocused();
  await expect(page.getByRole('button', { name: 'Find optional listening' })).toBeVisible();
  expect(state.spoken).toBe(0); // Automatic spoken invitation is unsupported.
  await page.screenshot({ path: `../docs/screenshots/AUD-59-ready-offer-${width}.png`, fullPage: true });
  await page.getByRole('button', { name: 'Find optional listening' }).focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeFocused();
  expect(state.decisions).toBe(1); expect(state.spoken).toBe(0); // Confirmation is visual.
  await page.getByRole('button', { name: 'Return Home' }).click();
  await page.getByRole('button', { name: 'Plan', exact: true }).click();
  const link = page.getByRole('link', { name: 'Watch on YouTube (opens a new tab)' });
  await expect(link).toHaveAttribute('referrerpolicy', 'no-referrer'); await link.focus(); await expect(link).toBeFocused();
  await page.screenshot({ path: `../docs/screenshots/AUD-59-ready-plan-${width}.png`, fullPage: true });
  state.status = 'unavailable'; await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByText('No suitable listening assignment is available right now. Your lesson is saved.')).toBeVisible();
  await page.screenshot({ path: `../docs/screenshots/AUD-59-ready-unavailable-${width}.png`, fullPage: true });
  state.enabled = false; await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Optional listening', exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Next three lessons' })).toBeVisible();
  await page.screenshot({ path: `../docs/screenshots/AUD-59-ready-disabled-${width}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('refresh observes in-flight discovery and recovers a committed response lost on the network', async ({ page }) => {
  const state = await setup(page);
  state.pending = true; await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Find optional listening' })).toBeDisabled();
  state.pending = false; state.status = 'assigned';
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  expect(state.decisions).toBe(0); expect(state.spoken).toBe(0);
  state.status = 'offered'; state.lostResponse = true;
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await page.getByRole('button', { name: 'Find optional listening' }).click();
  await expect(page.locator('.homework-card').getByRole('alert')).toContainText('Reload your saved assignment');
  await page.getByRole('button', { name: 'Reload saved assignment' }).click();
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible(); expect(state.decisions).toBe(1);
});

test('decline persists silently; invitation audio plays only on explicit request', async ({ page }) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Hear invitation' }).click();
  await expect(page.getByRole('button', { name: 'No thanks' })).toBeEnabled();
  expect(state.spoken).toBe(1); expect((await probe(page)).plays.length).toBe(1);
  await page.getByRole('button', { name: 'No thanks' }).click();
  await expect(page.getByRole('article', { name: 'Optional listening assignment' })).toBeFocused();
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Hear invitation' })).toHaveCount(0); expect(state.spoken).toBe(1);
});

test('a link that ages while Plan stays open is rechecked before navigation', async ({ page }) => {
  const state = await setup(page); state.status = 'assigned';
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Watch on YouTube (opens a new tab)' })).toBeVisible();
  await page.clock.install(); await page.clock.fastForward(3601000);
  await page.getByRole('link', { name: 'Watch on YouTube (opens a new tab)' }).click();
  await expect(page.getByText('Optional listening · unavailable')).toBeVisible(); expect(state.checks).toBe(1);
  await expect(page.locator('.homework-card a')).toHaveCount(0);
});

test('a stalled discovery response reaches a finite browser deadline', async ({ page }) => {
  test.setTimeout(45000);
  await setup(page);
  await page.route('**/api/homework/assignment/decision', () => { /* fault injection: server never responds */ });
  await page.getByRole('button', { name: 'Find optional listening' }).click();
  await expect(page.locator('.homework-card').getByRole('alert')).toContainText('Reload your saved assignment', { timeout: 30000 });
  await expect(page.getByRole('button', { name: 'Find optional listening' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Reload saved assignment' })).toBeEnabled();
});
