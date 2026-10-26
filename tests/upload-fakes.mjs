// Test helpers for the recording delivery tests (not a test file): a deterministic clock with timers and visibility, an operation stand-in,
// and a fake check-in server with the real compare-and-swap revision rule.
import { ApiError } from '../web/lib/api.ts';

const settle = () => new Promise(resolve => setImmediate(resolve));

export function fakeEnv({ hidden = false } = {}) {
  let now = 0, isHidden = hidden, next = 1;
  const timers = new Map(), listeners = new Set();
  const env = {
    now: () => now,
    setTimeout(callback, ms) { const id = next++; timers.set(id, { at: now + Math.max(0, ms), callback }); return id; },
    clearTimeout(id) { timers.delete(id); },
    visible: () => !isHidden,
    onVisibilityChange(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
  async function advance(ms) {
    const target = now + ms;
    for (;;) {
      await settle();
      const due = [...timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) break;
      now = Math.max(now, due[1].at); timers.delete(due[0]); due[1].callback();
    }
    now = target; await settle();
  }
  return { env, advance, settle, setHidden(value) { isHidden = value; for (const listener of [...listeners]) listener(); }, get now() { return now; }, get timers() { return timers.size; } };
}

export const POLICY = {
  baseMs: 8000, bytesPerSecond: 64000, minMs: 9000, maxMs: 30000, growth: [1, 1.25, 1.5], maxSends: 3, deadlineMs: 50000,
  backoffMs: [300, 900], slowHintMs: 3000,
};

/** An attempt that never answers until it is aborted (then it rejects like fetch does) or `failAfter` ms of fake time pass (then TypeError). */
export function hangingAttempt(clock, { failAfter = null, record } = {}) {
  return signal => new Promise((resolve, reject) => {
    record?.(signal);
    let handle = null;
    if (failAfter != null) handle = clock.env.setTimeout(() => reject(new TypeError('Load failed')), failAfter);
    signal.addEventListener('abort', () => { clock.env.clearTimeout(handle); reject(new DOMException('aborted', 'AbortError')); }, { once: true });
  });
}

/** What LessonOperation offers the delivery code, backed by handlers the test provides. */
export function fakeOperation({ api, apiWith } = {}) {
  const controller = new AbortController();
  return {
    signal: controller.signal, cancel: () => controller.abort(),
    api: api ?? (async () => { throw new Error('unexpected api call'); }),
    apiWith: apiWith ?? (async () => { throw new Error('unexpected apiWith call'); }),
  };
}

/** The check-in server: a lesson with a revision, a phase, a status and a pending transcription; uploads are compare-and-swap on the revision. */
export function checkinServer({ revision = 4, phase = 'WELCOME', status = 'active', pending = null } = {}) {
  const server = {
    lesson: { id: 'lesson-1', revision, phase, status, pending },
    transcriptions: 0, commits: 0, log: [], texts: ['I am fine, thank you.'],
    checkpoint() { return structuredClone(server.lesson); },
    /** One upload as the real route handles it: stale revision -> 409 before any transcription; otherwise transcribe, bump revision, set pending. */
    upload(sentRevision, text = server.texts[0]) {
      server.log.push(['upload', sentRevision]);
      if (server.lesson.status !== 'active' || server.lesson.revision !== sentRevision) throw new ApiError('Reload your current lesson checkpoint.', 409);
      server.transcriptions++;
      server.lesson = { ...server.lesson, revision: sentRevision + 1, pending: { text, confidence: 0.95, uncertainty: [] } };
      server.commits++;
      return server.checkpoint();
    },
    /** Other writers that bump the revision without an upload (end then resume), or change the phase. */
    endAndResume() { server.lesson = { ...server.lesson, revision: server.lesson.revision + 2 }; },
    pause() { server.lesson = { ...server.lesson, status: 'paused', revision: server.lesson.revision + 1 }; },
    advancePhase() { server.lesson = { ...server.lesson, phase: 'WELCOME_ACK', pending: null, revision: server.lesson.revision + 1 }; },
  };
  return server;
}

/** An exercise server with the P1 rule: the same upload id is the same attempt and costs no second transcription. */
export function exerciseServer() {
  const server = { attempts: new Map(), transcriptions: 0,
    upload(uploadId, text = 'The team rolled back the update.') {
      if (!server.attempts.has(uploadId)) { server.transcriptions++; server.attempts.set(uploadId, { id: `attempt-${server.attempts.size + 1}`, transcription: { text, confidence: 0.95, uncertainty: [] } }); }
      return structuredClone(server.attempts.get(uploadId));
    } };
  return server;
}
