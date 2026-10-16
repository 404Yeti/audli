import { test, expect } from '@playwright/test';
import { installVoice } from './voice-fixture';

for (const width of [320, 1440]) {
  test(`optional homework in Plan preserves lessons and works with keyboard at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 950 });
    await installVoice(page, { autoPlayback: true });
    let status = 'offered', decisions = 0;
    const assignment = () => ({ id: 'assignment', lesson_id: 'lesson', status, objective: 'Listen for the main idea and two details that support it.',
      content: status === 'assigned' || status === 'completed' ? { provider: 'youtube', provider_id: 'abcdefghijk', canonical_url: 'https://www.youtube.com/watch?v=abcdefghijk', title: 'How everyday science explains our world', publisher: 'Example publisher', duration_seconds: 180, validated_at: new Date().toISOString() } : null });
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'local', homework_enabled: true } });
      if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya', goal: 'Meetings', completed_attempts: 3 }, provider: 'demo' } });
      if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
      if (path === '/api/history') return route.fulfill({ json: [] });
      if (path === '/api/plan') return route.fulfill({ json: { revision: 1, completed_lessons: 1, block_progress: 1, focus: 'Notice the details that matter', readiness: 'ready', upcoming: ['Work updates', 'Travel stories', 'Science discoveries'].map((title, i) => ({ number: i + 1, title, topic: 'science', focus: 'details', objective: 'Listen for important details.', difficulty: { speech_rate: 1, duration_seconds: 60, vocabulary_level: 'B1', information_density: 1 }, accent_focus: null })), completed: [], checkin: { available: false, status: 'not_due' } } });
      if (path === '/api/homework') return route.fulfill({ json: { enabled: true, assignments: [assignment()] } });
      if ((path.endsWith('/decision') || path.endsWith('/decision/text'))) {
        const body = route.request().postDataJSON() as { text: string };
        expect(body.text).toBe('yes about science'); decisions++; status = 'assigned';
        return route.fulfill({ json: assignment() });
      }
      if (path.endsWith('/validate')) return route.fulfill({ json: assignment() });
      if (path.endsWith('/status')) { status = route.request().postDataJSON().status; return route.fulfill({ json: assignment() }); }
      return route.fulfill({ status: 404 });
    });
    await page.goto('/'); await page.getByRole('button', { name: 'Plan', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Next three lessons' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Optional listening', exact: true })).toBeVisible();
    const topic = page.getByLabel('Your reply', { exact: true }); await topic.fill('yes about science');
    await page.getByRole('button', { name: 'Send' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'How everyday science explains our world' })).toBeVisible();
    expect(decisions).toBe(1);
    const link = page.getByRole('link', { name: 'Watch on YouTube (opens a new tab)' });
    await expect(link).toHaveAttribute('href', 'https://www.youtube.com/watch?v=abcdefghijk');
    await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    await expect(page.getByText('YouTube · About 3 min · Example publisher')).toBeVisible();
    for (const title of ['Work updates', 'Travel stories', 'Science discoveries']) await expect(page.getByRole('heading', { name: title })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `../docs/screenshots/AUD-59-plan-${width}.png`, fullPage: true });
    await page.getByRole('button', { name: 'I listened', exact: true }).focus(); await page.keyboard.press('Enter');
    await expect(page.getByText('Optional listening · Completed (self-reported)')).toBeVisible();
    await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
    await expect(page.getByText('Optional listening · Completed (self-reported)')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send' })).toHaveCount(0);
  });
}

test('disabled homework is absent and malformed provider links are never rendered', async ({ page }) => {
  await installVoice(page, { autoPlayback: true });
  let enabled = false;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'local', homework_enabled: enabled } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya' }, provider: 'demo' } });
    if (path === '/api/plan') return route.fulfill({ json: { focus: 'Listening', readiness: 'ready', upcoming: [], checkin: { available: false } } });
    if (path === '/api/homework') return route.fulfill({ json: { enabled, assignments: enabled ? [{ id: 'bad', status: 'assigned', objective: 'Listen.', content: { provider: 'youtube', provider_id: 'abcdefghijk', canonical_url: 'javascript:alert(1)', title: '<img src=x onerror=alert(1)>', publisher: 'Untrusted title', duration_seconds: 180, validated_at: new Date().toISOString() } }] : [] } });
    return route.fulfill({ json: null });
  });
  await page.goto('/'); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your listening plan' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Optional listening' })).toHaveCount(0);
  enabled = true; await page.reload(); await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await expect(page.getByRole('heading', { name: '<img src=x onerror=alert(1)>' })).toBeVisible();
  await expect(page.locator('.homework-card img')).toHaveCount(0);
  await expect(page.locator('.homework-card a')).toHaveCount(0);
});
