import { test, expect } from '@playwright/test';
import { freshAttempt, lessonCard } from './listening-fixture';
import type { Page } from '@playwright/test';

// AUD-120: the COMPLETE lesson (audio playback and all four exercises) on a genuinely slow, high-latency connection.
// Chromium's network emulation throttles every request the page makes (API, audio file, recording upload); nothing is mocked.
test.skip(!process.env.AUDLI_LISTENING_E2E, 'run with listening.playwright.config.ts');

const TITLE = 'Ordering food at a restaurant';
const noHorizontalScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

/** Three seconds of a square-wave tone as a WAV file: valid, decodable, not silent. Real browsers send it through the real upload path. */
function wav(seconds = 3): Buffer {
  const rate = 16000, count = rate * seconds, data = Buffer.alloc(count * 2);
  for (let i = 0; i < count; i++) data.writeInt16LE(Math.floor(i / 40) % 2 ? 9000 : -9000, i * 2);
  const head = Buffer.alloc(44);
  head.write('RIFF', 0); head.writeUInt32LE(36 + data.length, 4); head.write('WAVEfmt ', 8); head.writeUInt32LE(16, 16); head.writeUInt16LE(1, 20);
  head.writeUInt16LE(1, 22); head.writeUInt32LE(rate, 24); head.writeUInt32LE(rate * 2, 28); head.writeUInt16LE(2, 32); head.writeUInt16LE(16, 34);
  head.write('data', 36); head.writeUInt32LE(data.length, 40);
  return Buffer.concat([head, data]);
}


const next = (page: Page) => page.getByRole('button', { name: /Next question|See your summary/ }).click();
async function chooseCorrect(page: Page) {
  await page.getByLabel('The restaurant has run out of mushrooms.').check();
  await page.getByRole('button', { name: 'Check answer' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Correct' }).first()).toBeVisible();
}
async function sendFile(page: Page) {
  await expect(page.getByRole('heading', { name: /In your own words/ })).toBeVisible();
  await page.locator('input[type=file]').setInputFiles({ name: 'answer.wav', mimeType: 'audio/wav', buffer: wav() });
  await page.getByRole('button', { name: 'Send answer' }).click();
  await expect(page.getByRole('heading', { name: 'Answer sent' })).toBeVisible();
}
async function fill(page: Page, word: string) {
  await page.getByLabel('Missing word').fill(word);
  await page.getByRole('button', { name: 'Check answer' }).click();
}
const item = (page: Page, text: string, dir: 'up' | 'down') => page.getByRole('button', { name: `Move “${text}” ${dir}` });
const WATER = 'Maria asks for a glass of water', MUSH = 'Sam says they have run out of mushrooms';
async function arrange(page: Page) {                    // chicken, bread, water, mushrooms  ->  water, mushrooms, chicken, bread
  await item(page, WATER, 'up').click(); await item(page, WATER, 'up').click();       // water, chicken, bread, mushrooms
  await item(page, MUSH, 'up').click(); await item(page, MUSH, 'up').click();         // water, mushrooms, chicken, bread
  await expect(page.getByRole('list', { name: 'Your order, first to last' }).getByRole('listitem').first()).toContainText('glass of water');
}


test('the whole lesson works on a slow, laggy connection: load, audio, all four exercises, summary', async ({ page }) => {
  test.setTimeout(175_000);
  await page.goto('/listening');
  await lessonCard(page, new RegExp(TITLE)).click();
  await expect(page.getByRole('heading', { name: TITLE })).toBeVisible({ timeout: 120_000 });     // first visit compiles the route in the dev server
  await freshAttempt(page);                                                                         // settle state at full speed, then slow down
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 350, downloadThroughput: 200_000, uploadThroughput: 90_000 });   // ~1.6 / 0.7 Mbit/s, 350 ms added delay
  await page.reload();
  await expect(page.getByRole('button', { name: 'Start exercises' }).or(page.getByRole('button', { name: 'Listen again' }))).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('alert').filter({ hasText: 'couldn’t load your lesson' })).toHaveCount(0);

  // audio playback over the slow link
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.waitForFunction(() => { const a = document.querySelector('audio') as HTMLAudioElement | null; return !!a && a.currentTime > 0.5; }, undefined, { timeout: 60_000 });
  await page.getByRole('button', { name: 'Pause' }).click();

  await page.getByRole('button', { name: 'Start exercises' }).click();
  await expect(page.getByText('Question 1', { exact: true })).toBeVisible();
  await chooseCorrect(page);
  await next(page);
  await sendFile(page);
  await next(page);
  await expect(page.getByText('Question 3 · Fill in the blank')).toBeVisible();
  await fill(page, ' Herbs. ');
  await expect(page.getByRole('status').filter({ hasText: 'Correct' }).first()).toBeVisible();
  await next(page);
  await expect(page.getByText('Question 4 · Put in order')).toBeVisible();
  await arrange(page);
  await page.getByRole('button', { name: 'Check order' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Correct order' })).toBeVisible();
  await page.getByRole('button', { name: 'See your summary' }).click();
  await expect(page.getByRole('heading', { name: TITLE, level: 2 })).toBeVisible();
  await expect(page.getByText('3 of 3 correct')).toBeVisible();
  expect(await noHorizontalScroll(page)).toBe(true);
});
