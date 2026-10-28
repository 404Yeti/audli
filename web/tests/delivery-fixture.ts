import { expect, type Page, type Request } from '@playwright/test';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';
import { installVoice, promptWav } from './voice-fixture';

/** How the next upload behaves: `ok` commits and answers; `hang` never answers; `lost` commits then drops the connection;
 * `down` fails without reaching the server; `bad` answers 503 without committing. */
export type Plan = 'ok' | 'hang' | 'lost' | 'down' | 'bad' | 'processing' | 'uncertain';
export type Mode = 'checkin' | 'exercise';

export const FAST_POLICY = { enabled: true, base_ms: 1000, bytes_per_second: 64000, min_ms: 2000, max_ms: 4000, growth: [1, 1], max_sends: 3, deadline_ms: 30000,
  backoff_ms: [100, 100], slow_hint_ms: 500 };

export type Fixture = Awaited<ReturnType<typeof setupDelivery>>;

export async function setupDelivery(page: Page, mode: Mode, uploads: Plan[], options: { policy?: object | 'down'; otherWriter?: () => void } = {}) {
  await installVoice(page, { autoPlayback: true });
  const requests: string[] = [], spoken: string[] = [], uploadRequests: { path: string; revision: number | null; uploadId: string | null }[] = [];
  let operation: { reference: { operation_id: string; kind: string }; result: unknown; uncertain: boolean } | null = null, statusReads = 0;
  let transcriptions = 0, nextUpload = 0, started = false;
  let exerciseState = mode === 'exercise' ? 'AWAITING_SUMMARY' : 'LISTENING';
  const clip = { id: 'clip', audio_url: '/api/exercises/clip/audio', topic: 'Pottery making', completed_attempt_id: null as string | null };
  const lesson: LessonCheckpoint = { id: 'structured', revision: 3, status: 'active', phase: mode === 'checkin' ? 'WELCOME' : 'EXERCISE',
    prompt: mode === 'checkin' ? 'Hi, Maya. How has your day been?' : null, pending: null, exercise: mode === 'exercise' ? clip : null, introduction_pending: false,
    elapsed_seconds: 0, remaining_seconds: 600, server_time: new Date().toISOString(), focus: 'details', exercises_completed: 0, strength: null, improvement_focus: null,
    encouragement: 'Thanks for listening, Maya.' } as unknown as LessonCheckpoint;
  const attempts = new Map<string, { id: string; transcription: { text: string; confidence: number; uncertainty: string[] } }>();
  const state = () => ({ ...lesson, server_time: new Date().toISOString() });
  const conversation = () => ({ state: exerciseState, cue_id: clip.completed_attempt_id ? 'feedback' : 'summary', prompt: 'What was the speaker mainly talking about?',
    active_followup: null, pending_attempt: exerciseState === 'AWAITING_SUMMARY' ? Array.from(attempts.values()).at(-1) ?? null : null, result: null, topic: clip.topic });
  const commitCheckin = () => { transcriptions++; lesson.revision++; lesson.pending = { text: "I'm good, and you?", confidence: 0.99, uncertainty: [] } as never; };

  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    requests.push(`${request.method()} ${path}`);
    if (path === '/api/operation-capabilities') return route.fulfill({ json: { durable_operations: !!operation } });
    if (path.includes('/recordings/') && path.endsWith('/record-again')) {
      operation = null; attempts.clear();
      if (mode === 'checkin' && lesson.pending) { lesson.pending = null; lesson.revision++; }
      return route.fulfill({ json: { record_again: true } });
    }
    if (path.startsWith('/api/operations/') && request.method() === 'GET') {
      statusReads++;
      if (!operation) return route.fulfill({ status: 404, json: {} });
      const status = operation.uncertain ? 'uncertain' : statusReads < 3 ? 'processing' : 'complete';
      return route.fulfill({ json: { ...operation.reference, status, ...(status === 'complete' ? { result: operation.result } : {}) } });
    }
    const audio = (text: string) => { spoken.push(text); return route.fulfill({ contentType: 'audio/wav', body: promptWav() }); };
    if (path === '/api/auth/config') return route.fulfill({ json: { mode: 'local' } });
    if (path === '/api/upload-policy') {
      if (options.policy === 'down') return route.fulfill({ status: 500, json: { detail: 'down' } });
      return route.fulfill({ json: options.policy ?? FAST_POLICY });
    }
    if (path === '/api/profile') return route.fulfill({ json: { destination: 'session_ready', profile: { name: 'Maya', goal: 'Meetings', completed_attempts: 2 }, provider: 'openai' } });
    if (path === '/api/history') return route.fulfill({ json: [] });
    if (path === '/api/exercises/current') return route.fulfill({ json: lesson.exercise ?? null });
    if (path === '/api/recognition/acknowledgment-audio') return audio('Okay, I heard you.');
    if (path === '/api/lessons/current') return route.fulfill({ json: started ? state() : null });
    if (path === '/api/lessons/start') { started = true; return route.fulfill({ json: state() }); }
    if (path === '/api/lessons/structured' && request.method() === 'GET') { options.otherWriter?.(); return route.fulfill({ json: state() }); }
    if (path.endsWith('/attempts') || path === '/api/lessons/structured/attempts') {
      const plan = uploads[Math.min(nextUpload++, uploads.length - 1)];
      const body = request.postData() ?? '';
      const revision = Number(/name="revision"\r\n\r\n(\d+)/.exec(body)?.[1] ?? NaN);
      const uploadId = request.headers()['x-audli-upload-id'] ?? null;
      uploadRequests.push({ path, revision: Number.isNaN(revision) ? null : revision, uploadId });
      if (plan === 'hang') return new Promise<void>(() => {});
      if (plan === 'down') return route.abort('internetdisconnected');
      if (plan === 'bad') return route.fulfill({ status: 503, json: { detail: 'I had trouble transcribing your recording. Please try again.' } });
      if (path.startsWith('/api/lessons/')) {
        if (revision !== lesson.revision) return route.fulfill({ status: 409, json: { detail: 'Reload your current lesson checkpoint.' } });
        commitCheckin();
        if (plan === 'processing' || plan === 'uncertain') {
          operation = { reference: { operation_id: '00000000-0000-4000-8000-000000000001', kind: 'request' }, result: state(), uncertain: plan === 'uncertain' };
          return route.fulfill({ status: 202, json: operation.reference });
        }
        return plan === 'lost' ? route.abort('internetdisconnected') : route.fulfill({ json: state() });
      }
      const key = uploadId ?? `anonymous-${attempts.size}`;
      if (!attempts.has(key)) { transcriptions++; attempts.set(key, { id: `attempt-${attempts.size + 1}`, transcription: { text: 'The meeting changed', confidence: 0.99, uncertainty: [] } }); }
      if (plan === 'processing' || plan === 'uncertain') {
        operation = { reference: { operation_id: '00000000-0000-4000-8000-000000000001', kind: 'upload' }, result: attempts.get(key), uncertain: plan === 'uncertain' };
        return route.fulfill({ status: 202, json: operation.reference });
      }
      return plan === 'lost' ? route.abort('internetdisconnected') : route.fulfill({ json: attempts.get(key) });
    }
    if (path.startsWith('/api/lessons/')) {
      if (path.endsWith('/audio')) return audio(lesson.prompt ?? 'Prompt');
      if (path.endsWith('/answer')) { lesson.pending = null; lesson.phase = 'WELCOME_ACK' as never; lesson.prompt = 'Good to hear. Doing well, thanks for asking!'; lesson.revision++; return route.fulfill({ json: state() }); }
      if (path.endsWith('/end')) lesson.status = 'paused' as never;
      if (path.endsWith('/resume')) lesson.status = 'active' as never;
      if (path.endsWith('/heard')) { lesson.revision++; }
      return route.fulfill({ json: state() });
    }
    if (path.endsWith('/conversation/listened')) exerciseState = 'AWAITING_SUMMARY';
    if (path.endsWith('/assess')) { exerciseState = 'GIVING_FEEDBACK'; clip.completed_attempt_id = 'answer'; }
    if (path.endsWith('/conversation') || path.endsWith('/listened') || path.endsWith('/assess') || path.endsWith('/ready')) return route.fulfill({ json: conversation() });
    if (path.endsWith('/coach-audio')) return audio(conversation().prompt);
    return audio('Listening passage');
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start today’s session' })).toBeEnabled();
  return {
    requests, spoken, uploadRequests, lesson, attempts,
    completeOperation() { if (operation) { operation.uncertain = false; statusReads = 3; } },
    get statusReads() { return statusReads; },
    get transcriptions() { return transcriptions; },
    count: (method: string, suffix: string) => requests.filter(entry => entry.startsWith(method + ' ') && entry.endsWith(suffix)).length,
    audioRequests: () => requests.filter(entry => /\/audio$|coach-audio$|acknowledgment-audio$/.test(entry)).length,
  };
}

/** Advance the page's fake clock until `done()` is true (or fail). */
export async function until(page: Page, done: () => Promise<boolean> | boolean, seconds = 90) {
  for (let i = 0; i < seconds; i++) {
    if (await done()) return;
    await page.clock.runFor(1000); await page.waitForTimeout(25);
  }
  expect(await done()).toBe(true);
}
export const seen = (page: Page, text: string | RegExp) => async () => (await page.getByText(text).count()) > 0;
export type { Request };
