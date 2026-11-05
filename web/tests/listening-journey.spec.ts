import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { freshAttempt } from './listening-fixture';

// Needs its own backend and worker: only runs under listening.playwright.config.ts, never in the default mocked suite.
test.skip(!process.env.AUDLI_LISTENING_E2E, 'run with listening.playwright.config.ts');

const TITLE = 'Ordering food at a restaurant';
const HEARD = 'I think the restaurant had run out of mushrooms, so Maria chose the chicken without onions instead';
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

async function begin(page: Page) {
  await page.goto('/listening');
  await page.getByRole('link', { name: new RegExp(TITLE) }).click();
  await freshAttempt(page);
  await page.getByRole('button', { name: 'Start exercises' }).click();
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

test.describe.serial('2.0 complete lesson journey', () => {
  test('choice, spoken answer, blank and ordering all work, and the learner never waits for the AI', async ({ page }) => {
    await begin(page);
    await expect(page.getByText('Question 1')).toBeVisible();
    await chooseCorrect(page);
    await next(page);

    await sendFile(page);                                                      // the recording is accepted...
    await next(page);
    await expect(page.getByText('Question 3 · Fill in the blank')).toBeVisible();   // ...and the lesson moves on at once
    await expect(page.getByRole('status').filter({ hasText: 'being checked' })).toBeVisible();   // the assessment is still pending (worker is delayed)
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
    await expect(page.getByRole('heading', { name: 'Your spoken answer' })).toBeVisible();
    // The summary updates by itself when the background assessment finishes.
    await expect(page.getByText('What Audli heard')).toBeVisible({ timeout: 60000 });
    await expect(page.getByText(HEARD)).toBeVisible();
    await expect(page.getByText('This is feedback on your answer, not a grade.')).toBeVisible();
    await expect(page.getByText('The restaurant had run out of mushrooms', { exact: true })).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);
  });

  test('the journey is recovered after a refresh while the answer is pending and again after it is assessed', async ({ page }) => {
    await begin(page);
    await chooseCorrect(page);
    await next(page);
    await sendFile(page);
    await page.reload();                                                       // mid-journey, assessment pending
    await expect(page.getByText('Question 3 · Fill in the blank')).toBeVisible();
    await fill(page, 'spices');
    await expect(page.getByText('The missing word was')).toBeVisible();
    await expect(page.getByText('herbs', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('Question 4 · Put in order')).toBeVisible();
    await page.getByRole('button', { name: 'Check order' }).click();           // the order shown first is deliberately wrong
    await expect(page.getByText(/Not quite — \d of 4 in the right place/)).toBeVisible();
    await expect(page.getByText('Misplaced').first()).toBeVisible();
    await page.reload();                                                       // attempt complete, spoken still pending or just assessed
    await expect(page.getByText('1 of 3 correct')).toBeVisible();
    await expect(page.getByText('What Audli heard')).toBeVisible({ timeout: 60000 });
    await page.reload();
    await expect(page.getByText('1 of 3 correct')).toBeVisible();
    await expect(page.getByText(HEARD)).toBeVisible();
    await expect(page.getByText('Not quite').first()).toBeVisible();
  });

  test('skipping the spoken answer is shown as skipped and never as a score', async ({ page }) => {
    await begin(page);
    await chooseCorrect(page);
    await next(page);
    await page.getByRole('button', { name: 'Skip this question' }).click();
    await expect(page.getByRole('heading', { name: 'Skipped' })).toBeVisible();
    await next(page);
    await fill(page, 'herbs');
    await next(page);
    await page.getByRole('button', { name: 'Check order' }).click();
    await page.getByRole('button', { name: 'See your summary' }).click();
    await expect(page.getByText('Spoken answer: skipped')).toBeVisible();
    await expect(page.getByText('You skipped this question, so it isn’t counted either way.')).toBeVisible();
    await expect(page.getByText('What Audli heard')).toHaveCount(0);
    await expect(page.getByText('2 of 3 correct')).toBeVisible();
  });

  test('a blocked microphone leaves the file upload and skip options in place', async ({ page }) => {
    // Some engines (WebKit in CI) have no MediaRecorder at all; give this scenario one so it tests the permission path in every engine.
    await page.addInitScript(() => {
      if (typeof (window as unknown as { MediaRecorder?: unknown }).MediaRecorder === 'undefined') {
        (window as unknown as { MediaRecorder: unknown }).MediaRecorder = class { static isTypeSupported() { return false; } };
      }
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError'));
    });
    await begin(page);
    await chooseCorrect(page);
    await next(page);
    await page.getByRole('button', { name: 'Start recording' }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'couldn’t use your microphone' })).toBeVisible();
    await expect(page.locator('input[type=file]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Skip this question' })).toBeVisible();
    await sendFile(page);                                                     // the upload still works
  });

  test('a browser that cannot record says so and still offers upload and skip', async ({ page }) => {
    await page.addInitScript(() => { delete (window as unknown as { MediaRecorder?: unknown }).MediaRecorder; });
    await begin(page);
    await chooseCorrect(page);
    await next(page);
    await expect(page.getByRole('status').filter({ hasText: 'Recording isn’t available in this browser' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start recording' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Skip this question' })).toBeVisible();
    await sendFile(page);
  });

  test('the real recorder works where the browser has a microphone device', async ({ page, browserName }) => {
    test.skip(browserName !== 'chromium', 'Only Chromium has a fake microphone in CI; other engines use the upload path above.');
    await begin(page);
    await chooseCorrect(page);
    await next(page);
    await page.getByRole('button', { name: 'Start recording' }).click();
    await expect(page.getByRole('status').filter({ hasText: /Recording 0:0/ })).toBeVisible();
    await page.waitForTimeout(3500);
    await page.getByRole('button', { name: 'Stop' }).click();
    await expect(page.getByLabel('Listen to your recording')).toBeVisible();
    await page.getByRole('button', { name: 'Record again' }).click();            // discarding is possible, and nothing was sent
    await expect(page.getByRole('button', { name: 'Start recording' })).toBeVisible();
    await page.getByRole('button', { name: 'Start recording' }).click();
    await page.waitForTimeout(3500);
    await page.getByRole('button', { name: 'Stop' }).click();
    await page.getByRole('button', { name: 'Send answer' }).click();
    await expect(page.getByRole('heading', { name: 'Answer sent' })).toBeVisible();
  });

  test('every screen of the journey is accessible and fits phone, small phone and desktop widths', async ({ page }) => {
    const scan = async (label: string) => {
      const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(result.violations.map(v => `${label}: ${v.id} ${v.nodes.map(n => `${n.target} ${n.any[0]?.message ?? ''}`).join(' | ')}`), label).toEqual([]);
      for (const [width, height] of [[390, 844], [320, 640], [1280, 900]]) {
        await page.setViewportSize({ width, height });
        expect(await noHorizontalScroll(page), `${label} at ${width}`).toBe(true);
      }
      await page.setViewportSize({ width: 390, height: 844 });
    };
    await begin(page);
    await scan('question 1');
    await chooseCorrect(page);
    await scan('question 1 answered');
    await next(page);
    await expect(page.getByRole('heading', { name: /In your own words/ })).toBeVisible();
    await scan('spoken ready');
    await page.locator('input[type=file]').setInputFiles({ name: 'answer.wav', mimeType: 'audio/wav', buffer: wav() });
    await expect(page.getByLabel('Listen to your recording')).toBeVisible();
    await scan('spoken recorded');
    await page.getByRole('button', { name: 'Send answer' }).click();
    await expect(page.getByRole('heading', { name: 'Answer sent' })).toBeVisible();
    await scan('spoken sent');
    await next(page);
    await scan('fill blank');
    await fill(page, 'herbs');
    await expect(page.getByRole('status').filter({ hasText: 'Correct' }).first()).toBeVisible();
    await scan('fill blank answered');
    await next(page);
    await scan('sequencing');
    await arrange(page);
    await page.getByRole('button', { name: 'Check order' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Correct order' })).toBeVisible();
    await scan('sequencing answered');
    await page.getByRole('button', { name: 'See your summary' }).click();
    await expect(page.getByText('3 of 3 correct')).toBeVisible();
    await scan('summary, assessment pending');
    await expect(page.getByText('What Audli heard')).toBeVisible({ timeout: 60000 });
    await scan('summary, assessed');
  });

  test('the sequencing exercise can be completed with the keyboard alone', async ({ page }) => {
    await begin(page);
    await chooseCorrect(page);
    await next(page);
    await page.getByRole('button', { name: 'Skip this question' }).click();
    await next(page);
    await fill(page, 'herbs');
    await next(page);
    const up = item(page, WATER, 'up');
    await up.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('status').filter({ hasText: /moved to position 2 of 4/ })).toBeAttached();
    await page.getByRole('button', { name: 'Check order' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('status').filter({ hasText: /Not quite|Correct order/ }).first()).toBeVisible();
  });
});
