import { test, expect, type Page } from '@playwright/test';

const TITLE = 'Ordering food at a restaurant';
const QUESTION = "Why can't Maria have the mushroom pasta?";
const audioTime = (page: Page) => page.evaluate(() => (document.querySelector('audio') as HTMLAudioElement).currentTime);
const noHorizontalScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

async function openLesson(page: Page) {
  await page.goto('/listening');
  await page.getByRole('link', { name: new RegExp(TITLE) }).click();
  await expect(page.getByRole('heading', { name: TITLE })).toBeVisible();
}
async function waitForAudio(page: Page) {
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
  await page.waitForFunction(() => { const a = document.querySelector('audio') as HTMLAudioElement | null; return !!a && a.readyState >= 1 && a.duration > 60; });
}

/** Start from a new, unanswered attempt whatever the previous test left behind. */
async function freshAttempt(page: Page) {
  const again = page.getByRole('button', { name: 'Listen again' });
  const start = page.getByRole('button', { name: 'Start exercises' });
  await expect(again.or(start)).toBeVisible();
  if (await again.isVisible()) await again.click();
  await expect(start).toBeVisible();
}

test.describe.serial('2.0 listening lesson', () => {
  test('the catalog offers the saved B1 lesson and nothing from the old engine runs', async ({ page }) => {
    const calls: string[] = [];
    page.on('request', request => { const url = new URL(request.url()); if (url.pathname.startsWith('/api/')) calls.push(url.pathname); });
    await page.goto('/listening');
    await expect(page.getByRole('heading', { name: 'Listening lessons' })).toBeVisible();
    const card = page.getByRole('link', { name: new RegExp(TITLE) });
    await expect(card).toContainText('B1');
    await card.click();
    await expect(page.getByText('Listen for')).toBeVisible();
    await waitForAudio(page);
    // audio first: no transcript until the learner asks for it
    await expect(page.getByRole('list', { name: 'Transcript' })).toHaveCount(0);
    expect(await noHorizontalScroll(page)).toBe(true);
    const legacy = calls.filter(path => !path.startsWith('/api/listening/') && path !== '/api/auth/config');
    expect(legacy).toEqual([]);
  });

  test('audio plays, seeks, skips and changes speed', async ({ page }) => {
    await openLesson(page);
    await waitForAudio(page);
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await expect.poll(() => audioTime(page), { timeout: 15000 }).toBeGreaterThan(0.6);
    await page.getByRole('button', { name: 'Pause' }).click();
    const paused = await audioTime(page);
    await page.getByRole('button', { name: 'Forward 10 seconds' }).click();
    expect(await audioTime(page)).toBeGreaterThan(paused + 9);
    await page.getByRole('button', { name: 'Back 10 seconds' }).click();
    expect(await audioTime(page)).toBeLessThan(paused + 1.5);
    await page.getByRole('button', { name: /Playback speed 1×/ }).click();
    await expect(page.getByRole('button', { name: /Playback speed 1.25×/ })).toBeVisible();
    expect(await page.evaluate(() => (document.querySelector('audio') as HTMLAudioElement).playbackRate)).toBe(1.25);
    await page.getByRole('button', { name: 'Replay from the start' }).click();
    await expect.poll(() => audioTime(page), { timeout: 10000 }).toBeLessThan(3);
  });

  test('the optional transcript follows the audio line by line and replays on tap', async ({ page }) => {
    await openLesson(page);
    await waitForAudio(page);
    await page.getByRole('button', { name: 'Guided' }).click();
    const lines = page.getByRole('list', { name: 'Transcript' }).getByRole('button');
    await expect(lines).toHaveCount(16);
    await expect(lines.nth(0)).toContainText('Sam · waiter');
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await expect(lines.nth(0)).toHaveAttribute('aria-current', 'true', { timeout: 10000 });
    await expect(lines.nth(1)).toHaveAttribute('aria-current', 'true', { timeout: 15000 });   // the second sentence starts at about 4.2s
    await expect(lines.nth(0)).not.toHaveAttribute('aria-current', 'true');
    // tap a later line: playback jumps there and that line becomes the active one
    await lines.nth(6).click();
    await expect(lines.nth(6)).toHaveAttribute('aria-current', 'true', { timeout: 5000 });
    expect(await audioTime(page)).toBeGreaterThan(20);
    await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
    await page.getByRole('button', { name: 'Challenge' }).click();
    await expect(page.getByRole('list', { name: 'Transcript' })).toHaveCount(0);
  });

  test('answering is deterministic, final, and survives a refresh', async ({ page }) => {
    await openLesson(page);
    await page.getByRole('button', { name: 'Start exercises' }).click();
    await expect(page.getByText(QUESTION)).toBeVisible();
    const check = page.getByRole('button', { name: 'Check answer' });
    await expect(check).toBeDisabled();
    await page.getByLabel('The pasta is too spicy for her.').check();
    await expect(check).toBeEnabled();
    // refresh before submitting: the attempt is recovered and the question is still there
    await page.reload();
    await expect(page.getByText(QUESTION)).toBeVisible();
    await page.getByLabel('The pasta is too spicy for her.').check();
    await check.click();
    await expect(page.getByRole('status').filter({ hasText: 'Not quite' })).toContainText('run out of mushrooms');
    await expect(page.getByText('Correct answer')).toBeVisible();
    await expect(page.getByText('Your answer')).toBeVisible();
    await expect(page.getByLabel('The restaurant has run out of mushrooms.')).toBeDisabled();
    await expect(page.getByText('0 of 1 correct')).toBeVisible();
    // refresh after finishing: the saved result and feedback come back
    await page.reload();
    await expect(page.getByText('0 of 1 correct')).toBeVisible();
    await expect(page.getByText('Not quite')).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);
  });

  test('a new attempt can be answered correctly and is scored correct', async ({ page }) => {
    await openLesson(page);
    await expect(page.getByRole('button', { name: 'Listen again' })).toBeVisible();   // the previous attempt's result
    await page.getByRole('button', { name: 'Listen again' }).click();
    await expect(page.getByRole('button', { name: 'Start exercises' })).toBeVisible();
    await page.getByRole('button', { name: 'Start exercises' }).click();
    await page.getByLabel('The restaurant has run out of mushrooms.').check();
    await page.getByRole('button', { name: 'Check answer' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Correct' }).first()).toBeVisible();
    await expect(page.getByText('1 of 1 correct')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Back to lessons' })).toBeVisible();
    await page.getByRole('link', { name: 'Back to lessons' }).click();
    await expect(page.getByRole('heading', { name: 'Listening lessons' })).toBeVisible();
  });

  test('phone and desktop layouts fit, with comfortable touch targets', async ({ page }) => {
    await openLesson(page);
    await page.getByRole('button', { name: 'Listen again' }).click();
    const start = page.getByRole('button', { name: 'Start exercises' });
    await expect(start).toBeVisible();
    const box = (await start.boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.y + box.height).toBeLessThanOrEqual(844);          // the primary action is on screen without scrolling
    await expect(start).toHaveCSS('color', 'rgb(255, 255, 255)'); // visible label: white on the dark button (global CSS once made it invisible)
    await expect(start).toHaveCSS('background-color', 'rgb(14, 27, 54)');
    const close = (await page.getByRole('link', { name: 'Leave lesson' }).boundingBox())!;
    expect(close.x).toBeLessThan(24);                             // top bar: close at the left edge, progress segments filling the row
    const segments = page.getByRole('list', { name: 'Lesson progress' }).getByRole('listitem');
    await expect(segments).toHaveCount(3);
    for (let i = 0; i < 3; i++) expect((await segments.nth(i).boundingBox())!.width).toBeGreaterThan(30);
    for (const name of ['Play', 'Back 10 seconds', 'Forward 10 seconds']) {
      const target = (await page.getByRole('button', { name, exact: true }).boundingBox())!;
      expect(Math.min(target.width, target.height)).toBeGreaterThanOrEqual(44);
    }
    expect(await noHorizontalScroll(page)).toBe(true);
    await page.setViewportSize({ width: 320, height: 640 });
    expect(await noHorizontalScroll(page)).toBe(true);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByRole('button', { name: 'Guided' }).click();
    const transcript = (await page.getByRole('list', { name: 'Transcript' }).boundingBox())!;
    const player = (await page.getByRole('region', { name: 'Audio player' }).boundingBox())!;
    expect(player.x).toBeGreaterThan(transcript.x + transcript.width - 1);   // two columns on a wide screen
    expect(await noHorizontalScroll(page)).toBe(true);
  });

  test('the signed audio link is short-lived and refuses tampering', async ({ page, request }) => {
    await openLesson(page);
    const src = await page.evaluate(() => (document.querySelector('audio') as HTMLAudioElement).src);
    const url = new URL(src);
    expect(url.pathname.startsWith('/api/listening/media/')).toBe(true);
    expect(Number(url.searchParams.get('exp')) * 1000 - Date.now()).toBeLessThanOrEqual(301_000);
    expect((await request.get(src)).status()).toBe(200);
    expect((await request.get(src.replace(/sig=[0-9a-f]/, 'sig=0'))).status()).toBe(404);
  });
});

import AxeBuilder from '@axe-core/playwright';

test.describe.serial('2.0 listening lesson: accessibility and keyboard', () => {
  test('no automatically detectable accessibility violations in any phase', async ({ page }) => {
    await openLesson(page);
    await freshAttempt(page);
    const scan = async (label: string) => {
      const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(result.violations.map(v => `${label}: ${v.id} ${v.nodes.map(n => `${n.target} ${n.any[0]?.message ?? ''}`).join(' | ')}`), label).toEqual([]);
    };
    await scan('listen');
    await page.getByRole('button', { name: 'Guided' }).click();
    await scan('guided transcript');
    await page.getByRole('button', { name: 'Challenge' }).click();
    await page.getByRole('button', { name: 'Start exercises' }).click();
    await scan('question');
    await page.getByLabel('The restaurant has run out of mushrooms.').check();
    await page.getByRole('button', { name: 'Check answer' }).click();
    await expect(page.getByText('1 of 1 correct')).toBeVisible();
    await scan('result');
  });

  test('the whole lesson can be completed with the keyboard alone, and focus is not lost', async ({ page }) => {
    await openLesson(page);
    await freshAttempt(page);
    await waitForAudio(page);
    const play = page.getByRole('button', { name: 'Play', exact: true });
    await play.focus();
    await page.keyboard.press('Space');
    await expect(page.getByRole('button', { name: 'Pause' })).toBeFocused();       // the same control keeps focus and now offers Pause
    await page.keyboard.press('Space');
    await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeFocused();
    const seek = page.getByRole('slider', { name: 'Seek' });
    await seek.focus();
    const before = await audioTime(page);
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => audioTime(page)).toBeGreaterThan(before);
    const start = page.getByRole('button', { name: 'Start exercises' });
    await start.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText(QUESTION)).toBeVisible();
    await expect(page.locator('#lesson-main')).toBeFocused();                      // focus moved to the new content, not left on a removed button
    await page.keyboard.press('Tab');                                              // into the page; reach the radio group without a mouse
    const radios = page.getByRole('radio');
    await radios.first().focus();
    await page.keyboard.press('ArrowDown');
    await expect(radios.nth(1)).toBeChecked();                                     // native radio keyboard behaviour
    await page.keyboard.press('Space');
    await expect(page.getByRole('button', { name: 'Check answer' })).toBeEnabled();
    await page.getByRole('button', { name: 'Check answer' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('status').filter({ hasText: /Correct|Not quite/ }).first()).toBeVisible();
    await expect(page.getByText('1 of 1 correct').or(page.getByText('0 of 1 correct'))).toBeVisible();
  });

  test('loading, failure and recovery states are shown and recoverable', async ({ page }) => {
    await page.route('**/api/listening/lessons', route => route.abort());
    await page.goto('/listening');
    await expect(page.getByRole('alert').filter({ hasText: 'couldn’t load your lesson' })).toBeVisible();
    await page.unroute('**/api/listening/lessons');
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('link', { name: new RegExp(TITLE) })).toBeVisible();
    // an answer that fails to save keeps the choice and offers a retry
    await page.getByRole('link', { name: new RegExp(TITLE) }).click();
    await freshAttempt(page);
    await page.getByRole('button', { name: 'Start exercises' }).click();
    await page.getByLabel('The restaurant has run out of mushrooms.').check();
    await page.route('**/api/listening/attempts/*/answers', route => route.abort());
    await page.getByRole('button', { name: 'Check answer' }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'couldn’t save that answer' })).toBeVisible();
    await expect(page.getByLabel('The restaurant has run out of mushrooms.')).toBeChecked();
    await page.unroute('**/api/listening/attempts/*/answers');
    await page.getByRole('button', { name: 'Check answer' }).click();
    await expect(page.getByText(/of 1 correct/)).toBeVisible();
  });
});
