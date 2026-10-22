import { test, expect, type Page } from '@playwright/test';
import { installVoice } from './voice-fixture';
import type { ImprovementState, LessonReport, ReviewDashboard, ReviewLesson } from '../lib/review';

const lesson: ReviewLesson = { id: 'lesson-one', kind: 'lesson', completed: true, completed_at: '2026-10-14T12:00:00Z',
  title: 'A change to the Monday meeting', topics: ['Work meetings'], levels: ['B1'], takeaway: 'Understanding was demonstrated in your responses.', assessed_passages: 1 };
const dashboard: ReviewDashboard = { summary: { completed_lessons: 1, structured_lessons: 1, earlier_practice: 0, practice_days: 1, undated_lessons: 0, timezone_used: 'UTC', timezone_fallback: false, current_practice_level: 'B1' },
  insight: 'Complete a few more lessons to discover your listening patterns.', patterns_version: 'review-patterns-v1',
  patterns: ['main_idea', 'details', 'vocabulary', 'inference'].map(dimension => ({ dimension, title: dimension, label: 'Not enough evidence' as const, observations: 0, practice_days: 0, first_date: null, last_date: null, context: null })),
  lessons: [lesson], assessed_practice: [], next_cursor: null };
const ANSWER = 'She change the meeting to Monday because the team was sick';
const ready: ImprovementState = { state: 'ready', reason: null, unchanged: false,
  improved_text: 'She changed the meeting to Monday because the team was sick',
  notes: [{ kind: 'grammar', before: 'She change', after: 'She changed', why: 'Use the past tense for a finished action.' }],
  diff: [{ op: 'equal', text: 'She ' }, { op: 'delete', text: 'change' }, { op: 'insert', text: 'changed' }, { op: 'equal', text: ' the meeting to Monday because the team was sick' }] };
const available: ImprovementState = { state: 'available', reason: null, improved_text: null, unchanged: false, notes: [], diff: [] };
const report = (improvement: ImprovementState | null | undefined): LessonReport => ({ lesson, passages: [{ exercise_id: 'exercise-one', title: 'The Monday meeting', topic: 'Work meetings', level: 'B1', accent: null, accent_fallback: false,
  units: [{ dimension: 'main_idea', index: 0, status: 'demonstrated', meaning: 'The team changed the meeting.', evidence: 'She change the meeting' }],
  answers: [{ attempt_id: 'attempt-one', question: null, text: ANSWER, uncertain: false }, { attempt_id: 'attempt-two', question: 'When does it start?', text: 'It starts at nine', uncertain: false }],
  assessment_available: true, saved_feedback: null, next_focus: 'Keep practicing.', adaptation_reason: null,
  transcript_url: '/api/exercises/exercise-one/transcript', audio_url: null, ...(improvement === undefined ? {} : { improvement }) }] });

type Reply = { status: number; json: unknown; delay?: number };
async function setup(page: Page, initial: ImprovementState | null | undefined, replies: Reply[] = []) {
  await installVoice(page);
  const posts: string[] = [], writes: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') writes.push(`${request.method()} ${path}`);
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'local' } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya', completed_attempts: 4 }, provider: 'demo' } });
    if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
    if (path === '/api/history') return route.fulfill({ json: [] });
    if (path === '/api/plan') return route.fulfill({ status: 503, json: { detail: 'No check-in due.' } });
    if (path === '/api/review') return route.fulfill({ json: dashboard });
    if (path === '/api/review/lesson/lesson-one') return route.fulfill({ json: report(initial) });
    if (path === '/api/exercises/exercise-one/review/improvement' && request.method() === 'POST') {
      posts.push(path);
      const reply = replies.shift() ?? { status: 200, json: { improvement: ready } };
      if (reply.delay) await new Promise(resolve => setTimeout(resolve, reply.delay));
      return route.fulfill({ status: reply.status, json: reply.json });
    }
    return route.fulfill({ status: 404, json: { detail: 'Unexpected request' } });
  });
  await page.goto('/'); await page.getByRole('button', { name: 'Review', exact: true }).click();
  await page.getByRole('button', { name: /A change to the Monday meeting/ }).click();
  return { posts, writes };
}
const heard = (page: Page) => page.getByRole('region', { name: 'What Audli heard' });

test('offers coaching without calling anything, then shows the four labeled parts', async ({ page }) => {
  const fixture = await setup(page, available);
  await expect(heard(page).getByRole('group', { name: 'Improve my answer' })).toContainText('never changes your progress');
  expect(fixture.posts).toEqual([]);
  await heard(page).getByRole('button', { name: 'Show an improved version' }).click();
  await expect(heard(page).getByRole('heading', { name: 'Your answer, improved' })).toBeVisible();
  await expect(heard(page).getByRole('heading', { name: 'What changed' })).toBeVisible();
  await expect(heard(page).getByRole('heading', { name: 'Why' })).toBeVisible();
  await expect(heard(page).getByRole('heading', { name: 'What Audli heard' })).toBeVisible();
  await expect(heard(page).locator('.review-improved .review-script')).toHaveText('She changed the meeting to Monday because the team was sick');
  await expect(heard(page).getByText(ANSWER, { exact: true })).toBeVisible();          // the original is unchanged and still shown
  await expect(heard(page).getByText('Grammar:')).toBeVisible();
  await expect(heard(page).getByText(/Language coaching only/)).toBeVisible();
  expect(fixture.posts).toHaveLength(1);
  expect(fixture.writes).toEqual(['POST /api/exercises/exercise-one/review/improvement']);
  // No synthesized audio in this phase.
  await expect(page.getByRole('button', { name: /listen|play|speak|hear/i }).filter({ hasText: /improved/i })).toHaveCount(0);
  await expect(heard(page).locator('audio')).toHaveCount(0);
});

test('changes are readable without color: struck-through and underlined, with screen-reader labels', async ({ page }) => {
  await setup(page, ready);
  const diff = heard(page).getByLabel('Changes between your answer and the improved answer');
  await expect(diff.locator('del')).toHaveText('Removed: change');
  await expect(diff.locator('ins')).toHaveText('Added: changed');
  expect(await diff.locator('del').evaluate(node => getComputedStyle(node).textDecorationLine)).toContain('line-through');
  expect(await diff.locator('ins').evaluate(node => getComputedStyle(node).textDecorationLine)).toContain('underline');
  await expect(heard(page).getByText('Struck-through words were removed. Underlined words were added.')).toBeVisible();
  await expect(heard(page).locator('.review-why li')).toHaveCount(1);
  if (process.env.AUDLI_REVIEW_SCREENSHOT_DIR) await heard(page).screenshot({ path: `${process.env.AUDLI_REVIEW_SCREENSHOT_DIR}/improvement-example-${page.viewportSize()?.width}.png` });
});

test('an already natural answer says so and invents nothing', async ({ page }) => {
  await setup(page, { ...ready, unchanged: true, notes: [], diff: [] });
  await expect(heard(page).getByRole('status').filter({ hasText: 'already reads naturally' })).toBeVisible();
  await expect(heard(page).getByRole('heading', { name: 'What changed' })).toHaveCount(0);
});

test('a rejected suggestion is never shown as improved English', async ({ page }) => {
  await setup(page, available, [{ status: 200, json: { improvement: { state: 'rejected', reason: 'negation_changed', improved_text: null, unchanged: false, notes: [], diff: [] } } }]);
  await heard(page).getByRole('button', { name: 'Show an improved version' }).click();
  await expect(heard(page)).toContainText("couldn't improve this answer without risking its meaning");
  await expect(heard(page).getByRole('heading', { name: 'Your answer, improved' })).toHaveCount(0);
  await expect(heard(page).getByRole('button', { name: 'Show an improved version' })).toHaveCount(0);
});

for (const [reason, copy] of [['unreliable_recognition', /wasn't sure it heard this answer correctly/], ['too_short', /too short/], ['too_long', /too long/]] as const) {
  test(`unavailable (${reason}) explains why and offers no button`, async ({ page }) => {
    const fixture = await setup(page, { ...available, state: 'unavailable', reason });
    await expect(heard(page)).toContainText(copy);
    await expect(heard(page).getByRole('button')).toHaveCount(0);
    expect(fixture.posts).toEqual([]);
  });
}

test('feature off or no saved answer renders nothing extra', async ({ page }) => {
  for (const improvement of [null, undefined]) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await setup(page, improvement);
    await expect(heard(page)).toBeVisible();
    await expect(page.getByRole('group', { name: 'Improve my answer' })).toHaveCount(0);
    await page.goto('/');
  }
});

test('pending can be checked again for free and then completes', async ({ page }) => {
  const fixture = await setup(page, { ...available, state: 'pending' }, [{ status: 202, json: { improvement: { ...available, state: 'pending' } } }, { status: 200, json: { improvement: ready } }]);
  await heard(page).getByRole('button', { name: 'Check again' }).click();
  await expect(heard(page).getByRole('status')).toContainText('still preparing');
  await heard(page).getByRole('button', { name: 'Check again' }).click();
  await expect(heard(page).getByRole('heading', { name: 'Your answer, improved' })).toBeVisible();
  expect(fixture.posts).toHaveLength(2);
});

test('only one request is in flight and the button is disabled meanwhile', async ({ page }) => {
  const fixture = await setup(page, available, [{ status: 200, json: { improvement: ready }, delay: 600 }]);
  const button = heard(page).getByRole('button', { name: 'Show an improved version' });
  await button.click();
  await expect(button).toBeDisabled();
  await button.click({ force: true, timeout: 500 }).catch(() => undefined);
  await expect(heard(page).getByRole('heading', { name: 'Your answer, improved' })).toBeVisible();
  expect(fixture.posts).toHaveLength(1);
});

test('the daily limit and provider failures are explained and recoverable', async ({ page }) => {
  const fixture = await setup(page, available, [
    { status: 429, json: { detail: 'You have used today’s language suggestions. More open in about 5 hours.', improvement: { code: 'improvement_limit', retry_after_seconds: 18000 } } },
    { status: 503, json: { detail: "Audli couldn't improve this answer right now. Try again in a moment." } },
    { status: 200, json: { improvement: ready } }]);
  const button = heard(page).getByRole('button', { name: 'Show an improved version' });
  await button.click();
  await expect(heard(page).getByRole('alert')).toContainText('More open in about 5 hours');
  await button.click();
  await expect(heard(page).getByRole('alert')).toContainText("couldn't improve this answer right now");
  await button.click();
  await expect(heard(page).getByRole('heading', { name: 'Your answer, improved' })).toBeVisible();
  await expect(heard(page).getByRole('alert')).toHaveCount(0);
  expect(fixture.posts).toHaveLength(3);
});

test('leaving Review while a request is running discards its result without errors', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await setup(page, available, [{ status: 200, json: { improvement: ready }, delay: 700 }]);
  await heard(page).getByRole('button', { name: 'Show an improved version' }).click();
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await page.waitForTimeout(900);
  await expect(page.getByRole('heading', { name: /Good to see you/ })).toBeVisible();
  expect(errors).toEqual([]);
});

test('keyboard operation and labeled landmarks', async ({ page }) => {
  await setup(page, available);
  const button = heard(page).getByRole('button', { name: 'Show an improved version' });
  await button.focus(); await page.keyboard.press('Enter');
  await expect(heard(page).getByRole('heading', { name: 'Why' })).toBeVisible();
});

for (const width of [320, 390, 1280]) {
  test(`comparison fits without horizontal scrolling at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const long = 'word '.repeat(60).trim();
    await setup(page, { ...ready, improved_text: long, diff: [{ op: 'equal', text: long }],
      notes: [{ kind: 'vocabulary', before: 'a very long verbatim phrase '.repeat(4).trim(), after: 'another long replacement phrase '.repeat(4).trim(), why: 'A reason that is long enough to wrap onto several lines on a narrow phone screen.' }] });
    await expect(heard(page).getByRole('heading', { name: 'Why' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (process.env.AUDLI_REVIEW_SCREENSHOT_DIR) await heard(page).screenshot({ path: `${process.env.AUDLI_REVIEW_SCREENSHOT_DIR}/improvement-ready-${width}.png` });
  });
}
