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
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: authorization === `Bearer ${tokenB}` ? 'Bob' : 'Alice', goal: 'Meetings', completed_attempts: 0, difficulty: { speech_rate: .75, duration_seconds: 45 } }, provider: 'openai' } });
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
  await page.getByRole('button', { name: 'Start training', exact: false }).click();
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
  await page.getByRole('button', { name: 'Start training', exact: false }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { mediaRace: { calls: number } }).mediaRace.calls)).toBe(1);
  await switchToB(page);
  await releaseMedia(page);
  expect(state.requests.filter(request => request.method !== 'GET')).toEqual([]);
  await expect(page.getByRole('heading', { name: 'Hello, Bob' })).toBeVisible();
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
  await page.getByRole('button', { name: 'Start training', exact: false }).click();
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

async function setupOnboarding(page: Page, legacy = false, audioMode: 'failure' | 'success' | 'race' = 'failure') {
  await page.addInitScript(({ audioMode }) => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true, value: async () => ({ getTracks: () => [{ stop() {} }] }) });
    class Recorder {
      static isTypeSupported() { return true; }
      state = 'inactive'; mimeType = 'audio/webm';
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start() { this.state = 'recording'; }
      stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['spoken preferences'], { type: this.mimeType }) }); this.onstop?.(); }); }
    }
    Object.defineProperty(window, 'MediaRecorder', { value: Recorder });
    if (audioMode === 'failure') {
      HTMLMediaElement.prototype.play = async function () {};
      HTMLMediaElement.prototype.pause = function () {};
    }
  }, { audioMode });
  const requests = await setupAuth(page);
  const profile = { name: 'Listener', goal: 'Everyday English', target_language: 'en', interests: [] as string[], target_situations: [] as string[], onboarding_status: legacy ? 'profile_saved' : 'not_started', completed_attempts: 0, difficulty: { speech_rate: .75, duration_seconds: 45 } };
  let stage = 'identity', revision = 0, pending: { text: string; uncertainty: string[] } | null = null;
  let failTranscribe = false, failComplete = false, generated = false;
  let releaseAudio: (() => void) | undefined;
  const audioRevisions: number[] = [];
  let conflicts = 0;
  await page.route('**/api/exercises', route => { generated = true; return route.fulfill({ json: { id: 'first', audio_url: '/api/exercises/first/audio', difficulty: profile.difficulty, completed_attempt_id: null } }); });
  await page.route('**/api/exercises/current', route => route.fulfill({ json: generated ? { id: 'first', audio_url: '/api/exercises/first/audio', difficulty: profile.difficulty, completed_attempt_id: null } : null }));
  const snapshot = () => ({ profile, stage, revision, pending, prompt: stage === 'identity' ? 'What should I call you, and what language are you training?' : stage === 'needs' ? 'What situations matter to you?' : stage === 'interests' ? 'What topics do you enjoy?' : 'Check your listening preferences.', destination: profile.onboarding_status === 'complete' ? 'session_ready' : 'onboarding' });
  await page.route('**/api/profile', async route => {
    if (route.request().headers()['authorization'] === `Bearer ${tokenB}`) return route.fulfill({ json: { destination: 'session_ready', profile: { ...profile, name: 'Bob' }, provider: 'openai' } });
    return route.fulfill({ json: { destination: snapshot().destination, profile, provider: 'openai' } });
  });
  await page.route('**/api/onboarding**', async route => {
    const path = new URL(route.request().url()).pathname;
    requests.requests.push({ path, method: route.request().method(), authorization: route.request().headers()['authorization'], body: route.request().postData() });
    if (path.endsWith('/audio')) {
      if (audioMode === 'failure') return route.fulfill({ status: 503, json: { detail: 'Voice unavailable' } });
      const requested = route.request().postDataJSON().revision as number;
      audioRevisions.push(requested);
      if (audioMode === 'race' && requested === 0) await new Promise<void>(resolve => { releaseAudio = resolve; });
      if (requested !== revision) { conflicts++; return route.fulfill({ status: 409, json: { detail: 'Onboarding changed. Reload saved progress.' } }); }
      return route.fulfill({ contentType: 'audio/wav', body: promptWav() });
    }
    if (path.endsWith('/start')) { profile.onboarding_status = 'in_progress'; revision++; }
    if (path.endsWith('/attempts')) {
      if (failTranscribe) { failTranscribe = false; return route.fulfill({ status: 503, json: { detail: 'Transcription failed. Retry your recording.' } }); }
      pending = { text: 'Maya English, work meetings and science', uncertainty: ['Check what I heard.'] }; revision++;
    }
    if (path.endsWith('/answer')) {
      if (stage === 'identity') { profile.name = 'Maya'; stage = 'needs'; }
      else if (stage === 'needs') { profile.goal = 'Understand meetings'; profile.target_situations = ['work meetings']; stage = 'interests'; }
      else { profile.interests = ['science']; stage = 'review'; profile.onboarding_status = 'profile_saved'; }
      pending = null; revision++;
    }
    if (path.endsWith('/complete')) {
      profile.onboarding_status = 'complete'; revision++;
      if (failComplete) { failComplete = false; return route.fulfill({ status: 503, json: { detail: 'Response interrupted. Progress is safe.' } }); }
    }
    return route.fulfill({ json: snapshot() });
  });
  await signIn(page);
  await expect(page.getByRole('button', { name: 'Let’s talk', exact: true })).toBeVisible();
  return { requests: requests.requests, audioRevisions, get conflicts() { return conflicts; }, get revision() { return revision; }, get audioPending() { return !!releaseAudio; }, releaseAudio() { releaseAudio?.(); }, failTranscription() { failTranscribe = true; }, interruptCompletion() { failComplete = true; } };
}
async function onboardingAnswer(page: Page) {
  await page.getByRole('button', { name: 'Record answer', exact: true }).click();
  await page.getByRole('button', { name: 'Finish answer', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Did I hear you correctly?' })).toBeVisible();
}

test('spoken onboarding resumes recognition and stages across refresh and logout, then opens first lesson', async ({ page }) => {
  const state = await setupOnboarding(page);
  await page.getByRole('button', { name: 'Let’s talk', exact: true }).click();
  await onboardingAnswer(page);
  await page.reload();
  await expect(page.getByLabel('Your spoken answer')).toHaveValue('Maya English, work meetings and science');
  await page.getByRole('button', { name: 'That’s what I said', exact: true }).click();
  await page.reload();
  await expect(page.getByText('What situations matter to you?', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click(); await signIn(page);
  await expect(page.getByText('What situations matter to you?', { exact: true })).toBeVisible();
  for (let i = 0; i < 2; i++) { await onboardingAnswer(page); await page.getByRole('button', { name: 'That’s what I said', exact: true }).click(); }
  await expect(page.getByText('Interests: science', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'That’s right. Let’s train your ears.', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Let’s train your ears.' })).toBeVisible();
  expect(state.requests.filter(r => r.path === '/api/onboarding/answer')).toHaveLength(3);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Hello, Maya' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Let’s make this yours.' })).toHaveCount(0);
});

test('onboarding transcription and interrupted completion recover without repeating stages', async ({ page }) => {
  const state = await setupOnboarding(page);
  await page.getByRole('button', { name: 'Let’s talk', exact: true }).click(); state.failTranscription();
  await page.getByRole('button', { name: 'Record answer', exact: true }).click();
  await page.getByRole('button', { name: 'Finish answer', exact: true }).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('Transcription failed');
  await page.getByRole('button', { name: 'Retry transcription', exact: true }).click();
  await page.getByRole('button', { name: 'That’s what I said', exact: true }).click();
  for (let i = 0; i < 2; i++) { await onboardingAnswer(page); await page.getByRole('button', { name: 'That’s what I said', exact: true }).click(); }
  state.interruptCompletion();
  await page.getByRole('button', { name: 'That’s right. Let’s train your ears.', exact: true }).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('Response interrupted');
  await page.getByRole('button', { name: 'Reload saved progress', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Let’s train your ears.' })).toBeVisible();
  expect(state.requests.filter(r => r.path === '/api/onboarding/answer')).toHaveLength(3);
});

test('denied onboarding microphone and failed TTS retain recoverable actions', async ({ page }) => {
  await setupOnboarding(page);
  await page.getByRole('button', { name: 'Let’s talk', exact: true }).click();
  await page.evaluate(() => { Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async () => { throw new DOMException('Denied', 'NotAllowedError'); }, configurable: true }); });
  await page.getByRole('button', { name: 'Record answer', exact: true }).click();
  await expect(page.locator('main').getByRole('alert')).toContainText('Microphone access was denied');
  await expect(page.getByRole('button', { name: 'Record answer', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Retry audio', exact: true })).toBeVisible();
});

for (const delayed of ['microphone', 'transcription', 'answer'] as const) {
  test(`account switch cancels delayed onboarding ${delayed}`, async ({ page }) => {
    const state = await setupOnboarding(page);
    await page.getByRole('button', { name: 'Let’s talk', exact: true }).click();
    let release: (() => Promise<void>) | undefined;
    if (delayed === 'microphone') {
      await page.evaluate(() => {
        const race = { stopped: 0, release: undefined as (() => void) | undefined };
        (window as unknown as { onboardingRace: typeof race }).onboardingRace = race;
        Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true, value: () => new Promise(resolve => { race.release = () => resolve({ getTracks: () => [{ stop() { race.stopped++; } }] }); }) });
      });
      await page.getByRole('button', { name: 'Record answer', exact: true }).click();
      await expect(page.getByText('Opening your microphone…', { exact: true })).toBeVisible();
    } else {
      if (delayed === 'answer') await onboardingAnswer(page);
      await page.route(`**/api/onboarding/${delayed === 'answer' ? 'answer' : 'attempts'}`, async route => {
        state.requests.push({ path: new URL(route.request().url()).pathname, method: route.request().method(), authorization: route.request().headers()['authorization'], body: null });
        await new Promise<void>(resolve => { release = async () => { await route.fulfill({ json: { stage: 'needs', revision: 3, pending: null, profile: { name: 'A private result' } } }).catch(() => {}); resolve(); }; });
      });
      if (delayed === 'answer') await page.getByRole('button', { name: 'That’s what I said', exact: true }).click();
      else { await page.getByRole('button', { name: 'Record answer', exact: true }).click(); await page.getByRole('button', { name: 'Finish answer', exact: true }).click(); }
      await expect.poll(() => !!release).toBe(true);
    }
    await switchToB(page); const count = state.requests.length;
    if (delayed === 'microphone') {
      await page.evaluate(() => (window as unknown as { onboardingRace: { release?: () => void } }).onboardingRace.release?.());
      await expect.poll(() => page.evaluate(() => (window as unknown as { onboardingRace: { stopped: number } }).onboardingRace.stopped)).toBe(1);
    } else await release!();
    await page.waitForTimeout(200);
    expect(state.requests.length).toBe(count);
    expect(state.requests.filter(r => r.authorization === `Bearer ${tokenB}` && r.method !== 'GET')).toEqual([]);
    await expect(page.getByRole('heading', { name: 'Hello, Bob' })).toBeVisible();
  });
}


test('existing profile_saved learner explicitly starts spoken onboarding', async ({ page }) => {
  await setupOnboarding(page, true);
  await page.getByRole('button', { name: 'Let’s talk', exact: true }).click();
  await onboardingAnswer(page);
  await page.getByRole('button', { name: 'That’s what I said', exact: true }).click();
  await expect(page.getByText('What situations matter to you?', { exact: true })).toBeVisible();
});

// Valid PCM lets Chromium load the successful TTS body and emit canplay.
function promptWav() {
  const samples = 8000 * 5;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
  return wav;
}
type AudioProbe = { plays: string[]; pauses: string[]; urls: string[]; revoked: string[]; stopped: number; bodyPending: boolean; released: boolean; releaseBody?: () => void };
async function instrumentOnboardingAudio(page: Page, delayBody = false) {
  await page.addInitScript(({ delayBody }) => {
    const probe: AudioProbe = { plays: [], pauses: [], urls: [], revoked: [], stopped: 0, bodyPending: false, released: false };
    (window as unknown as { audioProbe: AudioProbe }).audioProbe = probe;
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = body => { const url = create(body); probe.urls.push(url); return url; };
    URL.revokeObjectURL = url => { probe.revoked.push(url); revoke(url); };
    // Installed after setupOnboarding's media stubs, at page evaluation time.
    const install = () => {
      const play = HTMLMediaElement.prototype.play, pause = HTMLMediaElement.prototype.pause;
      HTMLMediaElement.prototype.play = async function () { await play.call(this); probe.plays.push(this.src); };
      HTMLMediaElement.prototype.pause = function () { probe.pauses.push(this.src); pause.call(this); };
      Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true, value: async () => ({ getTracks: () => [{ stop() { probe.stopped++; } }] }) });
    };
    window.addEventListener('DOMContentLoaded', install);
    if (delayBody) {
      const fetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const response = await fetch(...args);
        if (String(args[0]).endsWith('/api/onboarding/audio') && response.ok) {
          const blob = response.blob.bind(response);
          response.blob = async () => {
            const body = await blob();
            probe.bodyPending = true;
            await new Promise<void>(resolve => { probe.releaseBody = resolve; });
            probe.released = true; return body;
          };
        }
        return response;
      };
    }
  }, { delayBody });
}
async function audioProbe(page: Page) {
  return page.evaluate(() => (window as unknown as { audioProbe: AudioProbe }).audioProbe);
}

test('successful onboarding TTS survives rerenders and stops on recording cancellation and unmount', async ({ page }) => {
  await instrumentOnboardingAudio(page);
  const state = await setupOnboarding(page, false, 'success');
  await expect.poll(async () => (await audioProbe(page)).plays.length).toBe(1);
  await expect.poll(() => page.getByLabel('Hear onboarding prompt').evaluate(element => (element as HTMLAudioElement).paused)).toBe(false);
  const initial = await audioProbe(page);
  await page.getByRole('button', { name: 'Let’s talk', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Record answer', exact: true })).toBeEnabled();
  expect((await audioProbe(page)).pauses).toEqual(initial.pauses);
  expect(await page.getByLabel('Hear onboarding prompt').evaluate(element => (element as HTMLAudioElement).paused)).toBe(false);
  expect(state.audioRevisions).toEqual([0]);
  await page.getByRole('button', { name: 'Record answer', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel recording', exact: true })).toBeVisible();
  expect((await audioProbe(page)).pauses).toContain(initial.plays[0]);
  await page.getByRole('button', { name: 'Cancel recording', exact: true }).click();
  expect((await audioProbe(page)).stopped).toBeGreaterThan(0);
  await onboardingAnswer(page);
  const beforeEdit = await audioProbe(page);
  await page.getByLabel('Your spoken answer').fill('Maya, English');
  expect((await audioProbe(page)).pauses).toEqual(beforeEdit.pauses);
  expect(state.audioRevisions).toEqual([0]);
  await page.getByRole('button', { name: 'That’s what I said', exact: true }).click();
  await expect.poll(async () => (await audioProbe(page)).plays.length).toBe(2);
  expect((await audioProbe(page)).revoked).toContain(initial.plays[0]);
  const beforeLogout = await audioProbe(page);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  const after = await audioProbe(page);
  expect(after.pauses.length).toBeGreaterThan(beforeLogout.pauses.length);
  expect(after.revoked).toEqual(after.urls);
  await expect(page.getByLabel('Hear onboarding prompt')).toHaveCount(0);
});

test('stale initial TTS revision recovers once from the authoritative checkpoint', async ({ page }) => {
  await instrumentOnboardingAudio(page);
  const state = await setupOnboarding(page, false, 'race');
  await expect.poll(() => state.audioPending).toBe(true);
  await page.getByRole('button', { name: 'Let’s talk', exact: true }).click();
  await expect.poll(() => state.revision).toBe(1);
  state.releaseAudio();
  await expect.poll(async () => (await audioProbe(page)).plays.length).toBe(1);
  expect(state.conflicts).toBe(1);
  expect(state.audioRevisions).toEqual([0, 1]);
  expect(state.requests.filter(r => r.path === '/api/onboarding' && r.method === 'GET')).toHaveLength(2);
  await onboardingAnswer(page);
  await page.getByLabel('Your spoken answer').fill('English');
  expect(state.audioRevisions).toEqual([0, 1]);
  expect((await audioProbe(page)).plays).toHaveLength(1);
  await expect(page.getByRole('button', { name: 'Retry audio', exact: true })).toHaveCount(0);
});

test('account switch discards successful onboarding audio with delayed body resolution', async ({ page }) => {
  await instrumentOnboardingAudio(page, true);
  const state = await setupOnboarding(page, false, 'success');
  await expect.poll(async () => (await audioProbe(page)).bodyPending).toBe(true);
  await switchToB(page);
  const count = state.requests.length;
  await page.evaluate(() => (window as unknown as { audioProbe: AudioProbe }).audioProbe.releaseBody?.());
  await expect.poll(async () => (await audioProbe(page)).released).toBe(true);
  expect(state.requests.length).toBe(count);
  expect(state.requests.filter(r => r.authorization === `Bearer ${tokenB}` && r.method !== 'GET')).toEqual([]);
  const probe = await audioProbe(page);
  expect(probe.plays).toEqual([]); expect(probe.urls).toEqual([]); expect(probe.revoked).toEqual([]);
  await expect(page.getByLabel('Hear onboarding prompt')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Hello, Bob' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Let’s make this yours.' })).toHaveCount(0);
});

for (const delayed of ['completion', 'first generation'] as const) {
  test(`account switch discards delayed onboarding ${delayed}`, async ({ page }) => {
    const state = await setupOnboarding(page);
    await page.getByRole('button', { name: 'Let’s talk', exact: true }).click();
    for (let i = 0; i < 3; i++) { await onboardingAnswer(page); await page.getByRole('button', { name: 'That’s what I said', exact: true }).click(); }
    await expect(page.getByText('Interests: science', { exact: true })).toBeVisible();
    let release: (() => Promise<void>) | undefined;
    const path = delayed === 'completion' ? '/api/onboarding/complete' : '/api/exercises';
    await page.route(`**${path}`, async route => {
      state.requests.push({ path, method: route.request().method(), authorization: route.request().headers()['authorization'], body: route.request().postData() });
      await new Promise<void>(resolve => { release = async () => {
        await route.fulfill({ json: delayed === 'completion' ? { destination: 'session_ready' } : { id: 'private-A', audio_url: '/api/exercises/private-A/audio', difficulty: {}, completed_attempt_id: null } }).catch(() => {});
        resolve();
      }; });
    });
    await page.getByRole('button', { name: 'That’s right. Let’s train your ears.', exact: true }).click();
    await expect.poll(() => !!release).toBe(true);
    expect(state.requests.findLast(r => r.path === path)?.authorization).toBe(`Bearer ${token}`);
    await switchToB(page); const count = state.requests.length;
    await release!(); await page.waitForTimeout(200);
    expect(state.requests.length).toBe(count);
    expect(state.requests.filter(r => r.authorization === `Bearer ${tokenB}` && r.method !== 'GET')).toEqual([]);
    await expect(page.getByRole('heading', { name: 'Hello, Bob' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Let’s train your ears.' })).toHaveCount(0);
  });
}
