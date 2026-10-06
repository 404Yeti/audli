import { test, expect, type Page } from '@playwright/test';

const userId = '00000000-0000-0000-0000-000000000001';
const user = { id: userId, email: 'learner@example.com', email_confirmed_at: '2026-10-06T00:00:00Z', app_metadata: { provider: 'email', providers: ['email'] }, user_metadata: {}, aud: 'authenticated', created_at: '2026-10-06T00:00:00Z' };
const token = [Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url'), Buffer.from(JSON.stringify({ sub: userId, email: user.email, exp: Math.floor(Date.now()/1000)+3600, aud: 'authenticated', role: 'authenticated' })).toString('base64url'), 'mock-signature'].join('.');

const userB = { ...user, id: '00000000-0000-0000-0000-000000000002', email: 'bob@example.com' };
const tokenB = [token.split('.')[0], Buffer.from(JSON.stringify({ sub: userB.id, email: userB.email, exp: Math.floor(Date.now()/1000)+3600, aud: 'authenticated', role: 'authenticated' })).toString('base64url'), 'mock-signature-b'].join('.');

async function setupAuth(page: Page, training = false) {
  const requests: { path: string; authorization: string | undefined; method: string; body: string | null }[] = [];
  let reject = false;
  let listened = false;
  await page.route('https://audli-auth-test.supabase.co/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/signup')) return route.fulfill({ json: user });
    if (path.endsWith('/logout')) return route.fulfill({ status: 204 });
    if (path.endsWith('/token')) {
      const second = route.request().postDataJSON()?.email === userB.email;
      return route.fulfill({ json: { access_token: second ? tokenB : token, refresh_token: 'mock-refresh-token', expires_in: 3600, token_type: 'bearer', user: second ? userB : user } });
    }
    return route.fulfill({ json: user });
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const authorization = route.request().headers()['authorization'];
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'supabase' } });
    requests.push({ path, authorization, method: route.request().method(), body: route.request().postData() });
    if (reject || ![`Bearer ${token}`, `Bearer ${tokenB}`].includes(authorization ?? '')) return route.fulfill({ status: 401, json: { detail: 'Your session expired or is invalid. Please sign in again.' } });
    if (path === '/api/profile') return route.fulfill({ json: { profile: { name: authorization === `Bearer ${tokenB}` ? 'Bob' : 'Alice', goal: 'Meetings', completed_attempts: 0, difficulty: { speech_rate: .75, duration_seconds: 45 } }, provider: 'openai' } });
    if (path === '/api/exercises/current') return route.fulfill({ json: training && authorization !== `Bearer ${tokenB}` ? { id: 'clip', audio_url: '/api/exercises/clip/audio', difficulty: { duration_seconds: 45 }, completed_attempt_id: null } : null });
    if (path === '/api/exercises') return route.fulfill({ json: { id: 'clip', audio_url: '/api/exercises/clip/audio', difficulty: { duration_seconds: 45 }, completed_attempt_id: null } });
    if (path.endsWith('/conversation/listened')) listened = true;
    if (path.endsWith('/conversation') || path.endsWith('/conversation/listened')) return route.fulfill({ json: { state: listened ? 'AWAITING_SUMMARY' : 'LISTENING', cue_id: listened ? 'summary' : null, prompt: listened ? 'Tell me what you understood.' : null, active_followup: null, pending_attempt: null, result: null } });
    if (path.endsWith('/coach-audio')) return route.fulfill({ json: { audio_url: '/api/exercises/clip/coach-audio/summary' } });
    if (path.endsWith('/audio') || path.endsWith('/coach-audio/summary')) return route.fulfill({ contentType: 'audio/wav', body: Buffer.alloc(44) });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  return { requests, expire() { reject = true; } };
}
async function signIn(page: Page, email = user.email) {
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Password', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

test('unauthenticated gate sends no learner requests; confirmed sign-in loads own state and logout clears it', async ({ page }) => {
  const state = await setupAuth(page);
  expect(state.requests).toEqual([]);
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toBeVisible();
  expect(state.requests.length).toBeGreaterThan(0);
  expect(state.requests.every(request => request.authorization === `Bearer ${token}`)).toBe(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toBeVisible();
});

test('signup asks for email confirmation and does not initialize learner state', async ({ page }) => {
  const state = await setupAuth(page);
  await page.getByRole('button', { name: 'Create an account', exact: true }).click();
  await page.getByLabel('Email', { exact: true }).fill(user.email);
  await page.getByLabel('Password', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: 'Sign up', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Check your email');
  expect(state.requests).toEqual([]);
});

test('invalid persisted session unmounts learner state and requires sign-in', async ({ page }) => {
  const state = await setupAuth(page);
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toBeVisible();
  state.expire();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('session expired');
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toHaveCount(0);
});

test('authentication connection failure fails closed with a retry', async ({ page }) => {
  await page.route('**/api/auth/config', route => route.fulfill({ status: 502, contentType: 'text/html', body: 'Unavailable' }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Retry connection' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Set up your practice' })).toHaveCount(0);
});

test('logout stops an active microphone and prevents a discarded recording upload', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('stopped-tracks', '0');
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async () => ({ getTracks: () => [{ stop() { sessionStorage.setItem('stopped-tracks', String(Number(sessionStorage.getItem('stopped-tracks')) + 1)); } }] }) });
    class Recorder {
      static isTypeSupported() { return true; }
      state = 'inactive'; mimeType = 'audio/webm';
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start() { this.state = 'recording'; }
      stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['discarded'], { type: this.mimeType }) }); this.onstop?.(); }); }
    }
    Object.defineProperty(window, 'MediaRecorder', { value: Recorder });
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
  });
  const state = await setupAuth(page, true);
  await signIn(page);
  await page.getByRole('button', { name: 'Start training' }).click();
  await page.getByRole('button', { name: 'Listen to the lesson' }).click();
  await page.getByLabel('Play listening exercise').evaluate(element => element.dispatchEvent(new Event('ended')));
  await expect(page.getByLabel('Hear Audli’s message')).toBeVisible();
  await page.getByLabel('Hear Audli’s message').evaluate(element => element.dispatchEvent(new Event('ended')));
  await expect(page.getByRole('button', { name: 'Finish', exact: true })).toBeVisible();
  const before = await page.evaluate(() => Number(sessionStorage.getItem('stopped-tracks')));
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  expect(await page.evaluate(() => Number(sessionStorage.getItem('stopped-tracks')))).toBeGreaterThan(before);
  expect(state.requests.some(request => request.path.endsWith('/attempts'))).toBe(false);
});

test('failed remote logout still clears persisted browser credentials', async ({ page }) => {
  await setupAuth(page);
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toBeVisible();
  await page.route('https://audli-auth-test.supabase.co/auth/v1/logout**', route => route.fulfill({ status: 500, json: { message: 'Offline' } }));
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Signed out here');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
});

test('refresh during a learner request keeps the same account and continues the lesson', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async () => ({ getTracks: () => [{ stop() {} }] }) });
  });
  const state = await setupAuth(page);
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Hello, Alice' })).toBeVisible();
  await page.evaluate(() => {
    const session = JSON.parse(localStorage.getItem('audli-auth-session')!);
    session.expires_at = Math.floor(Date.now()/1000) - 1;
    localStorage.setItem('audli-auth-session', JSON.stringify(session));
  });
  const refresh = page.waitForRequest(request => request.url().includes('grant_type=refresh_token'));
  await page.getByRole('button', { name: 'Let’s listen', exact: false }).click();
  await refresh;
  await expect(page.getByRole('heading', { name: 'Let’s train your ears.' })).toBeVisible();
  expect(state.requests.some(request => request.path === '/api/exercises')).toBe(true);
});

async function delayedMedia(page: Page, delayCall: number) {
  await page.addInitScript(({ delayCall }) => {
    const race = { calls: 0, stopped: 0, recorders: 0, release: undefined as (() => void) | undefined };
    (window as unknown as { mediaRace: typeof race }).mediaRace = race;
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: () => {
      race.calls++;
      const stream = { getTracks: () => [{ stop() { race.stopped++; } }] };
      return race.calls === delayCall ? new Promise(resolve => { race.release = () => resolve(stream); }) : Promise.resolve(stream);
    } });
    class Recorder {
      static isTypeSupported() { return true; }
      state = 'inactive'; mimeType = 'audio/webm';
      constructor() { race.recorders++; }
      start() { this.state = 'recording'; }
      stop() { this.state = 'inactive'; }
    }
    Object.defineProperty(window, 'MediaRecorder', { value: Recorder });
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
  }, { delayCall });
}
async function switchToB(page: Page) {
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeEnabled();
  await signIn(page, userB.email);
  await expect(page.getByRole('heading', { name: 'Hello, Bob' })).toBeVisible();
}
async function releaseMedia(page: Page) {
  await page.evaluate(() => (window as unknown as { mediaRace: { release?: () => void } }).mediaRace.release?.());
  await expect.poll(() => page.evaluate(() => (window as unknown as { mediaRace: { stopped: number } }).mediaRace.stopped)).toBeGreaterThan(0);
  // Observe the network after promise/microtask continuations have had a chance to run.
  await page.waitForTimeout(200);
}

test('pending setup permission cannot write A profile or start an exercise under B', async ({ page }) => {
  await delayedMedia(page, 1);
  const state = await setupAuth(page);
  await signIn(page);
  await page.getByLabel('What should we call you?').fill('Private Alice');
  await page.getByLabel('What do you want to improve your listening for?').fill('Private goal A');
  await page.getByRole('button', { name: 'Let’s listen', exact: false }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { mediaRace: { calls: number } }).mediaRace.calls)).toBe(1);
  await switchToB(page);
  await releaseMedia(page);
  expect(state.requests.filter(request => request.method !== 'GET')).toEqual([]);
  await expect(page.getByLabel('What should we call you?')).toHaveValue('Bob');
  await expect(page.getByLabel('What do you want to improve your listening for?')).toHaveValue('Meetings');
});

test('late exercise generation cannot continue conversation/profile/audio work after account switch', async ({ page }) => {
  await delayedMedia(page, 99);
  const state = await setupAuth(page);
  let release: (() => Promise<void>) | undefined;
  await page.route('**/api/exercises', async route => {
    state.requests.push({ path: '/api/exercises', method: route.request().method(), authorization: route.request().headers()['authorization'], body: route.request().postData() });
    await new Promise<void>(resolve => {
      release = async () => { await route.fulfill({ json: { id: 'late-a', audio_url: '/api/exercises/late-a/audio', difficulty: {}, completed_attempt_id: null } }).catch(() => {}); resolve(); };
    });
  });
  await signIn(page);
  await page.getByRole('button', { name: 'Let’s listen', exact: false }).click();
  await expect.poll(() => !!release).toBe(true);
  await switchToB(page);
  const count = state.requests.length;
  await release!();
  await page.waitForTimeout(200);
  expect(state.requests.length).toBe(count);
  expect(state.requests.filter(request => request.authorization === `Bearer ${tokenB}` && request.method !== 'GET')).toEqual([]);
  await expect(page.getByRole('heading', { name: 'Hello, Bob' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Let’s train your ears.' })).toHaveCount(0);
});

test('late recording acquisition releases tracks without creating a recorder or uploading under B', async ({ page }) => {
  await delayedMedia(page, 2);
  const state = await setupAuth(page, true);
  await signIn(page);
  await page.getByRole('button', { name: 'Start training' }).click();
  await page.getByRole('button', { name: 'Listen to the lesson' }).click();
  await page.getByLabel('Play listening exercise').evaluate(element => element.dispatchEvent(new Event('ended')));
  await expect(page.getByLabel('Hear Audli’s message')).toBeVisible();
  await page.getByLabel('Hear Audli’s message').evaluate(element => element.dispatchEvent(new Event('ended')));
  await expect.poll(() => page.evaluate(() => (window as unknown as { mediaRace: { calls: number } }).mediaRace.calls)).toBe(2);
  await switchToB(page);
  await releaseMedia(page);
  expect(await page.evaluate(() => (window as unknown as { mediaRace: { stopped: number; recorders: number } }).mediaRace)).toMatchObject({ stopped: 2, recorders: 0 });
  expect(state.requests.some(request => request.path.endsWith('/attempts'))).toBe(false);
  expect(state.requests.some(request => request.authorization === `Bearer ${tokenB}` && request.method !== 'GET')).toBe(false);
});
