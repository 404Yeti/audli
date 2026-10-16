/** Browser-local diagnostics only. Never accept an Error, URL, session or payload. */
type Stage = 'auth_config' | 'session_restore' | 'token_refresh' | 'auth_gate_deadline' | 'retry_connection';
type Category = 'completed' | 'pending' | 'http_4xx' | 'http_5xx' | 'network' | 'invalid_response' | 'initialization' | 'timeout' | 'connected';
type Timing = { stage: Stage; elapsedMs: number; category: Category };
const retryKey = 'audli-auth-timeout-retry';
const pendingRefreshes = new Set<number>();
const refreshObservers = new Set<(elapsedMs: number | null, category: Category) => void>();

export function authHttpCategory(status: number): Category {
  return status >= 500 ? 'http_5xx' : status >= 400 ? 'http_4xx' : 'completed';
}

// Only the learner client's refresh endpoint is observed, never request/response
// bodies, headers, credentials, arbitrary URL strings or SDK error messages.
export const timedAuthFetch: typeof fetch = async (input, init) => {
  let refresh = false;
  try {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url);
    refresh = url.pathname.endsWith('/auth/v1/token') && url.searchParams.get('grant_type') === 'refresh_token';
  } catch { /* SDK still receives the original request unchanged. */ }
  if (!refresh) return fetch(input, init);
  const start = performance.now();
  pendingRefreshes.add(start);
  for (const observer of refreshObservers) observer(null, 'pending');
  try {
    const response = await fetch(input, init);
    for (const observer of refreshObservers) observer(performance.now() - start, authHttpCategory(response.status));
    return response;
  } catch (error) {
    for (const observer of refreshObservers) observer(performance.now() - start, 'network');
    throw error;
  } finally { pendingRefreshes.delete(start); }
};

export function markAuthTimeoutRetry() {
  try { sessionStorage.setItem(retryKey, String(Date.now())); } catch { /* Optional diagnostics. */ }
}

export class AuthDiagnostics {
  private started = performance.now();
  private stageStarted = this.started;
  private stage: 'auth_config' | 'session_restore' = 'auth_config';
  private timings: Timing[] = [];
  private refreshStarted: number | null = null;
  private stopped = false;
  private retry = false;
  private observe = (elapsedMs: number | null, category: Category) => {
    if (this.stopped) return;
    if (elapsedMs === null) this.refreshStarted = performance.now();
    else {
      this.refreshStarted = null;
      const config = this.timings.filter(timing => timing.stage === 'auth_config');
      this.timings = [...config, ...this.timings.filter(timing => timing.stage === 'token_refresh').slice(-2)];
      this.timings.push({ stage: 'token_refresh', elapsedMs: Math.round(elapsedMs), category });
    }
  };
  constructor() {
    try {
      const value = Number(sessionStorage.getItem(retryKey));
      this.retry = value > 0 && Date.now() - value >= 0 && Date.now() - value < 300_000;
    } catch { /* Optional diagnostics. */ }
    refreshObservers.add(this.observe);
    if (pendingRefreshes.size) this.refreshStarted = Math.min(...pendingRefreshes);
  }
  configComplete() {
    if (this.stopped) return;
    this.timings.push(this.timing('auth_config', this.stageStarted, 'completed'));
    this.stage = 'session_restore'; this.stageStarted = performance.now();
  }
  private timing(stage: Stage, start: number, category: Category): Timing {
    return { stage, elapsedMs: Math.max(0, Math.round(performance.now() - start)), category };
  }
  private emit(timing: Timing) {
    try { console.warn('[Audli auth startup]', timing); } catch { /* Logging cannot affect authentication. */ }
  }
  private clearRetry() {
    try { sessionStorage.removeItem(retryKey); } catch { /* Optional diagnostics. */ }
  }
  fail(category: Category) {
    if (this.stopped) return;
    for (const timing of this.timings) this.emit(timing);
    if (this.refreshStarted !== null) this.emit(this.timing('token_refresh', this.refreshStarted, 'pending'));
    this.emit(this.timing(this.stage, this.stageStarted, category === 'timeout' ? 'pending' : category));
    if (category === 'timeout') this.emit(this.timing('auth_gate_deadline', this.started, 'timeout'));
    this.clearRetry(); this.close();
  }
  ready() {
    if (this.stopped) return;
    if (this.retry || this.timings.some(timing => timing.category !== 'completed')) {
      for (const timing of this.timings) this.emit(timing);
      this.emit(this.timing(this.stage, this.stageStarted, 'completed'));
      if (this.retry) this.emit(this.timing('retry_connection', this.started, 'connected'));
      this.clearRetry();
    }
    this.close();
  }
  close() { this.stopped = true; refreshObservers.delete(this.observe); }
}
