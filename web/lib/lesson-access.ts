// AUD-120: how the lesson page finds or starts an attempt, and what it does when that fails.
// Pure and dependency-injected so the rules are unit-testable without a browser. Rules, in order of importance:
//  1. Nothing waits forever: every request has a deadline.
//  2. Reads (GET) are retried a few times with backoff, only for failures that can be transient.
//  3. The one write (start an attempt) is idempotent by its request id, so it is replayed at most ONCE, with the SAME id, and only
//     after the current attempt has been re-read (the first request may have succeeded and only its response been lost).
//  4. A 409 is never retried blindly. Only the request-id conflict (a stored id that already belongs to another lesson) is
//     recovered, by forgetting that id and re-reading; every other 4xx is final.
//  5. Every failure carries a support reference: the server's when a response arrived, otherwise one made here.
import type { AttemptView } from './listening-core.ts';

export type FailureKind = 'timeout' | 'network' | 'unreachable' | 'server' | 'auth' | 'missing' | 'conflict' | 'limited' | 'rejected';
export type LoadFailure = { kind: FailureKind; status: number | null; reference: string; transient: boolean };
export type LoadResult =
  | { kind: 'ready'; view: AttemptView; existing: boolean }
  | { kind: 'error'; failure: LoadFailure };

export const READ_TIMEOUT_MS = 15_000;
export const WRITE_TIMEOUT_MS = 25_000;
/** Everything together (all tries, retries and recovery) gives up here, so a learner is never kept waiting longer than this. */
export const TOTAL_BUDGET_MS = 60_000;
export const RETRY_DELAYS_MS = [600, 1_800];       // two retries for a read: three tries in all

export type AccessDeps = {
  currentAttemptId(lessonId: string, signal: AbortSignal): Promise<string | null>;
  getAttempt(attemptId: string, signal: AbortSignal): Promise<AttemptView>;
  startAttempt(lessonId: string, fresh: boolean, signal: AbortSignal): Promise<AttemptView>;
  forgetRequestId(lessonId: string): void;
  sleep(ms: number, signal: AbortSignal): Promise<void>;
  newReference(): string;
  /** Tests only: shorter deadlines. */
  limits?: { read: number; write: number; total?: number };
};

/** What went wrong, from an error thrown by the API layer. `ApiError` and `UnreachableError` are recognised structurally. */
export function classify(error: unknown): { kind: FailureKind; status: number | null; reference: string | null; code: string | null } {
  const e = error as { name?: string; status?: number; reference?: string | null; code?: string | null } | null;
  const reference = typeof e?.reference === 'string' && e.reference ? e.reference : null;
  const code = typeof e?.code === 'string' ? e.code : null;
  if (e?.name === 'DeadlineError') return { kind: 'timeout', status: null, reference, code };
  if (e?.name === 'UnreachableError') return { kind: 'unreachable', status: typeof e.status === 'number' ? e.status : null, reference, code };
  if (typeof e?.status === 'number') {
    const status = e.status;
    const kind: FailureKind = status === 401 ? 'auth' : status === 404 ? 'missing' : status === 409 ? 'conflict' : status === 429 ? 'limited'
      : status >= 500 ? 'server' : 'rejected';
    return { kind, status, reference, code };
  }
  return { kind: 'network', status: null, reference, code };
}

/** Only failures that can pass on their own. 503 is the API saying "retry"; 500 is not retried (it may be a real fault). */
export function isTransient(error: unknown): boolean {
  const c = classify(error);
  if (c.kind === 'timeout' || c.kind === 'network' || c.kind === 'unreachable') return true;
  return c.kind === 'server' && (c.status === 502 || c.status === 503 || c.status === 504);
}

class DeadlineError extends Error { constructor() { super('The request took too long.'); this.name = 'DeadlineError'; } }

/** Runs one request under a deadline. Leaving the page (`outer`) aborts it and rethrows the abort, never a failure. */
async function bounded<T>(task: (signal: AbortSignal) => Promise<T>, ms: number, outer: AbortSignal): Promise<T> {
  outer.throwIfAborted();
  const controller = new AbortController();
  let timedOut = false;
  const onOuter = () => controller.abort();
  outer.addEventListener('abort', onOuter, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, ms);
  try { return await task(controller.signal); }
  catch (error) {
    if (outer.aborted) throw error;
    if (timedOut) throw new DeadlineError();
    throw error;
  } finally { clearTimeout(timer); outer.removeEventListener('abort', onOuter); }
}

async function reading<T>(deps: AccessDeps, outer: AbortSignal, task: (signal: AbortSignal) => Promise<T>): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try { return await bounded(task, deps.limits?.read ?? READ_TIMEOUT_MS, outer); }
    catch (error) {
      if (outer.aborted || attempt >= RETRY_DELAYS_MS.length || !isTransient(error)) throw error;
      await deps.sleep(RETRY_DELAYS_MS[attempt], outer);
    }
  }
}

async function adopt(deps: AccessDeps, outer: AbortSignal, attemptId: string): Promise<LoadResult> {
  return { kind: 'ready', view: await reading(deps, outer, s => deps.getAttempt(attemptId, s)), existing: true };
}

async function start(deps: AccessDeps, outer: AbortSignal, lessonId: string, fresh: boolean): Promise<LoadResult> {
  const send = (asFresh: boolean) => bounded(s => deps.startAttempt(lessonId, asFresh, s), deps.limits?.write ?? WRITE_TIMEOUT_MS, outer);
  try { return { kind: 'ready', view: await send(fresh), existing: false }; }
  catch (first) {
    if (outer.aborted) throw first;
    const c = classify(first);
    if (c.kind === 'conflict' && c.code === 'request_id_conflict') {
      // The id this browser kept already belongs to another lesson. Forget it, look again, and ask once more with a new id.
      deps.forgetRequestId(lessonId);
      if (!fresh) {
        const current = await reading(deps, outer, s => deps.currentAttemptId(lessonId, s));
        if (current) return adopt(deps, outer, current);
      }
      return { kind: 'ready', view: await send(fresh), existing: false };
    }
    if (!isTransient(first)) throw first;
    // Ambiguous: the attempt may exist already. Look before writing again; for "listen again" a new attempt was wanted, so go straight on.
    await deps.sleep(RETRY_DELAYS_MS[0], outer);
    if (!fresh) {
      const current = await reading(deps, outer, s => deps.currentAttemptId(lessonId, s));
      if (current) return adopt(deps, outer, current);
    }
    return { kind: 'ready', view: await send(false), existing: false };     // same stored id: the server replays instead of duplicating
  }
}

export async function loadLesson(lessonId: string, fresh: boolean, deps: AccessDeps, page: AbortSignal): Promise<LoadResult> {
  // `outer` ends when the page goes away OR the overall budget is spent; only the first is silent.
  const budget = new AbortController();
  const outer = new AbortController();
  const onPage = () => outer.abort(page.reason);
  page.addEventListener('abort', onPage, { once: true });
  if (page.aborted) onPage();
  const timer = setTimeout(() => { budget.abort(); outer.abort(); }, deps.limits?.total ?? TOTAL_BUDGET_MS);
  try {
    const existing = fresh ? null : await reading(deps, outer.signal, s => deps.currentAttemptId(lessonId, s));
    return existing ? await adopt(deps, outer.signal, existing) : await start(deps, outer.signal, lessonId, fresh);
  } catch (error) {
    if (page.aborted) throw error;
    const c = budget.signal.aborted ? { kind: 'timeout' as const, status: null, reference: null } : classify(error);
    return { kind: 'error', failure: { kind: c.kind, status: c.status, reference: c.reference ?? deps.newReference(), transient: budget.signal.aborted || isTransient(error) } };
  } finally { clearTimeout(timer); page.removeEventListener('abort', onPage); }
}

/** Copy for the error screen. Never exposes a status code or server text; the reference is how support finds the request. */
export function failureMessage(kind: FailureKind): { headline: string; detail: string } {
  const headline = 'We couldn’t load your lesson. Your progress is safe.';
  switch (kind) {
    case 'timeout': case 'network': case 'unreachable': case 'server':
      return { headline, detail: 'Audli took too long to answer. This is usually a slow or unstable connection. Try again in a moment.' };
    case 'auth': return { headline, detail: 'Your sign-in needs refreshing. Sign in again, then reopen the lesson.' };
    case 'missing': return { headline, detail: 'This lesson is not available to your account. Go back to your lessons and choose another.' };
    case 'limited': return { headline, detail: 'You have reached a limit for now. Try again a little later.' };
    default: return { headline, detail: 'Something went wrong on our side. Try again, and if it keeps happening, send us the reference below.' };
  }
}

export function newClientReference(random: () => string = () => globalThis.crypto.randomUUID()): string {
  return 'C-' + random().replace(/-/g, '').slice(0, 10);
}
