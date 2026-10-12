import { test, expect, type Page } from '@playwright/test';
import { installVoice, promptWav } from './voice-fixture';

const user = { id: '00000000-0000-0000-0000-000000000034', email: 'test@example.com',
  email_confirmed_at: '2026-10-06T00:00:00Z', app_metadata: { provider: 'email', providers: ['email'] }, user_metadata: {}, aud: 'authenticated', created_at: '2026-10-06T00:00:00Z' };
const token = [Buffer.from('{"alg":"HS256"}').toString('base64url'), Buffer.from(JSON.stringify({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url'), 'fixture'].join('.');
async function setup(page: Page, options: { restart?: boolean; failedReset?: boolean; unconfirmedReset?: boolean; revokedReset?: boolean } = {}) {
  await installVoice(page);
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  let onboarding = false;
  await page.route('https://audli-auth-test.supabase.co/**', route => {
    if (new URL(route.request().url()).pathname.endsWith('/logout')) return route.fulfill({ status: 204 });
    return route.fulfill({ json: { access_token: token, refresh_token: 'fixture-refresh', expires_in: 3600, token_type: 'bearer', user } });
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'supabase' } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: onboarding ? 'onboarding' : 'session_ready', profile: { name: 'Robin', goal: 'Meetings', completed_attempts: 2 }, provider: 'demo' } });
    if (path === '/api/history') return route.fulfill({ json: [] });
    if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
    if (path === '/api/onboarding/reset-controls') return route.fulfill({ json: { restart_available: options.restart ?? true, onboarding_revision: 12,
      reset_description: 'Your preferred name, goal, interests, accent preferences, and onboarding answers reset. Your account, lessons, attempts, learning scores, difficulty, and accessibility settings stay saved.' } });
    if (path === '/api/onboarding/restart') {
      writes.push({ path, body: route.request().postDataJSON() });
      if (options.revokedReset) return route.fulfill({ status: 403, json: { detail: 'Onboarding restart is restricted to authorized test accounts.' } });
      if (options.unconfirmedReset) return route.fulfill({ json: { destination: 'onboarding', revision: 12 } });
      if (options.failedReset) return route.fulfill({ status: 409, json: { detail: 'Finish your current lesson before restarting onboarding.' } });
      onboarding = true; return route.fulfill({ json: { destination: 'onboarding', revision: 13 } });
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
  return writes;
}

test('authorized reset explains preserved data and reloads into onboarding', async ({ page }) => {
  const writes = await setup(page); await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('learning scores');
  await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
  await page.getByRole('button', { name: 'Confirm restart', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Let’s make this yours.' })).toBeVisible();
  expect(writes).toEqual([{ path: '/api/onboarding/restart', body: { confirmation: 'RESTART ONBOARDING', revision: 12 } }]);
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).not.toBeNull();
});

test('ordinary production accounts never see reset', async ({ page }) => {
  await setup(page, { restart: false }); await expect(page.getByRole('button', { name: 'Restart Onboarding', exact: true })).toHaveCount(0);
});

test('active lesson conflict prevents reset and keeps the current account', async ({ page }) => {
  const writes = await setup(page, { failedReset: true }); await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
  await page.getByRole('button', { name: 'Confirm restart', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Finish your current lesson');
  expect(writes).toHaveLength(1); await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
});

test('confirmation is exact, mobile dialog fits, and Escape restores focus', async ({ page }) => {
  const writes = await setup(page);
  await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  const input = page.getByLabel('Type RESTART ONBOARDING to confirm');
  const confirm = page.getByRole('button', { name: 'Confirm restart', exact: true });
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
  await page.getByRole('button', { name: 'Confirm restart', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Restart could not be confirmed');
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).not.toBeNull();
});

test('server revocation overrides previously displayed reset controls', async ({ page }) => {
  const writes = await setup(page, { revokedReset: true });
  await page.getByRole('button', { name: 'Restart Onboarding', exact: true }).click();
  await page.getByLabel('Type RESTART ONBOARDING to confirm').fill('RESTART ONBOARDING');
  await page.getByRole('button', { name: 'Confirm restart', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('restricted to authorized test accounts');
  expect(writes).toHaveLength(1);
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
});
