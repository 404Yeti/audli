import { test, expect, type Page } from '@playwright/test';
import { installVoice } from './voice-fixture';

const user = { id: '00000000-0000-0000-0000-000000000034', email: 'test@example.com',
  email_confirmed_at: '2026-10-06T00:00:00Z', app_metadata: { provider: 'email', providers: ['email'] }, user_metadata: {}, aud: 'authenticated', created_at: '2026-10-06T00:00:00Z' };
const token = [Buffer.from('{"alg":"HS256"}').toString('base64url'), Buffer.from(JSON.stringify({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url'), 'fixture'].join('.');
async function setup(page: Page, options: { available?: boolean; failedDelete?: boolean } = {}) {
  await installVoice(page);
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  await page.route('https://audli-auth-test.supabase.co/**', route => {
    if (new URL(route.request().url()).pathname.endsWith('/logout')) return route.fulfill({ status: 204 });
    return route.fulfill({ json: { access_token: token, refresh_token: 'fixture-refresh', expires_in: 3600, token_type: 'bearer', user } });
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'supabase' } });
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Robin', goal: 'Meetings', completed_attempts: 2 }, provider: 'demo' } });
    if (path === '/api/history') return route.fulfill({ json: [] });
    if (path === '/api/exercises/current' || path === '/api/lessons/current') return route.fulfill({ json: null });
    if (path === '/api/account/controls') return route.fulfill({ json: { delete_available: options.available ?? true } });
    if (path === '/api/account/delete') {
      writes.push({ path, body: route.request().postDataJSON() });
      return route.fulfill(options.failedDelete ? { status: 503, json: { detail: 'Deletion could not be confirmed. Retry or contact support.' } } : { json: { deleted: true } });
    }
    return route.fulfill({ status: 404, json: { detail: 'Unexpected request' } });
  });
  await page.goto('/'); await page.getByRole('button', { name: 'I already have an account' }).click();
  await page.getByLabel('Email', { exact: true }).fill(user.email); await page.getByLabel('Password', { exact: true }).fill('fixture-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you, Robin.' })).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  return writes;
}

test('deletion is invisible unless the server says it is available', async ({ page }) => {
  const writes = await setup(page, { available: false });
  await expect(page.getByText('Account & Data')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Delete Account', exact: true })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test('mobile deletion needs password and exact confirmation, then clears the session', async ({ page }) => {
  const writes = await setup(page);
  await page.getByRole('button', { name: 'Delete Account', exact: true }).click();
  const dialog = page.getByRole('dialog'); await expect(dialog).toContainText('permanently');
  await expect(dialog).toContainText('language suggestions');
  await expect(dialog).toContainText('existing backups may remain');
  await expect(dialog).toContainText('Keep MFA enabled');
  await expect(dialog.getByRole('link', { name: 'Audli support' })).toHaveAttribute('href', 'mailto:robbie@audli.ai?subject=Audli%20account%20deletion');
  await expect(page.getByLabel('Confirm your current password')).toBeFocused();
  await expect(dialog.getByRole('button', { name: 'Permanently delete account' })).toBeDisabled();
  await page.getByLabel('Confirm your current password').fill('fresh-password');
  await page.getByLabel('Type DELETE MY ACCOUNT to confirm').fill('delete');
  await expect(dialog.getByRole('button', { name: 'Permanently delete account' })).toBeDisabled();
  await page.getByLabel('Type DELETE MY ACCOUNT to confirm').fill('DELETE MY ACCOUNT');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await dialog.getByRole('button', { name: 'Permanently delete account' }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  expect(writes).toEqual([{ path: '/api/account/delete', body: { confirmation: 'DELETE MY ACCOUNT', password: 'fresh-password' } }]);
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).toBeNull();
});

test('failed deletion keeps the account and exposes retry without claiming success', async ({ page }) => {
  await setup(page, { failedDelete: true }); await page.getByRole('button', { name: 'Delete Account', exact: true }).click();
  await page.getByLabel('Confirm your current password').fill('fresh-password');
  await page.getByLabel('Type DELETE MY ACCOUNT to confirm').fill('DELETE MY ACCOUNT');
  await page.getByRole('button', { name: 'Permanently delete account' }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('could not be confirmed');
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).not.toBeNull();
  await expect(page.getByLabel('Confirm your current password')).toHaveValue('');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Delete Account', exact: true })).toBeFocused();
});

test('desktop confirmation traps focus and Escape cancels without a request', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const writes = await setup(page); await page.getByRole('button', { name: 'Delete Account', exact: true }).click();
  await page.keyboard.press('Shift+Tab'); await expect(page.getByRole('link', { name: 'Audli support' })).toBeFocused();
  await page.keyboard.press('Shift+Tab'); await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(page.getByRole('link', { name: 'Audli support' })).toBeFocused();
  await page.keyboard.press('Tab'); await expect(page.getByLabel('Confirm your current password')).toBeFocused();
  await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Delete Account', exact: true })).toBeFocused();
  expect(writes).toEqual([]);
});
