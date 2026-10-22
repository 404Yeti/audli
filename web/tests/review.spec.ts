import { test, expect, type Page } from '@playwright/test';
import { installVoice, promptWav } from './voice-fixture';
import type { ReviewDashboard, ReviewLesson, LessonReport } from '../lib/review';

const first: ReviewLesson = { id: 'lesson-one', kind: 'lesson', completed: true, completed_at: '2026-10-14T12:00:00Z',
  title: 'A change to the Monday meeting', topics: ['Work meetings'], levels: ['B1'], takeaway: 'Some information was not demonstrated.', assessed_passages: 1 };
const older: ReviewLesson = { ...first, id: 'lesson-old', title: 'Earlier listening lesson', completed_at: null, levels: [], takeaway: 'Detailed assessment unavailable for this lesson.' };
const dashboard: ReviewDashboard = { summary: { completed_lessons: 2, structured_lessons: 2, earlier_practice: 0, practice_days: 1, undated_lessons: 1, timezone_used: 'America/Los_Angeles', timezone_fallback: false, current_practice_level: 'B1' },
  insight: 'Complete a few more lessons to discover your listening patterns.', patterns_version: 'review-patterns-v1',
  patterns: ['Main ideas', 'Supporting details', 'Words in context', 'Implied meaning'].map((title, i) => ({ dimension: ['main_idea', 'details', 'vocabulary', 'inference'][i], title, label: 'Not enough evidence', observations: 0, practice_days: 0, first_date: null, last_date: null, context: null })),
  lessons: [first], assessed_practice: [], next_cursor: 'lesson:lesson-one' };
const report: LessonReport = { lesson: first, passages: [{ exercise_id: 'exercise-one', title: 'The Monday meeting', topic: 'Work meetings', level: 'B1', accent: null, accent_fallback: false,
  units: [{ dimension: 'main_idea', index: 0, status: 'demonstrated', meaning: 'The team changed the meeting.', evidence: 'They change meeting.' },
    { dimension: 'details', index: 0, status: 'partial', meaning: 'The meeting moved to Monday morning.', evidence: 'Monday' },
    { dimension: 'details', index: 1, status: 'misunderstood', meaning: 'It starts at nine.', evidence: 'It starts ten.' },
    { dimension: 'inference', index: 0, status: 'not_demonstrated', meaning: 'The room needed repairs.', evidence: null }],
  answers: [{ attempt_id: 'attempt-one', question: null, text: 'They change meeting to Monday.', uncertain: false },
    { attempt_id: 'attempt-two', question: 'When does it start?', text: 'It starts ten.', uncertain: true }],
  assessment_available: true, saved_feedback: 'Saved feedback from this assessment.', next_focus: 'Replay to check the supporting details.', adaptation_reason: 'Evidence remains incomplete; keep difficulty stable.',
  transcript_url: '/api/exercises/exercise-one/transcript', audio_url: '/api/exercises/exercise-one/audio' }] };

type Options = { empty?: boolean; failed?: boolean; delayed?: boolean; staleAudio?: boolean; longTitle?: boolean };
async function setup(page: Page, options: Options = {}) {
  await installVoice(page);
  let reviews = 0, transcripts = 0, replays = 0;
  const writes: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (request.method() !== 'GET') writes.push(path);
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'local' } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya', completed_attempts: 4 }, provider: 'demo' } });
    if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
    if (path === '/api/history') return route.fulfill({ json: [] });
    if (path === '/api/plan') return route.fulfill({ status: 503, json: { detail: 'No check-in due.' } });
    if (path === '/api/review') {
      reviews++;
      if (options.delayed) await new Promise(resolve => setTimeout(resolve, 700));
      if (options.failed) return route.fulfill({ status: 503, json: { detail: 'Your review is temporarily unavailable.' } });
      const data = structuredClone(dashboard);
      if (options.empty) { data.summary.completed_lessons = 0; data.summary.structured_lessons = 0; data.summary.practice_days = 0; data.summary.undated_lessons = 0; data.summary.current_practice_level = null; data.lessons = []; data.next_cursor = null; }
      if (options.longTitle) data.lessons[0].title = 'An unexpectedly long lesson title '.repeat(12);
      if (url.searchParams.get('cursor')) { data.lessons = [older]; data.next_cursor = null; }
      return route.fulfill({ json: data });
    }
    if (path === '/api/review/lesson/lesson-one') return route.fulfill({ json: report });
    if (path === '/api/review/lesson/lesson-old') return route.fulfill({ json: { lesson: older, passages: [{ ...report.passages[0], exercise_id: 'exercise-old', title: 'Earlier passage', units: [], answers: [], assessment_available: false, saved_feedback: null, audio_url: null, transcript_url: '/api/exercises/exercise-old/transcript' }] } });
    if (path.endsWith('/transcript')) { transcripts++; return route.fulfill({ json: { title: 'Original passage', script: 'The team changed the meeting to Monday at nine.' } }); }
    if (path.endsWith('/audio')) { replays++; return options.staleAudio ? route.fulfill({ status: 404, json: { detail: 'Audio missing.' } }) : route.fulfill({ contentType: 'audio/wav', body: promptWav() }); }
    return route.fulfill({ status: 404, json: { detail: 'Unexpected request' } });
  });
  await page.goto('/'); await page.getByRole('button', { name: 'Review', exact: true }).click();
  return { counts: () => ({ reviews, transcripts, replays }), writes, recover: () => { options.failed = false; } };
}

test('overview, detail, independent transcript toggle, replay and back navigation', async ({ page }) => {
  const fixture = await setup(page);
  await expect(page.getByRole('heading', { name: 'Your listening journey' })).toBeVisible();
  await expect(page.getByText("See what you're understanding and where you're growing.")).toBeVisible();
  await expect(page.getByLabel('Practice summary')).toContainText('2');
  await page.getByText('About your practice totals').click();
  await expect(page.getByText(/America\/Los_Angeles/)).toBeVisible();
  await page.getByRole('button', { name: /A change to the Monday meeting/ }).click();
  await expect(page.getByRole('heading', { name: 'What you understood well' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Incorrect understanding' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Incomplete understanding' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Information not demonstrated' })).toBeVisible();
  await expect(page.getByText(/This does not mean you misunderstood it/)).toBeVisible();
  expect(fixture.counts().transcripts).toBe(0);
  const transcript = page.getByRole('button', { name: 'Passage transcript' });
  await transcript.click(); await expect(transcript).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('The team changed the meeting to Monday at nine.')).toBeVisible();
  await transcript.click(); await expect(page.getByText('The team changed the meeting to Monday at nine.')).toHaveCount(0);
  await transcript.click(); expect(fixture.counts().transcripts).toBe(1);
  await page.getByRole('button', { name: 'Replay original passage' }).click();
  await expect.poll(() => fixture.counts().replays).toBe(1);
  await page.getByRole('button', { name: 'Back to Review' }).click();
  await expect(page.getByRole('button', { name: /A change to the Monday meeting/ })).toBeFocused();
  await expect(page.getByText('The team changed the meeting to Monday at nine.')).toHaveCount(0);
  expect(fixture.writes).toEqual([]);
});

test('shows the saved response as what Audli heard, verbatim and without corrections', async ({ page }) => {
  const fixture = await setup(page);
  await page.getByRole('button', { name: /A change to the Monday meeting/ }).click();
  const heard = page.getByRole('region', { name: 'What Audli heard' });
  await expect(heard.getByRole('heading', { name: 'What Audli heard' })).toBeVisible();
  await expect(heard).toContainText('They change meeting to Monday.');
  await expect(heard).toContainText('In reply to: When does it start?');
  await expect(heard).toContainText('It starts ten.');
  await expect(heard).toContainText('may differ from what you said');
  await expect(heard.getByText(/Speech recognition was unsure/)).toHaveCount(1);
  await expect(heard.getByRole('button')).toHaveCount(0);
  await expect(page.getByText(/improved|corrected|better way to say/i)).toHaveCount(0);
  expect(fixture.writes).toEqual([]);
});

test('a report from an API without saved answers still renders and shows no heard section', async ({ page }) => {
  await setup(page);
  await page.route('**/api/review/lesson/lesson-one', async route => {
    const legacy = structuredClone(report); delete (legacy.passages[0] as { answers?: unknown }).answers;
    return route.fulfill({ json: legacy });
  });
  await page.getByRole('button', { name: /A change to the Monday meeting/ }).click();
  await expect(page.getByRole('heading', { name: 'What you understood well' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'What Audli heard' })).toHaveCount(0);
});

test('older lessons without a saved response say so and never invent one', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: 'Show older lessons' }).click();
  await page.getByRole('button', { name: /Earlier listening lesson/ }).click();
  await expect(page.getByRole('region', { name: 'What Audli heard' })).toContainText("Your saved response isn't available for this older lesson.");
});

test('patterns are qualitative and insufficient evidence is explicit', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: 'Explore listening patterns' }).click();
  await expect(page.getByRole('heading', { name: 'Your listening patterns' })).toBeFocused();
  await expect(page.getByText('Not enough evidence', { exact: true })).toHaveCount(4);
  await expect(page.getByText(/at least three fully observed lessons/)).toBeVisible();
  await expect(page.locator('.review-hub').getByRole('img')).toHaveCount(0);
  await page.getByRole('button', { name: 'Back to Review' }).click();
  await expect(page.getByRole('button', { name: 'Explore listening patterns' })).toBeFocused();
});

test('older records retain limited reports and transcript access without original audio', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: 'Show older lessons' }).click();
  await expect(page.getByText('Date unavailable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Earlier listening lesson/ }).click();
  await expect(page.getByText(/Detailed assessment unavailable for this passage/)).toBeVisible();
  await expect(page.getByText(/original recording is no longer available/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Replay original passage' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Passage transcript' }).click();
  await expect(page.getByText('The team changed the meeting to Monday at nine.')).toBeVisible();
});

test('empty state has no fictional feedback or chart', async ({ page }) => {
  await setup(page, { empty: true });
  await expect(page.getByRole('heading', { name: 'Your journey starts here' })).toBeVisible();
  await expect(page.getByText('Complete a few more lessons to discover your listening patterns.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Completed lessons', exact: true })).toHaveCount(0);
  await expect(page.getByText(/Consistent strength|getting better|%/)).toHaveCount(0);
});

test('loading and retry restore review after a read failure', async ({ page }) => {
  const fixture = await setup(page, { failed: true, delayed: true });
  await expect(page.getByRole('status', { name: '' }).filter({ hasText: 'Loading your listening review' })).toBeVisible();
  await expect(page.locator('.review-hub').getByRole('alert')).toContainText('Your review is temporarily unavailable.');
  fixture.recover();
  await page.getByRole('button', { name: 'Retry review' }).click();
  await expect(page.getByRole('button', { name: /A change to the Monday meeting/ })).toBeVisible();
});

test('missing retained audio never generates replacement speech', async ({ page }) => {
  const fixture = await setup(page, { staleAudio: true });
  await page.getByRole('button', { name: /A change to the Monday meeting/ }).click();
  await page.getByRole('button', { name: 'Replay original passage' }).click();
  await expect(page.locator('.review-hub').getByRole('alert')).toContainText('will not generate replacement audio');
  expect(fixture.writes).toEqual([]);
});

for (const width of [320, 390, 1280]) {
  test(`Review remains readable without overflow at ${width}px and keyboard navigable`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await setup(page, { longTitle: true });
    const lesson = page.getByRole('button', { name: /An unexpectedly long lesson title/ });
    await expect(lesson).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await lesson.focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'A change to the Monday meeting', exact: true })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
    if (process.env.AUDLI_REVIEW_SCREENSHOT_DIR) {
      await page.screenshot({ path: `${process.env.AUDLI_REVIEW_SCREENSHOT_DIR}/report-${width}.png`, fullPage: true });
      await page.getByRole('button', { name: 'Back to Review' }).click();
      await page.screenshot({ path: `${process.env.AUDLI_REVIEW_SCREENSHOT_DIR}/overview-${width}.png`, fullPage: true });
    }
  });
}

test('navigating away during Review loading discards the response', async ({ page }) => {
  await setup(page, { delayed: true });
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you, Maya.' })).toBeVisible();
  await page.waitForTimeout(850);
  await expect(page.getByRole('heading', { name: 'Your listening journey' })).toHaveCount(0);
});

test('an expired history cursor can recover by reloading Review', async ({ page }) => {
  await setup(page);
  await expect(page.getByRole('button', { name: /A change to the Monday meeting/ })).toBeVisible();
  await page.route('**/api/review?*', async route => {
    if (new URL(route.request().url()).searchParams.has('cursor')) return route.fulfill({ status: 422, json: { detail: 'Review history changed. Reload your review.' } });
    return route.fallback();
  });
  await page.getByRole('button', { name: 'Show older lessons' }).click();
  await expect(page.locator('.review-hub').getByRole('alert')).toContainText('Reload your review.');
  await page.getByRole('button', { name: 'Retry review' }).click();
  await expect(page.locator('.review-hub').getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /A change to the Monday meeting/ })).toBeVisible();
});
