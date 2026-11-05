import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { freshAttempt, lessonCard } from './listening-fixture';

// Needs its own backend and worker: only runs under listening.playwright.config.ts, never in the default mocked suite.
test.skip(!process.env.AUDLI_LISTENING_E2E, 'run with listening.playwright.config.ts');

const noHorizontalScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const item = (extra: Record<string, unknown>) => ({
  lesson_id: '11111111-1111-4111-8111-111111111111', slug: 'at-the-pharmacy', title: 'At the pharmacy', topic: 't', cefr: 'A2', listen_for: 'x', accents: ['american'],
  duration_ms: 56_000, status: 'new', attempt_id: null, answered: 0, total: 4, correct: null, scored: null, spoken_status: null, last_activity_at: null, completed_count: 0, ...extra });
const NO_PROGRESS = { lessons_available: 1, lessons_completed: 0, attempts_completed: 0, questions_correct: 0, questions_scored: 0, recent: [] };
const wav = (): Buffer => {
  const rate = 16000, count = rate * 3, data = Buffer.alloc(count * 2);
  for (let i = 0; i < count; i++) data.writeInt16LE(Math.floor(i / 40) % 2 ? 9000 : -9000, i * 2);
  const head = Buffer.alloc(44);
  head.write('RIFF', 0); head.writeUInt32LE(36 + data.length, 4); head.write('WAVEfmt ', 8); head.writeUInt32LE(16, 16); head.writeUInt16LE(1, 20);
  head.writeUInt16LE(1, 22); head.writeUInt32LE(rate, 24); head.writeUInt32LE(rate * 2, 28); head.writeUInt16LE(2, 32); head.writeUInt16LE(16, 34);
  head.write('data', 36); head.writeUInt32LE(data.length, 40);
  return Buffer.concat([head, data]);
};
const mockLibrary = async (page: Page, items: unknown[], progress: unknown = NO_PROGRESS) => {
  await page.route('**/api/listening/lessons', route => route.fulfill({ json: items }));
  await page.route('**/api/listening/progress', route => route.fulfill({ json: progress }));
};
const move = (page: Page, text: string, dir: 'up' | 'down') => page.getByRole('button', { name: `Move “${text}” ${dir}` });

test.describe('2.0 library states (real pages, controlled responses)', () => {
  test('a new lesson shows its level, length and a clear way in', async ({ page }) => {
    await mockLibrary(page, [item({})]);
    await page.goto('/listening');
    const card = lessonCard(page, /At the pharmacy/);
    await expect(card).toContainText('A2');
    await expect(card).toContainText('About 1 min of listening · 4 questions');
    await expect(card).toContainText('New');
    await expect(card).toContainText('Start');
    await expect(page.getByText('Nothing finished yet. Pick a lesson to begin.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Recent activity' })).toHaveCount(0);
  });

  test('in progress, finished, pending, failed and skipped spoken answers are each stated truthfully', async ({ page }) => {
    const when = new Date().toISOString();
    await mockLibrary(page, [
      item({ title: 'In progress lesson', lesson_id: '22222222-2222-4222-8222-222222222222', status: 'in_progress', answered: 2, attempt_id: 'a' }),
      item({ title: 'Pending lesson', lesson_id: '33333333-3333-4333-8333-333333333333', status: 'completed', correct: 2, scored: 3, spoken_status: 'pending', completed_count: 1 }),
      item({ title: 'Failed lesson', lesson_id: '44444444-4444-4444-8444-444444444444', status: 'completed', correct: 3, scored: 3, spoken_status: 'failed', completed_count: 1 }),
      item({ title: 'Skipped lesson', lesson_id: '55555555-5555-4555-8555-555555555555', status: 'completed', correct: 1, scored: 3, spoken_status: 'skipped', completed_count: 1 }),
    ], { lessons_available: 4, lessons_completed: 3, attempts_completed: 3, questions_correct: 6, questions_scored: 9, recent: [
      { attempt_id: 'x', lesson_id: '33333333-3333-4333-8333-333333333333', title: 'Pending lesson', cefr: 'A2', status: 'completed', when, correct: 2, scored: 3, spoken_status: 'pending' }] });
    await page.goto('/listening');
    const card = (name: string) => lessonCard(page, new RegExp(name));
    await expect(card('In progress lesson')).toContainText('In progress · 2 of 4 answered');
    await expect(card('In progress lesson')).toContainText('Continue');
    await expect(card('Pending lesson')).toContainText('Finished · 2 of 3 correct');
    await expect(card('Pending lesson')).toContainText('Spoken answer: being checked');
    await expect(card('Pending lesson')).toContainText('See results');
    await expect(card('Failed lesson')).toContainText('Spoken answer: couldn’t be checked');
    await expect(card('Skipped lesson')).toContainText('Spoken answer: skipped');
    await expect(page.getByText('3 of 4 lessons finished')).toBeVisible();
    await expect(page.getByText('6 of 9 scored questions correct in your finished lessons. Spoken answers are not scored.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Recent activity' })).toBeVisible();
    await expect(page.getByRole('link', { name: /Pending lesson · Today/ })).toContainText('2 of 3 correct · spoken answer being checked');
    for (const word of ['streak', 'mastery', 'proficiency', 'level up']) await expect(page.getByText(new RegExp(word, 'i'))).toHaveCount(0);
  });

  test('empty, loading and error states are intentional and recoverable', async ({ page }) => {
    await mockLibrary(page, []);
    await page.goto('/listening');
    await expect(page.getByRole('status').filter({ hasText: 'No lessons yet.' })).toBeVisible();
    await page.unroute('**/api/listening/lessons');
    await page.unroute('**/api/listening/progress');
    // loading: a slow response shows a labelled placeholder, not a blank page
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    await page.route('**/api/listening/lessons', async route => { await held; await route.fulfill({ json: [item({})] }); });
    await page.route('**/api/listening/progress', route => route.fulfill({ json: NO_PROGRESS }));
    await page.goto('/listening');
    await expect(page.getByRole('status').filter({ hasText: 'Loading lessons…' })).toBeAttached();
    await expect(page.getByRole('heading', { name: 'Listening lessons' })).toBeVisible();
    release();
    await expect(lessonCard(page, /At the pharmacy/)).toBeVisible();
    // error then retry
    await page.unroute('**/api/listening/lessons');
    await page.route('**/api/listening/lessons', route => route.abort());
    await page.reload();
    await expect(page.getByRole('alert').filter({ hasText: 'couldn’t load your lesson' })).toBeVisible();
    await page.unroute('**/api/listening/lessons');
    await mockLibrary(page, [item({})]);
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(lessonCard(page, /At the pharmacy/)).toBeVisible();
  });

  test('an account that is not on the 2.0 allow-list is told so plainly, with no retry that cannot help', async ({ page }) => {
    await page.route('**/api/listening/lessons', route => route.fulfill({ status: 404, json: { detail: 'Not found.' } }));
    await page.route('**/api/listening/progress', route => route.fulfill({ status: 404, json: { detail: 'Not found.' } }));
    await page.goto('/listening');
    const notice = page.getByRole('status').filter({ hasText: 'aren’t available on your account yet' });
    await expect(notice).toBeVisible();
    await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0);
    await expect(notice.getByRole('link', { name: 'Back to Audli' })).toBeVisible();
  });

  test('a failing progress request does not take the library down', async ({ page }) => {
    await page.route('**/api/listening/lessons', route => route.fulfill({ json: [item({})] }));
    await page.route('**/api/listening/progress', route => route.abort());
    await page.goto('/listening');
    await expect(lessonCard(page, /At the pharmacy/)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Your progress' })).toHaveCount(0);
  });

  test('the library is accessible, keyboard operable and fits every width', async ({ page }) => {
    await mockLibrary(page, [item({}), item({ title: 'Finished lesson', lesson_id: '66666666-6666-4666-8666-666666666666', status: 'completed', correct: 3, scored: 3, spoken_status: 'assessed', completed_count: 1 })],
      { lessons_available: 2, lessons_completed: 1, attempts_completed: 1, questions_correct: 3, questions_scored: 3, recent: [
        { attempt_id: 'x', lesson_id: '66666666-6666-4666-8666-666666666666', title: 'Finished lesson', cefr: 'A2', status: 'completed', when: new Date().toISOString(), correct: 3, scored: 3, spoken_status: 'assessed' }] });
    await page.goto('/listening');
    await expect(lessonCard(page, /At the pharmacy/)).toBeVisible();
    const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(result.violations.map(v => `${v.id} ${v.nodes.map(n => `${n.target} ${n.any[0]?.message ?? ''}`).join(' | ')}`)).toEqual([]);
    for (const [width, height] of [[390, 844], [320, 640], [1280, 900]]) {
      await page.setViewportSize({ width, height });
      expect(await noHorizontalScroll(page), `width ${width}`).toBe(true);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    for (const link of await lessonCard(page, /pharmacy|Finished lesson/).all()) {
      const box = (await link.boundingBox())!;
      expect(Math.min(box.width, box.height)).toBeGreaterThanOrEqual(44);
    }
    await page.getByRole('link', { name: 'Back to Audli home' }).focus();
    await page.keyboard.press('Tab');
    await expect(lessonCard(page, /At the pharmacy/)).toBeFocused();       // first interactive thing after the top bar is the first lesson
  });
});

test.describe.serial('2.0 library with the real API and worker', () => {
  test('start, leave halfway, continue exactly where you were, finish, review: one attempt, nothing lost', async ({ page, request }) => {
    await page.goto('/listening');
    await lessonCard(page, /At the pharmacy/).click();
    await freshAttempt(page);
    const lessonId = page.url().match(/\/listening\/([0-9a-f-]{36})/)![1];
    const attempts = async () => (await (await request.get(`/api/listening/lessons/${lessonId}/attempts/current`)).json()).attempt_id as string;
    await page.getByRole('button', { name: 'Start exercises' }).click();
    await page.getByLabel('Penicillin').check();
    await page.getByRole('button', { name: 'Check answer' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Correct' }).first()).toBeVisible();
    const attemptId = await attempts();

    await page.getByRole('link', { name: 'Leave lesson' }).click();                       // leave halfway, straight back to the library
    const card = lessonCard(page, /At the pharmacy/);
    await expect(card).toContainText('In progress · 1 of 4 answered');
    await expect(card).toContainText('Continue');
    await card.click();
    await expect(page.getByRole('heading', { name: /In your own words/ })).toBeVisible();   // straight to the next question: no intro, no repeat
    await expect(page.getByText('What is Tom allergic to?')).toHaveCount(0);
    expect(await attempts()).toBe(attemptId);                                               // no second attempt

    await page.reload();                                                                    // a refresh changes nothing either
    await expect(page.getByRole('heading', { name: /In your own words/ })).toBeVisible();
    expect(await attempts()).toBe(attemptId);

    await page.locator('input[type=file]').setInputFiles({ name: 'answer.wav', mimeType: 'audio/wav', buffer: wav() });
    await page.getByRole('button', { name: 'Send answer' }).click();
    await expect(page.getByRole('heading', { name: 'Answer sent' })).toBeVisible();
    await page.getByRole('button', { name: 'Next question' }).click();
    await page.getByLabel('Missing word').fill('honey');
    await page.getByRole('button', { name: 'Check answer' }).click();
    await page.getByRole('button', { name: 'Next question' }).click();
    await move(page, 'Tom says he has a cough', 'up').click(); await move(page, 'Tom says he has a cough', 'up').click();
    await move(page, 'Lena asks about allergies', 'up').click(); await move(page, 'Lena asks about allergies', 'up').click();
    await move(page, 'Lena explains how often to take the syrup', 'up').click();
    await page.getByRole('button', { name: 'Check order' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Correct order' })).toBeVisible();
    await page.getByRole('button', { name: 'See your summary' }).click();
    await expect(page.getByText('3 of 3 correct')).toBeVisible();
    await expect(page.getByText('What Audli heard')).toBeVisible({ timeout: 60000 });   // the worker finished it in the background

    await page.getByRole('link', { name: 'Back to lessons' }).click();
    const done = lessonCard(page, /At the pharmacy/);
    await expect(done).toContainText('Finished · 3 of 3 correct');
    await expect(done).toContainText('Spoken answer: checked');
    await expect(done).toContainText('See results');
    await expect(page.getByRole('heading', { name: 'Recent activity' })).toBeVisible();
    await expect(page.getByRole('link', { name: /At the pharmacy · Today/ }).first()).toContainText('3 of 3 correct');   // newest first; earlier runs may add rows
    const progress = await (await request.get('/api/listening/progress')).json();
    expect(progress.lessons_completed).toBeGreaterThanOrEqual(1);
    expect(Object.keys(progress).sort()).toEqual(['attempts_completed', 'lessons_available', 'lessons_completed', 'questions_correct', 'questions_scored', 'recent']);

    await done.click();                                                                      // "See results" opens the saved summary, it does not restart
    await expect(page).toHaveURL(/\/listening\/[0-9a-f-]{36}/);
    await expect(page.getByText('3 of 3 correct')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start exercises' })).toHaveCount(0);
    expect(await attempts()).toBe(attemptId);
  });

  test('"Listen again" is the only way to start over, and it keeps the finished attempt', async ({ page, request }) => {
    await page.goto('/listening');
    await lessonCard(page, /At the pharmacy/).click();
    await freshAttempt(page);
    await expect(page.getByRole('button', { name: 'Start exercises' })).toBeVisible();
    await page.getByRole('link', { name: 'Leave lesson' }).click();
    await expect(lessonCard(page, /At the pharmacy/)).toContainText('In progress · 0 of 4 answered');
    const progress = await (await request.get('/api/listening/progress')).json();
    expect(progress.attempts_completed).toBeGreaterThanOrEqual(1);                        // the earlier result is still counted
  });
});
