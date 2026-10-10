import { test, expect, type Page } from '@playwright/test';

const supabase = 'https://audli-auth-test.supabase.co';
const user = { id: '00000000-0000-0000-0000-000000000002', email: 'recover@example.com', email_confirmed_at: '2026-10-06T00:00:00Z',
  app_metadata: { provider: 'email', providers: ['email'] }, user_metadata: {}, aud: 'authenticated', created_at: '2026-10-06T00:00:00Z' };
function token(exp = Math.floor(Date.now() / 1000) + 3600) {
  return [Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url'), Buffer.from(JSON.stringify({ sub: user.id, exp, aud: 'authenticated', role: 'authenticated' })).toString('base64url'), 'mock-signature'].join('.');
}
const recoveryToken = token();
const session = () => ({ access_token: recoveryToken, refresh_token: 'recovery-refresh', expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: 'bearer', user });
const hashLink = '/auth/recovery?type=recovery&token_hash=0123456789abcdef0123456789abcdef';
const implicitLink = () => `/auth/recovery#type=recovery&access_token=${token()}&refresh_token=recovery-refresh`;
type Options = { recoveryStatus?: number; verifyError?: string; updateError?: string; delayVerify?: boolean; delayUpdate?: boolean; delayRecover?: boolean; singleUse?: boolean; userError?: boolean; network?: boolean; logoutError?: boolean };
async function setup(page: Page, options: Options = {}) {
  const authRequests: { path: string; method: string; body: Record<string, unknown> | null; authorization?: string; redirect?: string | null }[] = [];
  const learnerRequests: string[] = [];
  let release: (() => void) | undefined;
  await page.route('**/api/**', route => {
    if (new URL(route.request().url()).pathname === '/api/auth/config') return route.fulfill({ json: { mode: 'supabase' } });
    learnerRequests.push(route.request().url());
    return route.fulfill({ status: 401, json: { detail: 'Sign in to continue.' } });
  });
  await page.route(supabase + '/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    authRequests.push({ path, method: request.method(), body: request.postData() ? request.postDataJSON() : null,
      authorization: request.headers().authorization, redirect: url.searchParams.get('redirect_to') });
    if (path.endsWith('/recover')) {
      if (options.delayRecover) await new Promise<void>(resolve => { release = resolve; });
      if (options.network) return route.abort();
      return route.fulfill({ status: options.recoveryStatus ?? 200, json: options.recoveryStatus ? { code: 'email_not_found', msg: 'Private provider detail' } : {} });
    }
    if (path.endsWith('/verify')) {
      if (options.delayVerify) await new Promise<void>(resolve => { release = resolve; });
      const code = options.verifyError ?? (options.singleUse && authRequests.filter(x => x.path.endsWith('/verify')).length > 1 ? 'otp_expired' : undefined);
      return code ? route.fulfill({ status: 403, json: { code, msg: 'Private provider detail' } }) : route.fulfill({ json: session() });
    }
    if (path.endsWith('/user')) {
      if (request.method() === 'PUT') {
        if (options.delayUpdate) await new Promise<void>(resolve => { release = resolve; });
        return options.updateError ? route.fulfill({ status: options.updateError === 'bad_jwt' ? 401 : 422,
          headers: { 'X-Supabase-Api-Version': '2024-01-01', 'Access-Control-Expose-Headers': 'X-Supabase-Api-Version' }, json: { code: options.updateError, msg: 'Private provider detail' } }) : route.fulfill({ json: user });
      }
      return options.userError ? route.fulfill({ status: 401, json: { code: 'bad_jwt', msg: 'Private provider detail' } }) : route.fulfill({ json: user });
    }
    if (path.endsWith('/logout')) return options.logoutError ? route.fulfill({ status: 500, json: { msg: 'Private provider detail' } }) : route.fulfill({ status: 204 });
    return route.fulfill({ status: 400, json: { msg: 'Unexpected auth request' } });
  });
  return { authRequests, learnerRequests, release: () => release?.() };
}
async function fillPassword(page: Page, confirmation = 'a-new-password') {
  await page.getByLabel('New password', { exact: true }).fill('a-new-password');
  await page.getByLabel('Confirm password', { exact: true }).fill(confirmation);
}

test('login exposes a minimal mobile-friendly reset request without learner access', async ({ page }) => {
  const fixture = await setup(page); await page.goto('/');
  await page.getByRole('button', { name: 'I already have an account' }).click();
  await page.getByRole('link', { name: 'Forgot password?' }).click();
  await expect(page.getByRole('heading', { name: 'Reset your password.' })).toBeVisible();
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  }
  await page.getByLabel('Email').fill('learner@example.com');
  await page.getByRole('button', { name: 'Send reset link' }).click();
  await expect(page.getByRole('status')).toContainText('If an account exists');
  expect(fixture.authRequests.filter(x => x.path.endsWith('/recover'))).toHaveLength(1);
  expect(fixture.authRequests[0].redirect).toBe('http://127.0.0.1:3100/auth/recovery');
  expect(fixture.learnerRequests).toEqual([]);
});

for (const status of [200, 400, 429]) test(`reset email confirmation is neutral for provider status ${status}`, async ({ page }) => {
  const fixture = await setup(page, { recoveryStatus: status === 200 ? undefined : status }); await page.goto('/forgot-password');
  await page.getByLabel('Email').fill('unknown@example.com'); await page.getByRole('button', { name: 'Send reset link' }).click();
  await expect(page.getByRole('status')).toHaveText('If an account exists for that email, you’ll receive a password-reset link. Check your inbox and spam folder.');
  await expect(page.getByText('Private provider detail')).toHaveCount(0); expect(fixture.learnerRequests).toEqual([]);
});

test('network failure permits explicit retry and reveals no account information', async ({ page }) => {
  await setup(page, { network: true }); await page.goto('/forgot-password');
  await page.getByLabel('Email').fill('learner@example.com'); await page.getByRole('button', { name: 'Send reset link' }).click();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('Could not connect');
  await expect(page.getByRole('button', { name: 'Send reset link' })).toBeEnabled();
});

test('verified one-time link updates password once without persisting or replacing learner credentials', async ({ page }) => {
  const fixture = await setup(page, { delayVerify: true, delayUpdate: true });
  await page.addInitScript(() => localStorage.setItem('audli-auth-session', JSON.stringify({ access_token: 'existing-account-A', refresh_token: 'existing-refresh-A' })));
  await page.goto(hashLink + '&next=https://evil.example');
  await expect(page.getByRole('status')).toHaveText('Checking your reset link…');
  await expect(page).toHaveURL(/\/auth\/recovery$/);
  await expect.poll(() => fixture.authRequests.filter(x => x.path.endsWith('/verify')).length).toBe(1);
  fixture.release(); await expect(page.getByLabel('New password', { exact: true })).toBeVisible();
  await fillPassword(page); await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('button', { name: 'Updating…' })).toBeDisabled();
  fixture.release(); await expect(page.getByRole('heading', { name: 'Password updated.' })).toBeVisible();
  expect(fixture.authRequests.filter(x => x.method === 'PUT').map(x => x.body)).toMatchObject([{ password: 'a-new-password' }]);
  expect(fixture.authRequests.find(x => x.method === 'PUT')?.authorization).toBe(`Bearer ${recoveryToken}`);
  expect(fixture.authRequests.filter(x => x.path.endsWith('/logout'))).toHaveLength(1);
  expect(fixture.learnerRequests).toEqual([]);
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).toContain('existing-account-A');
  expect(await page.evaluate(() => localStorage.getItem('audli-password-recovery'))).toBeNull();
  await expect(page.getByLabel('New password', { exact: true })).toHaveCount(0);
  await page.reload(); await expect(page.getByRole('main').getByRole('alert')).toContainText('invalid, expired, or has already been used');
  expect(fixture.authRequests.filter(x => x.path.endsWith('/verify'))).toHaveLength(1);
});

test('default implicit recovery links work without a browser PKCE verifier', async ({ page }) => {
  const fixture = await setup(page); await page.goto(implicitLink());
  await expect(page.getByLabel('New password', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/auth\/recovery$/);
  expect(fixture.authRequests.filter(x => x.path.endsWith('/token'))).toHaveLength(0);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
});

test('legacy Site URL recovery callback is forwarded before the persistent auth gate opens', async ({ page }) => {
  const fixture = await setup(page); await page.goto(implicitLink().replace('/auth/recovery', '/'));
  await expect(page.getByLabel('New password', { exact: true })).toBeVisible();
  expect(fixture.learnerRequests).toEqual([]);
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).toBeNull();
});

for (const code of ['otp_expired', 'access_denied']) test(`invalid or reused Supabase recovery link fails closed: ${code}`, async ({ page }) => {
  const fixture = await setup(page, { verifyError: code }); await page.goto(hashLink);
  await expect(page.getByRole('main').getByRole('alert')).toContainText('invalid, expired, or has already been used');
  await expect(page.getByLabel('New password', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Request a new link' })).toHaveAttribute('href', '/forgot-password');
  expect(fixture.authRequests.filter(x => x.path.endsWith('/verify'))).toHaveLength(1);
  expect(fixture.learnerRequests).toEqual([]);
});

for (const suffix of ['', '#type=signup&access_token=invalid&refresh_token=invalid', '#error=access_denied&error_code=otp_expired&error_description=SECRET',
  `#type=recovery&access_token=${token(1)}&refresh_token=expired-refresh`]) {
  test(`missing, non-recovery, error or expired credentials cannot fall back to an existing session: ${suffix.slice(0, 25)}`, async ({ page }) => {
    const fixture = await setup(page); await page.addInitScript(() => localStorage.setItem('audli-auth-session', JSON.stringify(session())));
    await page.goto('/auth/recovery' + suffix); await expect(page.getByRole('main').getByRole('alert')).toContainText('invalid, expired, or has already been used');
    expect(fixture.authRequests).toEqual([]); expect(fixture.learnerRequests).toEqual([]);
    await expect(page.getByText('SECRET')).toHaveCount(0); await expect(page).toHaveURL(/\/auth\/recovery$/);
  });
}

test('confirmation and minimum length validation prevent password updates', async ({ page }) => {
  const fixture = await setup(page); await page.goto(hashLink); await fillPassword(page, 'other-password');
  await page.getByRole('button', { name: 'Update password' }).click(); await expect(page.getByRole('main').getByRole('alert')).toContainText('do not match');
  expect(fixture.authRequests.filter(x => x.method === 'PUT')).toHaveLength(0);
  await page.getByLabel('New password', { exact: true }).fill('short'); await page.getByLabel('Confirm password', { exact: true }).fill('short');
  await page.getByRole('button', { name: 'Update password' }).click(); expect(fixture.authRequests.filter(x => x.method === 'PUT')).toHaveLength(0);
});

for (const code of ['weak_password', 'same_password', 'bad_jwt']) test(`password update error is safe and actionable: ${code}`, async ({ page }) => {
  const fixture = await setup(page, { updateError: code }); await page.goto(hashLink); await fillPassword(page);
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('main').getByRole('alert')).toContainText(code === 'bad_jwt' ? 'invalid, expired' : code === 'weak_password' ? 'stronger password' : 'different password');
  await expect(page.getByText('Private provider detail')).toHaveCount(0);
  expect(fixture.authRequests.filter(x => x.method === 'PUT')).toHaveLength(1);
});

test('forged callback session rejected by Supabase cannot update any password', async ({ page }) => {
  const fixture = await setup(page, { userError: true }); await page.goto(implicitLink());
  await expect(page.getByRole('main').getByRole('alert')).toContainText('invalid, expired');
  expect(fixture.authRequests.filter(x => x.method === 'PUT')).toHaveLength(0);
  expect(fixture.learnerRequests).toEqual([]);
});

test('recovery responses prevent referrer leakage and caching', async ({ page }) => {
  await setup(page); const response = await page.goto(hashLink);
  expect(response?.headers()['referrer-policy']).toBe('no-referrer');
  // Next dev replaces Cache-Control; the production build is checked separately.
  expect(response?.headers()['cache-control']).toMatch(/no-store|no-cache/);
  expect(response?.headers()['x-robots-tag']).toBe('noindex, nofollow');
});

test('a completed password reset clears only the matching learner session and a reused OTP stays rejected', async ({ page }) => {
  const fixture = await setup(page, { singleUse: true });
  await page.goto('/forgot-password');
  await page.evaluate(value => localStorage.setItem('audli-auth-session', JSON.stringify(value)), session());
  await page.goto(hashLink); await fillPassword(page); await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('heading', { name: 'Password updated.' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('audli-auth-session'))).toBeNull();
  await page.goto(hashLink); await expect(page.getByRole('main').getByRole('alert')).toContainText('already been used');
  expect(fixture.authRequests.filter(x => x.method === 'PUT')).toHaveLength(1);
  expect(fixture.learnerRequests).toEqual([]);
  await page.getByRole('link', { name: 'Back to sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Welcome back.' })).toBeVisible();
});

test('slow reset-email submission is disabled and duplicate submits do not resend', async ({ page }) => {
  const fixture = await setup(page, { delayRecover: true }); await page.goto('/forgot-password');
  await page.getByLabel('Email').fill('learner@example.com'); await page.getByRole('button', { name: 'Send reset link' }).click();
  await expect.poll(() => fixture.authRequests.filter(x => x.path.endsWith('/recover')).length).toBe(1);
  await expect(page.getByRole('button', { name: 'Sending…' })).toBeDisabled();
  await page.locator('form').dispatchEvent('submit'); fixture.release();
  await expect(page.getByRole('status')).toContainText('If an account exists');
  expect(fixture.authRequests.filter(x => x.path.endsWith('/recover'))).toHaveLength(1);
});

test('a successful password update remains successful when global logout is unavailable', async ({ page }) => {
  const fixture = await setup(page, { logoutError: true }); await page.goto(hashLink); await fillPassword(page);
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('heading', { name: 'Password updated.' })).toBeVisible();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('signing out other sessions could not be confirmed');
  expect(fixture.authRequests.filter(x => x.method === 'PUT')).toHaveLength(1);
  expect(fixture.authRequests.filter(x => x.path.endsWith('/logout'))).toHaveLength(1);
});

test('recovery session that expires while the form is open cannot refresh or update the password', async ({ page }) => {
  const fixture = await setup(page); await page.clock.install(); await page.goto(hashLink);
  await fillPassword(page); await page.clock.fastForward(3601000);
  await page.getByRole('button', { name: 'Update password' }).click();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('invalid, expired');
  expect(fixture.authRequests.filter(x => x.method === 'PUT' || x.path.endsWith('/token'))).toHaveLength(0);
  await expect(page.getByLabel('New password', { exact: true })).toHaveCount(0);
});
