import { test, expect, type Page } from '@playwright/test';
import { installVoice, promptWav, probe, endAudio } from './voice-fixture';

const user = { id: '00000000-0000-0000-0000-000000000034', email: 'test@example.com',
  email_confirmed_at: '2026-10-06T00:00:00Z', app_metadata: { provider: 'email', providers: ['email'] }, user_metadata: {}, aud: 'authenticated', created_at: '2026-10-06T00:00:00Z' };
const token = [Buffer.from('{"alg":"HS256"}').toString('base64url'), Buffer.from(JSON.stringify({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url'), 'fixture'].join('.');
async function setup(page: Page, options: { failedReset?: boolean; unconfirmedReset?: boolean; expiredReset?: boolean; lesson?: boolean; delayEnd?: boolean } = {}) {
  await installVoice(page);
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  let onboarding = false;
  let lesson = options.lesson ? { id: 'unfinished', status: 'paused', phase: 'WELCOME', revision: 0,
    prompt: 'How has your day been?', pending: null, exercise: null, elapsed_seconds: 0, remaining_seconds: 600,
    server_time: new Date().toISOString(), focus: 'details', exercises_completed: 0, strength: null,
    improvement_focus: null, encouragement: 'Thanks for listening.' } : null;
  await page.route('https://audli-auth-test.supabase.co/**', route => {
    if (new URL(route.request().url()).pathname.endsWith('/logout')) return route.fulfill({ status: 204 });
    return route.fulfill({ json: { access_token: token, refresh_token: 'fixture-refresh', expires_in: 3600, token_type: 'bearer', user } });
  });
  let releaseEnd: (() => Promise<void>) | undefined;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'supabase' } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: onboarding ? 'onboarding' : 'session_ready', profile: { name: 'Robin', goal: 'Meetings', completed_attempts: 2 }, provider: 'demo' } });
    if (path === '/api/history') return route.fulfill({ json: [] });
    if (path === '/api/exercises/current') return route.fulfill({ json: null });
    if (path === '/api/lessons/current') return route.fulfill({ json: lesson });
    if (path.startsWith('/api/lessons/')) {
      if (path.endsWith('/audio')) return route.fulfill({ contentType: 'audio/wav', body: promptWav() });
      if (path.endsWith('/resume')) lesson!.status = 'active';
      if (path.endsWith('/end')) {
        if (options.delayEnd) return new Promise<void>(resolve => { releaseEnd = async () => {
          await route.fulfill({ json: { ...lesson, status: 'paused' } }).catch(() => {}); resolve();
        }; });
        lesson!.status = 'paused';
      }
      writes.push({ path, body: {} });
      return route.fulfill({ json: lesson });
    }
    if (path === '/api/onboarding/reset-controls') return route.fulfill({ json: { restart_available: true, onboarding_revision: 12,
      reset_description: 'Your preferred name, goal, interests, accent preferences, and onboarding answers reset. Your account, lessons, attempts, learning scores, difficulty, and accessibility settings stay saved.' } });
    if (path === '/api/onboarding/restart') {
      writes.push({ path, body: route.request().postDataJSON() });
      if (options.expiredReset) return route.fulfill({ status: 401, json: { detail: 'Your session expired or is invalid. Please sign in again.' } });
      if (options.unconfirmedReset) return route.fulfill({ json: { destination: 'onboarding', revision: 12 } });
      if (options.failedReset) return route.fulfill({ status: 409, json: { detail: 'Your account is saving another step. Wait and retry.' } });
      onboarding = true; lesson = null; return route.fulfill({ json: { destination: 'onboarding', revision: 13 } });
    }
    if (path.startsWith('/api/onboarding')) {
      if (path.endsWith('/audio')) return route.fulfill({ contentType: 'audio/wav', body: promptWav() });
      return route.fulfill({ json: { profile: { name: 'Listener' }, stage: 'identity', revision: 13, welcome: 'introduction', name_entry_available: false,
        name_question_revision: 13, prompt: 'Welcome to Audli. Let’s get to know you.', clarification_paused: false, pending: null, destination: 'onboarding' } });
    }
    return route.fulfill({ status: 404, json: { detail: 'Unexpected request' } });
  });
  await page.goto('/'); await page.getByRole('button', { name: 'I already have an account' }).click();
  await page.getByLabel('Email', { exact: true }).fill(user.email); await page.getByLabel('Password', { exact: true }).fill('fixture-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you, Robin.' })).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Delete Account', exact: true })).toHaveCount(0);
  return Object.defineProperty(writes, 'releaseEnd', { value: () => releaseEnd?.() }) as typeof writes & { releaseEnd: () => Promise<void> | undefined };
}

test('authorized reset explains preserved data and enters onboarding', async ({ page }) => {
  const writes = await setup(page); await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('completed lessons and learning history');
  await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
  await page.getByRole('button', { name: 'Restart onboarding', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Let’s make this yours.' })).toBeVisible({ timeout: 15000 });
  expect(writes).toEqual([{ path: '/api/onboarding/restart', body: { confirmation: 'RESTART ONBOARDING', revision: 12 } }]);
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).not.toBeNull();
});

test('ordinary authenticated learners see reset', async ({ page }) => {
  await setup(page); await expect(page.getByRole('button', { name: 'Restart Onboarding', exact: true })).toBeVisible();
});

test('server conflict preserves the current account for retry', async ({ page }) => {
  const writes = await setup(page, { failedReset: true }); await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
  await page.getByRole('button', { name: 'Restart onboarding', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Your account is saving another step');
  expect(writes).toHaveLength(1); await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
});

test('confirmation is exact, mobile dialog fits, and Escape restores focus', async ({ page }) => {
  const writes = await setup(page);
  await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  const input = page.getByLabel('Type RESTART ONBOARDING to confirm');
  const confirm = page.getByRole('button', { name: 'Restart onboarding', exact: true });
  await expect(input).toBeFocused(); await expect(confirm).toBeDisabled();
  await input.fill('restart onboarding'); await expect(confirm).toBeDisabled();
  await input.fill('RESTART ONBOARDING'); await expect(confirm).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.keyboard.press('Shift+Tab'); await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(input).toBeFocused();
  await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Restart Onboarding', exact: true })).toBeFocused();
  expect(writes).toEqual([]);
});

test('unconfirmed response preserves session and shows recovery without reloading', async ({ page }) => {
  const writes = await setup(page, { unconfirmedReset: true });
  await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
  await page.getByRole('button', { name: 'Restart onboarding', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Restart could not be confirmed');
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).not.toBeNull();
});

test('expired authentication cannot use previously displayed reset controls', async ({ page }) => {
  const writes = await setup(page, { expiredReset: true });
  await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
  await page.getByRole('button', { name: 'Restart onboarding', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('desktop confirmation explains reset and cancel sends no mutation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const writes = await setup(page);
  await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText("You'll start Audli's setup again.");
  await expect(page.getByRole('dialog')).toContainText('Your completed lessons and learning history will stay saved.');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(writes).toEqual([]);
  await expect(page.getByRole('button', { name: 'Restart Onboarding', exact: true })).toBeFocused();
});


for (const phase of ['playback', 'recording', 'pending response'] as const) {
  test(`restart abandons unfinished lesson after ${phase} and refresh stays in onboarding`, async ({ page }) => {
    const writes = await setup(page, { lesson: true, delayEnd: phase === 'pending response' });
    await page.getByLabel('Reduce motion').check();
    await page.getByRole('button', { name: 'Home', exact: true }).click();
    await page.getByRole('button', { name: 'Start today’s session' }).click();
    await expect.poll(async () => (await probe(page)).plays.length).toBeGreaterThan(0);
    if (phase === 'recording') {
      await endAudio(page);
      await expect.poll(async () => (await probe(page)).recorders).toBeGreaterThan(0);
    }
    await page.getByRole('button', { name: 'End', exact: true }).click();
    await page.getByRole('button', { name: 'End session', exact: true }).click();
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
    await expect(page.getByRole('dialog')).not.toContainText('Finish your lesson first');
    await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
    await page.getByRole('button', { name: 'Restart onboarding', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Let’s make this yours.' })).toBeVisible();
    const oldPlays = (await probe(page)).plays.length;
    await writes.releaseEnd();
    await page.waitForTimeout(150);
    expect((await probe(page)).plays.length).toBe(oldPlays);
    expect(await page.evaluate(() => [...document.querySelectorAll('audio')].every(audio => audio.paused))).toBe(true);
    expect((await probe(page)).tracks).toBe((await probe(page)).mediaCalls);
    expect(writes.filter(write => write.path === '/api/onboarding/restart')).toHaveLength(1);
    expect(writes.some(write => /heard|advance|assess/.test(write.path))).toBe(false);
    await expect(page.getByRole('heading', { name: 'Lesson completed' })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.dataset.reducedMotion)).toBe('true');
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Let’s make this yours.' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.dataset.reducedMotion)).toBe('true');
  });
}
