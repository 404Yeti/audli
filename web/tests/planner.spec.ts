import { test, expect } from '@playwright/test';
import { installVoice, promptWav } from './voice-fixture';

test('plan shows roadmap and saves a spoken check-in before refreshing; skip is persistent', async ({ page }) => {
  await installVoice(page, { autoPlayback: true });
  let status = 'due', revision = 1, uploads = 0, answers = 0, audioCalls = 0;
  const plan = () => ({ revision, completed_lessons: 3, block_progress: 0, focus: 'Catch details for meetings', readiness: 'ready',
    upcoming: ['Meeting changes', 'Travel arrangements', 'Science news'].map((title, i) => ({ number: i + 4, title: status === 'answered' ? `Travel practice ${i + 1}` : title, topic: 'travel', focus: 'details', objective: ['Catch the key names, times and changes in an update.', 'Follow important steps in order.', 'Notice what happened and which details explain it.'][i], difficulty: { speech_rate: .9, duration_seconds: 45, vocabulary_level: 'B1', information_density: 1 }, accent_focus: i === 0 ? 'british' : null })),
    completed: [{ id: 'past', title: 'Work meetings', focus: 'details', completed_at: '2026-10-10T12:00:00Z' }],
    checkin: { status, after_lesson: status === 'due' ? 3 : null, available: status === 'due', prompt: 'How did the difficulty feel? Would you like different topics?', pending: uploads && !answers && status === 'due' ? { text: 'More travel please', confidence: .99, uncertainty: [] } : null } });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'local' } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya', goal: 'Meetings', completed_attempts: 9 }, provider: 'demo' } });
    if (path === '/api/exercises/exercise-past/transcript') return route.fulfill({ json: { title: 'Work meetings', script: 'The meeting has moved to Tuesday.' } });
    if (path === '/api/history') return route.fulfill({ json: [{ id: 'history-past', exercise_id: 'exercise-past', created_at: '2026-10-10T12:00:00Z', evaluation: { feedback: 'You understood the meeting changes.' } }] });
    if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
    if (path === '/api/plan/checkin/audio') { audioCalls++; return route.fulfill({ contentType: 'audio/wav', body: promptWav() }); }
    if (path === '/api/plan/checkin/attempts') { uploads++; revision++; return route.fulfill({ json: plan() }); }
    if (path === '/api/plan/checkin/answer') { answers++; status = 'answered'; revision++; return route.fulfill({ json: plan() }); }
    if (path === '/api/plan/checkin/skip') { status = 'skipped'; revision++; return route.fulfill({ json: plan() }); }
    if (path === '/api/plan') return route.fulfill({ json: plan() });
    return route.fulfill({ status: 404 });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Review your next lessons' }).click();
  await expect(page.getByRole('heading', { name: 'Next three lessons' })).toBeVisible();
  await expect(page.getByText(/toward your next check-in|Next optional check-in|Listening check-in|lessons completed/)).toHaveCount(0);
  await expect(page.getByText('Intermediate · Short listening practice · Slower pace')).toHaveCount(3);
  await expect(page.getByText(/45-second|90%|B1/)).toHaveCount(0);
  await expect(page.getByText('Optional accent interest:', { exact: false })).toContainText('Audli will ask');
  if (process.env.AUDLI_PLAN_SCREENSHOT) await page.screenshot({ path: process.env.AUDLI_PLAN_SCREENSHOT, fullPage: true });
  await page.getByRole('button', { name: 'Share feedback' }).click();
  await expect(page.getByRole('heading', { name: 'Travel practice 1' })).toBeVisible({ timeout: 15000 });
  expect(uploads).toBe(1); expect(answers).toBe(1); expect(audioCalls).toBe(1);
  await expect(page.getByRole('heading', { name: 'Travel practice 1' })).toBeVisible();
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Share feedback' })).toHaveCount(0);
  status = 'due'; uploads = 0; answers = 0; revision++;
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await page.getByRole('button', { name: 'Not now' }).click();
  await expect(page.getByRole('button', { name: 'Not now' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Completed lessons' })).toHaveCount(0);
  await expect(page.getByText('Work meetings', { exact: false })).toHaveCount(0);
  await page.getByRole('button', { name: 'Review', exact: true }).click();
  await expect(page.getByText('You understood the meeting changes.')).toBeVisible();
  await page.getByRole('button', { name: 'Read transcript' }).click();
  await expect(page.getByText('The meeting has moved to Tuesday.')).toBeVisible();
  await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Not now' })).toHaveCount(0);
  expect(audioCalls).toBe(1);
});
