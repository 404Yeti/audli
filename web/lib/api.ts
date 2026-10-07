let accessToken: (() => Promise<string | null>) | null = null;
let authRevision = 0;
const pendingOperations = new Set<AbortController>();
export function currentAuthRevision() { return authRevision; }

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
  const response = await fetch(url, { ...init, headers, cache: 'no-store', credentials: 'omit', redirect: 'error' });
  operation?.assertCurrent();
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

export class ApiError extends Error {
  status: number;
  uncertain: boolean;
  constructor(message: string, status: number, uncertain = false) { super(message); this.status = status; this.uncertain = uncertain; }
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
      !!(data && typeof data === 'object' && 'uncertain' in data && data.uncertain === true));
  }
  return data as T;
}
