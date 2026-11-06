import { test, expect, type Page } from '@playwright/test';
import { freshAttempt, lessonCard, settleOpenAttempt } from './listening-fixture';

test.skip(!process.env.AUDLI_LISTENING_E2E, 'run with listening.playwright.config.ts');

const TITLE = 'Ordering food at a restaurant';
const noHorizontalScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

async function openLesson(page: Page) {
  await page.goto('/listening');
  await lessonCard(page, new RegExp(TITLE)).click();
  await freshAttempt(page);
  return page.url().match(/\/listening\/([0-9a-f-]{36})/)![1];
}
async function currentAttempt(page: Page, lessonId: string): Promise<string> {
  return (await (await page.request.get(`/api/listening/lessons/${lessonId}/attempts/current`)).json()).attempt_id;
}
async function attemptsCount(page: Page, lessonId: string) {          // completed lessons recorded for this learner, from the API
  return (await (await page.request.get('/api/listening/progress')).json()).attempts_completed as number;
}

test.describe.serial('2.0 lesson success (Figma D13/M13)', () => {
  test('a finished lesson shows its real result, survives refresh, and creates no extra records', async ({ page }) => {
    const lessonId = await openLesson(page);
    await settleOpenAttempt(page, lessonId);                 // answers everything: the spoken question is skipped
    const attempt = await currentAttempt(page, lessonId);
    const before = await attemptsCount(page, lessonId);

    await page.goto(`/listening/${lessonId}/success?attempt=${attempt}`);
    await expect(page.getByRole('heading', { name: 'Lesson complete', level: 1 })).toBeVisible();
    await expect(page.getByText(TITLE)).toBeVisible();
    await expect(page.getByLabel('Level B1')).toBeVisible();
    await expect(page.getByTestId('score')).toHaveText(/^\d of 3$/);                       // deterministic questions only
    await expect(page.getByText('You skipped the spoken question.')).toBeVisible();        // words, never a number
    await expect(page.getByText(/%/)).toHaveCount(0);                                      // no invented percentage
    await expect(page.getByText(/unaided|CEFR|level up|improved/i)).toHaveCount(0);        // no invented proficiency claim
    await expect(page.getByText('American accent')).toBeVisible();
    await expect(page.getByText('Spoken answer', { exact: true })).toHaveCount(0);         // skipped is not "practised"
    await expect(page.getByRole('link', { name: 'Back to home' })).toHaveAttribute('href', '/');
    const score = await page.getByTestId('score').textContent();

    for (let i = 0; i < 3; i++) {                              // refresh and revisit as often as you like
      await page.reload();
      await expect(page.getByTestId('score')).toHaveText(score!);
    }
    await page.goto(`/listening/${lessonId}`);                 // leave and come back with the browser's back button
    await page.goBack();
    await expect(page.getByRole('heading', { name: 'Lesson complete', level: 1 })).toBeVisible();
    await expect(page.getByTestId('score')).toHaveText(score!);
    expect(await attemptsCount(page, lessonId)).toBe(before);                              // viewing is read-only: nothing was recorded
    expect(await noHorizontalScroll(page)).toBe(true);
  });

  test('Finish lesson in the summary leads to the same result; review leads back to the answers', async ({ page }) => {
    const lessonId = await openLesson(page);
    await settleOpenAttempt(page, lessonId);
    await page.goto(`/listening/${lessonId}`);
    await page.getByRole('link', { name: 'Finish lesson' }).click();
    await expect(page).toHaveURL(new RegExp(`/listening/${lessonId}/success\\?attempt=`));
    await expect(page.getByRole('heading', { name: 'Lesson complete' })).toBeVisible();
    const review = page.getByRole('link', { name: /^Review my (mistake|mistakes) \(\d\)$/ });    // the blank answered 'x' is certainly wrong
    await expect(review).toBeVisible();
    await review.click();
    await expect(page).toHaveURL(new RegExp(`/listening/${lessonId}$`));
    await expect(page.getByRole('heading', { name: 'Your answers' })).toBeVisible();
  });

  test('an unfinished lesson is never shown as complete', async ({ page }) => {
    const lessonId = await openLesson(page);
    const attempt = await currentAttempt(page, lessonId);
    await page.goto(`/listening/${lessonId}/success?attempt=${attempt}`);
    await expect(page.getByRole('heading', { name: 'You haven’t finished this lesson yet' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Lesson complete' })).toHaveCount(0);
    await page.getByRole('link', { name: 'Continue the lesson' }).click();
    await expect(page).toHaveURL(new RegExp(`/listening/${lessonId}$`));
  });

  test('missing, malformed or foreign attempt ids give a plain not-found, not an error page', async ({ page }) => {
    const lessonId = await openLesson(page);
    for (const query of ['', '?attempt=', '?attempt=../../etc', '?attempt=00000000-0000-0000-0000-000000000000']) {
      await page.goto(`/listening/${lessonId}/success${query}`);
      await expect(page.getByRole('heading', { name: 'We couldn’t find that result' })).toBeVisible();
    }
    const attempt = await currentAttempt(page, lessonId);
    await page.goto(`/listening/00000000-0000-0000-0000-000000000000/success?attempt=${attempt}`);     // right attempt, wrong lesson
    await expect(page.getByRole('heading', { name: 'We couldn’t find that result' })).toBeVisible();
  });

  test('a failing connection offers a retry that recovers', async ({ page }) => {
    const lessonId = await openLesson(page);
    await settleOpenAttempt(page, lessonId);
    const attempt = await currentAttempt(page, lessonId);
    let fail = true;
    await page.route(`**/api/listening/attempts/${attempt}`, route => fail ? route.abort() : route.continue());
    await page.goto(`/listening/${lessonId}/success?attempt=${attempt}`);
    await expect(page.getByRole('heading', { name: 'We couldn’t load your result' })).toBeVisible();
    fail = false;
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('heading', { name: 'Lesson complete' })).toBeVisible();
  });

  test('phone and desktop layouts fit, actions are comfortable to tap, keyboard reaches every action', async ({ page }) => {
    const lessonId = await openLesson(page);
    await settleOpenAttempt(page, lessonId);
    const attempt = await currentAttempt(page, lessonId);
    for (const size of [{ width: 360, height: 740 }, { width: 390, height: 844 }, { width: 1440, height: 900 }]) {
      await page.setViewportSize(size);
      await page.goto(`/listening/${lessonId}/success?attempt=${attempt}`);
      await expect(page.getByRole('heading', { name: 'Lesson complete' })).toBeVisible();
      expect(await noHorizontalScroll(page)).toBe(true);
      const box = await page.getByRole('link', { name: 'Back to home' }).boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }
    await page.keyboard.press('Tab');
    await expect(page.locator(':focus')).toBeVisible();
  });
});
