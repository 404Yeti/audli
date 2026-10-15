import { test, expect, type Page, type Route } from '@playwright/test';
test.use({ video: 'on' });
const profile = { destination: 'session_ready', profile: { name: 'Robin', goal: 'Meetings', completed_attempts: 2 }, provider: 'demo' };
async function local(page: Page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path === '/api/auth/config' ? { mode: 'local' } : path === '/api/profile' ? profile : null });
  });
}
async function hold(page: Page, path: string) {
  const routes: Route[] = [];
  let resolve!: () => void;
  let response: Parameters<Route['fulfill']>[0] | undefined;
  const ready = new Promise<void>(done => { resolve = done; });
  await page.route(`**/api/${path}`, route => {
    if (response) return route.fulfill(response).catch(() => {});
    routes.push(route); resolve();
  });
  return { ready, async fulfill(value: Parameters<Route['fulfill']>[0]) {
    response = value;
    await Promise.all(routes.map(route => route.fulfill(value).catch(() => {})));
  } };
}
for (const width of [320, 390, 768, 1440]) test(`startup layout and status at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 844 });
  const request = await hold(page, 'auth/config');
  await page.goto('/'); await request.ready; const route = request;
  await expect(page.getByRole('status')).toHaveText('Getting Audli ready…');
  await expect(page.getByRole('status')).toHaveAttribute('aria-atomic', 'true');
  await expect(page.getByRole('main')).toHaveAccessibleName('Audli startup');
  await expect(page.locator('.audli-mascot')).toHaveAttribute('aria-hidden', 'true');
  await expect(page.getByRole('main').getByRole('button')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const box = await page.locator('.audli-mascot').boundingBox();
  expect(box!.width).toBe(256); expect(box!.height).toBeCloseTo(224, 0);
  await page.getByRole('main').screenshot({ path: info.outputPath(`startup-${width}.png`), animations: 'disabled' });
  await route.fulfill({ json: { mode: 'supabase' } });
  await expect(page.getByRole('button', { name: 'Start listening', exact: true })).toBeVisible();
  await expect(page.locator('.audli-loading-screen')).toHaveCount(0);
});
test('reduced motion disables splash animation', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await hold(page, 'auth/config'); await page.goto('/');
  await expect(page.getByRole('status')).toBeVisible();
  await expect(page.locator('.mascot-body')).toHaveCSS('animation-name', 'none');
});
test('slow routing hides Home and onboarding; failure retries with keyboard', async ({ page }) => {
  await local(page); const request = await hold(page, 'profile');
  await page.goto('/'); await request.ready; const route = request;
  await expect(page.getByRole('status')).toHaveText('Getting your listening space ready…');
  await expect(page.getByRole('heading')).toHaveCount(0); await expect(page.getByRole('navigation')).toHaveCount(0);
  await route.fulfill({ status: 503, json: { detail: 'Your account is unavailable. Please try again.' } });
  await expect(page.getByRole('main').getByRole('alert')).toContainText('account is unavailable');
  await expect(page.getByRole('status')).toHaveCount(0);
  await expect(page.locator('.mascot-body')).toHaveCSS('animation-name', 'none');
  await page.unroute('**/api/profile');
  await page.addStyleTag({ content: 'nextjs-portal { display: none; }' });
  await page.getByText('audli', { exact: true }).click(); await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Retry connection' })).toBeFocused(); await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Good to see you, Robin.' })).toBeVisible();
});
test('returning learner waits for checkpoint hydration', async ({ page }) => {
  await local(page); const request = await hold(page, 'lessons/current');
  await page.goto('/'); await request.ready; const route = request;
  await expect(page.getByRole('status')).toHaveText('Getting your listening space ready…');
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toHaveCount(0);
  await route.fulfill({ json: null });
  await expect(page.getByRole('heading', { name: 'Good to see you, Robin.' })).toBeVisible();
});
test('new learner waits for persisted walkthrough restoration', async ({ page }) => {
  await local(page);
  await page.route('**/api/profile', route => route.fulfill({ json: { ...profile, destination: 'onboarding' } }));
  const request = await hold(page, 'onboarding'); await page.goto('/'); await request.ready; const route = request;
  await expect(page.getByRole('status')).toHaveText('Getting your listening space ready…'); await expect(page.getByRole('heading')).toHaveCount(0);
  await route.fulfill({ json: { destination: 'onboarding', walkthrough: 0, profile: { name: 'Listener', onboarding_status: 'not_started' }, revision: 0, stage: 'identity', prompt: '', pending: null } });
  await expect(page.locator('.audli-loading-screen')).toHaveCount(0); await expect(page.getByRole('heading')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toHaveCount(0);
});
for (const stage of ['auth', 'profile', 'checkpoint', 'onboarding']) test(`${stage} timeout exposes recovery and ignores late success`, async ({ page }) => {
  await page.clock.install(); await local(page);
  if (stage === 'onboarding') await page.route('**/api/profile', route => route.fulfill({ json: { ...profile, destination: 'onboarding' } }));
  const path = { auth: 'auth/config', profile: 'profile', checkpoint: 'lessons/current', onboarding: 'onboarding' }[stage]!;
  const request = await hold(page, path); await page.goto('/'); await request.ready; const route = request;
  await expect(page.getByRole('status')).toBeVisible(); await page.clock.fastForward(20_001);
  await expect(page.getByRole('main').getByRole('alert')).toContainText('taking too long'); await expect(page.getByRole('button', { name: 'Retry connection' })).toBeVisible();
  await route.fulfill({ json: stage === 'auth' ? { mode: 'local' } : stage === 'profile' ? profile : null }).catch(() => {});
  await expect(page.getByRole('main').getByRole('alert')).toBeVisible(); await expect(page.getByRole('button', { name: 'Start today’s session' })).toHaveCount(0);
});

test.describe('animation capture', () => {
  test('Pip floats gently without shifting the status', async ({ page }) => {
    await hold(page, 'auth/config'); await page.goto('/');
    const text = page.getByRole('status'); await expect(text).toBeVisible();
    const before = await text.boundingBox();
    const body = page.locator('.mascot-body');
    await expect(body).toHaveCSS('animation-name', 'mascot-think');
    const initial = await body.evaluate(node => getComputedStyle(node).transform);
    await expect.poll(() => body.evaluate(node => getComputedStyle(node).transform)).not.toBe(initial);
    await page.waitForTimeout(3000); // Capture one complete decorative loop for visual review.
    expect(await text.boundingBox()).toEqual(before);
  });
});
