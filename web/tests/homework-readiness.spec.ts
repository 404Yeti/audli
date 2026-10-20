import { test, expect } from '@playwright/test';
import { installVoice, probe } from './voice-fixture';

import { setup, typeReply } from './homework-fixture';

for (const width of [320, 390, 1440]) test(`release screenshots and keyboard acceptance at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 950 });
  const state = await setup(page, true);
  await expect(page.getByRole('heading', { name: 'Lesson completed' })).toBeFocused();
  await expect(page.getByRole('button', { name: 'Speak', exact: true })).toBeVisible();
  expect(state.spoken).toBe(0); // Restored completion never repeats an automatic invitation.
  await page.screenshot({ path: `../docs/screenshots/AUD-59-ready-offer-${width}.png`, fullPage: true });
  await typeReply(page, 'yes please'); await page.getByRole('button', { name: 'Send' }).focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: /Lesson completed|Great job!/ })).toBeVisible();
  expect(state.decisions).toBe(1); await expect.poll(() => state.confirmations).toBe(1); // Saved before confirmation audio.
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
  await expect(page.getByText('Finding a short listening assignment…')).toBeVisible();
  state.pending = false; state.status = 'assigned';
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
  expect(state.decisions).toBe(0); expect(state.spoken).toBe(0);
  state.status = 'offered'; state.lostResponse = true;
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await typeReply(page, 'yes please'); await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.locator('.homework-card').getByRole('alert')).toContainText('Reload your saved assignment');
  await page.getByRole('button', { name: 'Reload saved assignment' }).click();
  await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible(); expect(state.decisions).toBe(1);
});

test('decline persists silently; invitation audio plays only on explicit request', async ({ page }) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Replay homework invitation' }).click();
  await expect(page.getByRole('button', { name: 'Speak' })).toBeEnabled();
  expect(state.spoken).toBe(1); expect((await probe(page)).plays.length).toBe(1);
  await typeReply(page, 'no thanks'); await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Replay homework invitation' })).toHaveCount(0); expect(state.spoken).toBe(1);
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
  await page.route('**/api/homework/assignment/decision/text', () => { /* fault injection: server never responds */ });
  await typeReply(page, 'yes please'); await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.locator('.homework-card').getByRole('alert')).toContainText('Reload your saved assignment', { timeout: 30000 });
  await expect(page.getByRole('button', { name: 'Speak' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Reload saved assignment' })).toBeEnabled();
});


for (const choice of ['accept', 'refuse', 'unavailable'] as const) test(`spoken homework ${choice} returns to saved completion`, async ({ page }) => {
  const state = await setup(page, true);
  state.acceptVoice = choice !== 'refuse'; state.failVoice = choice === 'unavailable';
  await page.getByRole('button', { name: 'Speak' }).click();
  await expect(page.getByText('Listening for your choice…')).toBeVisible();
  await expect.poll(() => state.voiceRequests).toBe(1);
  await expect(page.getByRole('heading', { name: /Lesson completed|Great job!/ })).toBeVisible();
  if (choice === 'accept') {
    await expect(page.getByRole('heading', { name: 'Science in everyday life' })).toBeVisible();
    await expect.poll(() => state.confirmations).toBe(1);
  } else {
    await expect(page.getByRole('heading', { name: /Lesson completed|Great job!/ })).toBeVisible();
    expect(state.status).toBe(choice === 'refuse' ? 'skipped' : 'unavailable');
    expect(state.confirmations).toBe(0);
  }
  const recorded = await probe(page); expect(recorded.overlaps).toBe(0); expect(recorded.tracks).toBe(1);
  await page.reload(); expect(state.voiceRequests).toBe(1); expect(state.spoken).toBe(0);
  await page.getByRole('button', { name: 'Return Home' }).click();
});

test('interrupted homework recording releases microphone and never submits or speaks', async ({ page }) => {
  const state = await setup(page, true);
  await page.getByRole('button', { name: 'Speak' }).click();
  await expect(page.getByText('Listening for your choice…')).toBeVisible();
  await page.getByRole('button', { name: 'Skip optional listening' }).click();
  await expect(page.getByRole('heading', { name: /Lesson completed|Great job!/ })).toBeVisible();
  expect((await probe(page)).tracks).toBe(1); expect(state.voiceRequests).toBe(0); expect(state.confirmations).toBe(0);
});

test('leaving during STT prevents late speech and duplicate decision delivery', async ({ page }) => {
  const state = await setup(page, true);
  let release!: () => void; const held = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  await page.route('**/api/homework/assignment/decision/attempts', async route => {
    requests++; await held;
    await route.fulfill({ json: { id: 'assignment', status: 'assigned', objective: 'Listen.' } }).catch(() => {});
  });
  await page.getByRole('button', { name: 'Speak' }).dblclick();
  await expect.poll(() => requests).toBe(1);
  await page.getByRole('button', { name: 'Return Home' }).click(); release();
  await expect(page.getByRole('heading', { name: 'Lesson completed' })).toHaveCount(0);
  expect(state.confirmations).toBe(0); expect((await probe(page)).overlaps).toBe(0);
});

test('fresh completion sequences one invitation, spoken choice and confirmation, then the farewell; refresh is silent', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const state = await setup(page);
  await page.route('**/api/lessons/start', route => route.fulfill({ json: { id: 'lesson', phase: 'COMPLETED', status: 'completed', focus: 'details', exercises_completed: 1, elapsed_seconds: 600, encouragement: 'Thanks for listening, Maya.' } }));
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Start today’s session' }).click();
  await expect(page.getByRole('heading', { name: /Lesson completed|Great job!/ })).toBeVisible();
  await expect.poll(() => state.spoken).toBe(1);
  await expect.poll(() => state.confirmations).toBe(1);
  await expect(page.locator('html')).toHaveAttribute('data-lifecycle-phase', 'SUCCESS');
  await expect(page.getByRole('button', { name: 'Hear assignment' })).toBeEnabled();
  expect(state.voiceRequests).toBe(1); expect(state.decisions).toBe(1);
  // The farewell plays once on the Success screen; it never gates it.
  await expect.poll(() => state.closings).toBe(1);
  await expect.poll(async () => (await probe(page)).plays.length).toBe(3);
  expect((await probe(page)).overlaps).toBe(0);
  await page.reload(); await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  expect(state.spoken).toBe(1); expect(state.confirmations).toBe(1); expect(state.closings).toBe(1);
});


test('repeated delivered decision joins existing discovery and confirms once after polling', async ({ page }) => {
  const state = await setup(page, true);
  await page.route('**/api/homework/assignment/decision/text', route => route.fulfill({ json: { id: 'assignment', lesson_id: 'lesson', status: 'offered', discovery_in_progress: true, objective: 'Listen.' } }));
  await typeReply(page, 'yes please'); await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText('Finding a short listening assignment…')).toBeVisible();
  state.status = 'assigned';
  await expect.poll(() => state.confirmations).toBe(1);
  await expect(page.getByRole('button', { name: 'Hear assignment' })).toBeEnabled();
  await page.reload(); await expect(page.getByRole('heading', { name: 'Great job!' })).toBeVisible();
  expect(state.confirmations).toBe(1); expect(state.decisions).toBe(0);
});


for (const completion of [false, true]) for (const [name, capability] of [
  ['disabled', false], ['unknown', undefined], ['malformed', 'true'], ['null', null],
] as const) test(`${name} capability makes zero homework requests in ${completion ? 'completion' : 'Plan'}`, async ({ page }) => {
  const state = await setup(page, completion, { capability });
  await expect(page.getByRole('heading', { name: completion ? 'Lesson completed' : 'Next three lessons' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Optional listening', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: completion ? 'Return Home' : 'Review', exact: true }).click();
  if (!completion) await expect(page.getByRole('heading', { name: 'Your listening journey' })).toBeVisible();
  await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Next three lessons' })).toBeVisible();
  expect(state.homeworkRequests).toBe(0); expect(state.spoken).toBe(0); expect(state.confirmations).toBe(0);
  expect((await probe(page)).mediaCalls).toBe(0);
});

test('failed bootstrap capability never loads homework', async ({ page }) => {
  // A failed bootstrap retains the existing connection-retry screen, not opt-in.
  await installVoice(page, { autoPlayback: true });
  const homework: string[] = [];
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes('homework')) homework.push(path);
    return route.fulfill({ status: 503, json: { detail: 'Configuration unavailable' } });
  });
  await page.goto('/'); await expect(page.getByRole('button', { name: 'Retry connection' })).toBeVisible();
  expect(homework).toEqual([]); expect((await probe(page)).mediaCalls).toBe(0);
});

test('an explicitly enabled bootstrap retains homework loading without another capability endpoint', async ({ page }) => {
  const state = await setup(page, false, { capability: true });
  await expect(page.getByRole('button', { name: 'Speak' })).toBeVisible();
  const loads = state.homeworkRequests; const bootstraps = state.configRequests;
  expect(loads).toBeGreaterThan(0); // Development Strict Mode may abort/restart the existing loader.
  await typeReply(page, 'no thanks'); await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  expect(state.homeworkRequests).toBe(loads + 1);
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByText('No homework today. Your next lesson is ready when you are.')).toBeVisible();
  expect(state.configRequests).toBe(bootstraps);
});
