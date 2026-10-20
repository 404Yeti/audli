import type { Page, Route } from '@playwright/test';
import { setup } from './homework-fixture';
import { promptWav } from './voice-fixture';

/** Stateful stand-in for the FastAPI lesson/planner contract (app/lesson_api.py, app/planner_api.py).
 *  Mock tests establish browser plumbing and lifecycle ordering, not provider accuracy. */
export type Server = ReturnType<typeof newServer>;
export type Options = {
  /** First server checkpoint. WELCOME exercises setup; CLOSING starts at feedback. */
  from?: 'WELCOME' | 'CLOSING';
  /** A planner topic check-in is due (every third completed lesson). */
  due?: boolean;
  /** Homework switched off for the account (nothing to decide). */
  homework?: boolean;
};

export function newServer(options: Options) {
  return {
    lesson: null as null | Record<string, unknown>, lastCompleted: null as string | null,
    due: options.due ?? false, resolved: null as null | 'answered' | 'skipped', revision: 1, claimed: false, pending: null as null | object,
    planRequests: 0, offers: 0, claims: 0, topicAudio: 0, topicAnswers: 0, topicTexts: 0, topicSkips: 0, starts: 0, heard: 0, farewells: 0,
    homeworkReviews: 0, order: [] as string[],
  };
}

const recognition = { text: 'Fine thanks', confidence: .95, uncertainty: [] };
const base = (id: string, phase: string, revision: number, extra: object = {}) => ({
  id, revision, status: phase === 'COMPLETED' ? 'completed' : 'active', phase, prompt: `Prompt for ${phase}`, pending: null, exercise: null,
  introduction_pending: false, introduction_prompt: null, elapsed_seconds: 30, remaining_seconds: 570, server_time: new Date().toISOString(),
  focus: 'details', exercises_completed: 1, strength: 'following the main idea', improvement_focus: null, encouragement: 'Thanks for listening, Maya.', ...extra,
});
const next: Record<string, string> = { WELCOME_ACK: 'TRANSITION', TRANSITION: 'CLOSING', CLOSING: 'COMPLETED' };

export function plan(server: Server) {
  const available = server.due && !server.resolved && !server.lesson;
  return { revision: server.revision, completed_lessons: 3, block_progress: 0, focus: 'Notice the details that matter', readiness: server.lesson ? 'resume_lesson' : 'ready',
    upcoming: [], completed: [], checkin: { after_lesson: 3, status: server.resolved ?? (server.due ? 'due' : 'not_due'), available,
      prompt: available ? 'Would you like to keep your listening topics, or try something different?' : null, pending: server.pending } };
}

export async function lifecycle(page: Page, options: Options = {}) {
  const server = newServer(options);
  const state = await setup(page, false, { autoPlayback: true, ...(options.homework === false ? { capability: false } : {}) });
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body });
  const audio = (route: Route) => route.fulfill({ contentType: 'audio/wav', body: promptWav() });
  await page.route('**/api/lessons/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/lessons/', '');
    const lesson = server.lesson;
    if (path === 'current') return json(route, lesson);
    if (path === 'start') {
      if (!lesson) {
        server.starts++; server.lesson = base('lesson', options.from ?? 'WELCOME', 0);
      }
      return json(route, server.lesson);
    }
    const [id, action] = path.split('/');
    if (action === 'topic-checkin') {
      server.offers++;
      server.order.push('legacy-offer');
      // Legacy completion-time claim (access(claim_lesson=...)). The client must no longer call it.
      const offered = server.lastCompleted === id && server.due && !server.claimed && !server.resolved;
      if (offered) { server.claimed = true; server.claims++; server.revision++; }
      return json(route, { offered, plan: offered ? { ...plan({ ...server, lesson: null }) } : null });
    }
    if (!action) return json(route, id === server.lastCompleted ? base(id, 'COMPLETED', 9) : lesson);
    if (action === 'farewell-audio') { server.farewells++; return audio(route); }
    if (action === 'homework') return route.fallback();
    if (!lesson || lesson.id !== id) return json(route, { detail: 'Lesson not found' }, 404);
    let body: { revision?: number } | null = null;
    try { body = route.request().postDataJSON(); } catch { /* multipart upload */ }
    if (action === 'audio' || action === 'introduction-audio') return audio(route);
    if (action === 'farewell-audio') { server.farewells++; return audio(route); }
    if (action === 'homework') return route.fallback();
    if (action === 'homework-review') { server.homeworkReviews++; return json(route, null); }
    if (action === 'attempts') { lesson.pending = recognition; lesson.revision = Number(lesson.revision) + 1; return json(route, lesson); }
    if (action === 'answer') { lesson.pending = null; lesson.phase = 'WELCOME_ACK'; lesson.revision = Number(lesson.revision) + 1; return json(route, lesson); }
    if (action === 'heard') {
      server.heard++;
      if (body?.revision !== lesson.revision) return json(route, { detail: 'Your lesson changed.' }, 409);
      const phase = next[String(lesson.phase)];
      if (!phase) return json(route, { detail: 'This phase requires a response.' }, 409);
      server.lesson = base(id, phase, Number(lesson.revision) + 1);
      if (phase === 'COMPLETED') { server.lastCompleted = id; server.lesson = null; return json(route, base(id, 'COMPLETED', Number(lesson.revision) + 1)); }
      return json(route, server.lesson);
    }
    return route.fallback();
  });
  await page.route('**/api/plan', route => { server.planRequests++; return json(route, plan(server)); });
  await page.route('**/api/plan/checkin/*', async route => {
    const action = new URL(route.request().url()).pathname.split('/').pop();
    if (action === 'audio') { server.topicAudio++; return audio(route); }
    if (server.lesson) return json(route, { detail: 'Finish or resume your lesson before this check-in.' }, 409);
    if (action === 'attempts') { server.pending = recognition; server.revision++; return json(route, plan(server)); }
    if (action === 'answer') { server.topicAnswers++; server.resolved = 'answered'; server.revision++; return json(route, plan(server)); }
    if (action === 'text') { server.topicTexts++; server.resolved = 'answered'; server.revision++; return json(route, plan(server)); }
    if (action === 'skip') { server.topicSkips++; server.resolved = 'skipped'; server.revision++; return json(route, plan(server)); }
    return route.fallback();
  });
  return { server, homework: state };
}

/** Counts every distinct entry to the topic-preference UI, across reloads of one test. */
export async function countTopicEntries(page: Page) {
  await page.addInitScript(() => {
    const key = 'topic-entries';
    const observe = () => new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes)
        if (node instanceof Element && (node.matches('.topic-checkin') || node.querySelector('.topic-checkin'))) sessionStorage.setItem(key, String(Number(sessionStorage.getItem(key) ?? 0) + 1));
    }).observe(document, { childList: true, subtree: true });
    observe();
  });
  return () => page.evaluate(() => Number(sessionStorage.getItem('topic-entries') ?? 0));
}

/** Records every lifecycle phase the page enters, across reloads of one test. */
export async function recordPhases(page: Page) {
  await page.addInitScript(() => {
    const key = 'phase-log';
    const note = () => {
      const phase = document.documentElement.dataset.lifecyclePhase;
      if (!phase) return;
      const log: string[] = JSON.parse(sessionStorage.getItem(key) ?? '[]');
      if (log.at(-1) !== phase) sessionStorage.setItem(key, JSON.stringify([...log, phase]));
    };
    // The root element may not exist yet when init scripts run; sample it, bounded by phase duration.
    setInterval(note, 10);
  });
  // Include the live phase: the sampler is timer-driven, so a phase that is already on screen can be missing from the
  // log when it is read (seen twice in CI with the DOM correctly on HOME). The log still records order of earlier phases.
  return () => page.evaluate(() => {
    const log: string[] = JSON.parse(sessionStorage.getItem('phase-log') ?? '[]');
    const live = document.documentElement.dataset.lifecyclePhase;
    return live && log.at(-1) !== live ? [...log, live] : log;
  });
}
export const phaseNow = (page: Page) => page.evaluate(() => document.documentElement.dataset.lifecyclePhase);
