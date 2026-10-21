let accessToken: (() => Promise<string | null>) | null = null;
let authRevision = 0;
const pendingOperations = new Set<AbortController>();
export function currentAuthRevision() { return authRevision; }

/** End pending work before starting a new onboarding run in the same account. */
export function cancelPendingOperations() {
  for (const controller of pendingOperations) controller.abort();
  pendingOperations.clear();
}

export function setAccessTokenProvider(provider: (() => Promise<string | null>) | null) {
  accessToken = provider;
  authRevision++;
  for (const controller of pendingOperations) controller.abort();
  pendingOperations.clear();
}

/** A component lifetime cannot be revived by a later mount or account. */
export class LessonLifetime {
  private active = true;
  private revision = authRevision;
  private operations = new Set<LessonOperation>();
  get current() { return this.active && this.revision === authRevision; }
  begin(): LessonOperation {
    if (!this.current) throw new DOMException('Lesson lifetime ended.', 'AbortError');
    const operation = new LessonOperation(this);
    operation.assertCurrent();
    this.operations.add(operation);
    return operation;
  }
  release(operation: LessonOperation) { this.operations.delete(operation); }
  close() {
    this.active = false;
    for (const operation of this.operations) operation.cancel();
    this.operations.clear();
  }
}

export class LessonOperation {
  private revision = authRevision;
  private controller = new AbortController();
  private lifetime: LessonLifetime;
  constructor(lifetime: LessonLifetime) {
    this.lifetime = lifetime;
    pendingOperations.add(this.controller);
  }
  get signal() { return this.controller.signal; }
  get current() { return this.lifetime.current && this.revision === authRevision && !this.signal.aborted; }
  assertCurrent(discard?: () => void) {
    if (!this.current) { discard?.(); throw new DOMException('Lesson operation cancelled.', 'AbortError'); }
  }
  release() { pendingOperations.delete(this.controller); this.lifetime.release(this); }
  cancel() { this.controller.abort(); this.release(); }
  async wait<T>(task: () => Promise<T>, discard?: (value: T) => void): Promise<T> {
    this.assertCurrent();
    const value = await task();
    if (!this.current) discard?.(value);
    this.assertCurrent();
    return value;
  }
  api<T>(url: string, init?: RequestInit, onResponse?: (status: number) => void): Promise<T> {
    this.assertCurrent();
    return this.wait(() => api<T>(url, { ...init, signal: this.signal }, onResponse, this));
  }
  audio(url: string): Promise<string> {
    this.assertCurrent();
    return this.wait(() => audioBlobUrl(url, this.signal, this), URL.revokeObjectURL);
  }
}

export async function authenticatedFetch(url: string, init?: RequestInit, operation?: LessonOperation): Promise<Response> {
  operation?.assertCurrent();
  if (!url.startsWith('/api/') || url.includes('\\')) throw new Error('Invalid Audli API path.');
  const revision = authRevision;
  let requestToken: string | null = null;
  const headers = new Headers(init?.headers);
  if (accessToken) {
    const token = await accessToken();
    operation?.assertCurrent();
    if (!token) throw new Error('Please sign in to continue.');
    if (revision !== authRevision) throw new Error('Your account changed. Please retry.');
    requestToken = token;
    headers.set('Authorization', `Bearer ${token}`);
  }
  const submittedAt = performance.now();
  if (process.env.NODE_ENV === 'development' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY === '1') console.debug('[Audli turn]', { stage: 'request_submission', elapsedMs: 0 });
  const response = await fetch(url, { ...init, headers, cache: 'no-store', credentials: 'omit', redirect: 'error' });
  if (process.env.NODE_ENV === 'development' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY === '1') console.debug('[Audli turn]', { stage: 'response_headers', elapsedMs: Math.round(performance.now() - submittedAt) });
  operation?.assertCurrent();
  if (typeof window !== 'undefined' && response.ok && revision === authRevision && response.headers.get('X-Audli-Tutor-Fallback') === 'true') window.dispatchEvent(new Event('audli-backup-voice'));
  if (response.status === 401 && revision === authRevision && typeof window !== 'undefined') {
    // A late response for an older token must not discard a refreshed session.
    const currentToken = accessToken ? await accessToken() : null;
    operation?.assertCurrent();
    if (revision === authRevision && currentToken === requestToken) window.dispatchEvent(new Event('audli-session-expired'));
  }
  return response;
}

export async function audioBlobUrl(url: string, signal?: AbortSignal, operation?: LessonOperation): Promise<string> {
  const response = await authenticatedFetch(url, { signal }, operation);
  if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in again.' : 'Audio playback is unavailable. Please retry.');
  const blob = await response.blob();
  operation?.assertCurrent();
  return URL.createObjectURL(blob);
}

/** Structured daily-limit refusal (HTTP 429): a stable code, never a generic failure. */
export type LimitInfo = { code: 'lesson_cooldown' | 'lesson_time_expired'; nextAvailableAt: string | null; retryAfterSeconds: number | null; serverTime: string | null };
export class ApiError extends Error {
  status: number;
  uncertain: boolean;
  limit?: LimitInfo;
  constructor(message: string, status: number, uncertain = false, limit?: LimitInfo) { super(message); this.status = status; this.uncertain = uncertain; this.limit = limit; }
}
export function limitFrom(data: unknown): LimitInfo | undefined {
  const error = data && typeof data === 'object' && 'error' in data ? (data as { error: unknown }).error : null;
  if (!error || typeof error !== 'object') return undefined;
  const e = error as Record<string, unknown>;
  if (e.code !== 'lesson_cooldown' && e.code !== 'lesson_time_expired') return undefined;
  return { code: e.code, nextAvailableAt: typeof e.next_available_at === 'string' ? e.next_available_at : null,
    retryAfterSeconds: typeof e.retry_after_seconds === 'number' ? e.retry_after_seconds : null, serverTime: typeof e.server_time === 'string' ? e.server_time : null };
}
export const isLimitError = (error: unknown): error is ApiError & { limit: LimitInfo } => error instanceof ApiError && error.status === 429 && !!error.limit;
/** For raw fetch paths (audio): turn a 429 response into the same structured error. */
export async function limitErrorFrom(response: Response): Promise<ApiError | null> {
  if (response.status !== 429) return null;
  const data: unknown = await response.json().catch(() => null);
  const limit = limitFrom(data);
  if (!limit) return null;
  const detail = data && typeof data === 'object' && 'detail' in data && typeof (data as { detail: unknown }).detail === 'string' ? (data as { detail: string }).detail : 'Your next lesson opens soon.';
  return new ApiError(detail, 429, false, limit);
}
export async function api<T>(url: string, init?: RequestInit, onResponse?: (status: number) => void, operation?: LessonOperation): Promise<T> {
  const response = await authenticatedFetch(`/api${url}`, init, operation);
  operation?.assertCurrent();
  onResponse?.(response.status);
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    // The Next.js proxy can return HTML/plain text when FastAPI is unreachable.
    throw new Error('Audli could not reach the server. Check that FastAPI is running and try again.');
  }
  operation?.assertCurrent();
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
    throw new ApiError(typeof detail === 'string' ? detail : 'Please check your input and try again.', response.status,
      !!(data && typeof data === 'object' && 'uncertain' in data && data.uncertain === true), response.status === 429 ? limitFrom(data) : undefined);
  }
  return data as T;
}
