/** Bounded recording delivery. Every stage races cancellation and a wall-clock deadline. */
import { ApiError, UnreachableError } from './api.ts';
import { sendTimeoutMs, type PolicyNumbers } from './upload-policy.ts';
export type DeliveryReason = 'timeout' | 'network' | 'server' | 'deadline' | 'background' | 'ambiguous';
export type DeliveryState =
  | { kind: 'sending'; send: number; of: number; slow: boolean }
  | { kind: 'retrying'; next: number; of: number }
  | { kind: 'failed'; reason: DeliveryReason; sends: number }
  | { kind: 'moved' }
  | { kind: 'delivered'; via: 'sent' | 'adopted' };
export class DeliveryFailed extends Error {
  readonly reason: DeliveryReason;
  readonly sends: number;
  constructor(reason: DeliveryReason, sends: number) {
    super("We couldn't confirm your answer was received. Your recording is available while this page stays open.");
    this.name = 'DeliveryFailed'; this.reason = reason; this.sends = sends;
  }
}
export type Reconciled<T> = { kind: 'adopt' | 'moved'; value: T } | { kind: 'resend' } | { kind: 'stop' };
export type SenderEnv = {
  now(): number; setTimeout(callback: () => void, ms: number): unknown; clearTimeout(handle: unknown): void;
  visible(): boolean; onVisibilityChange(listener: () => void): () => void;
  onPageHide?(listener: () => void): () => void;
};
export const browserEnv: SenderEnv = {
  now: () => Date.now(), setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: handle => clearTimeout(handle as ReturnType<typeof setTimeout>),
  visible: () => typeof document === 'undefined' || document.visibilityState !== 'hidden',
  onVisibilityChange: fn => {
    if (typeof document === 'undefined') return () => {};
    document.addEventListener('visibilitychange', fn);
    return () => document.removeEventListener('visibilitychange', fn);
  },
  onPageHide: fn => {
    if (typeof window === 'undefined') return () => {};
    window.addEventListener('pagehide', fn);
    return () => window.removeEventListener('pagehide', fn);
  },
};
const abortError = () => new DOMException('Upload cancelled.', 'AbortError');
/** Aborting a fetch does not cancel server work. Late promises are observed but cannot resolve this stage. */
export function boundedTask<T>(task: (signal: AbortSignal) => Promise<T>, ms: number, cancelled?: AbortSignal,
  env: SenderEnv = browserEnv): Promise<T> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    let settled = false;
    let timer: unknown = undefined;
    const end = (error: unknown, value?: T) => {
      if (settled) return;
      settled = true; env.clearTimeout(timer); cancelled?.removeEventListener('abort', cancel);
      controller.abort();
      if (error) reject(error); else resolve(value as T);
    };
    const cancel = () => end(abortError());
    if (cancelled?.aborted) { cancel(); return; }
    if (ms <= 0) { end(new DeliveryFailed('deadline', 0)); return; }
    cancelled?.addEventListener('abort', cancel, { once: true });
    timer = env.setTimeout(() => end(new DeliveryFailed('timeout', 0)), ms);
    Promise.resolve().then(() => {
      if (controller.signal.aborted) throw abortError();
      return task(controller.signal);
    }).then(value => end(null, value), error => end(error));
  });
}
export type DeliverOptions<T> = {
  policy: PolicyNumbers; bytes: number; attempt(signal: AbortSignal, send: number): Promise<T>;
  reconcile?(signal: AbortSignal): Promise<Reconciled<T>>;
  /** Revalidate before another automatic send; a disabled/unavailable policy stops recovery. */
  canRetry?(signal: AbortSignal): Promise<boolean>;
  onState?(state: DeliveryState): void; cancelled?: AbortSignal; env?: SenderEnv;
};
export type Delivered<T> = { value: T; via: 'sent' | 'adopted' | 'moved'; sends: number };
function retryReason(error: unknown): DeliveryReason | null {
  if (error instanceof DeliveryFailed) return error.reason;
  if (error instanceof ApiError) return [409, 500, 502, 503, 504].includes(error.status) ? 'server' : null;
  if (error instanceof UnreachableError) return 'server';
  return error instanceof TypeError ? 'network' : null;
}
export async function deliver<T>(options: DeliverOptions<T>): Promise<Delivered<T>> {
  const { policy, attempt, reconcile, canRetry, cancelled, onState } = options;
  const env = options.env ?? browserEnv;
  const endAt = env.now() + policy.deadlineMs;
  const lifetime = new AbortController();
  let backgrounded = !env.visible(), sends = 0;
  const cancel = () => lifetime.abort();
  const hide = () => { backgrounded = true; lifetime.abort(); };
  const visibility = () => { if (!env.visible()) hide(); };
  const stopVisibility = env.onVisibilityChange(visibility);
  const stopHide = env.onPageHide?.(hide);
  cancelled?.addEventListener('abort', cancel, { once: true });
  if (cancelled?.aborted || backgrounded) lifetime.abort();
  const emit = (state: DeliveryState) => { try { onState?.(state); } catch { /* Presentation cannot affect delivery. */ } };
  const stage = <V>(task: (signal: AbortSignal) => Promise<V>, max = policy.maxMs) =>
    boundedTask(task, Math.min(max, endAt - env.now()), lifetime.signal, env);
  const fail = (reason: DeliveryReason): never => {
    emit({ kind: 'failed', reason, sends }); throw new DeliveryFailed(reason, sends);
  };
  try {
    while (sends < policy.maxSends) {
      if (cancelled?.aborted) throw abortError();
      if (backgrounded || !env.visible()) return fail('background');
      if (env.now() >= endAt) return fail('deadline');
      sends++;
      emit({ kind: 'sending', send: sends, of: policy.maxSends, slow: false });
      const slow = env.setTimeout(() => emit({ kind: 'sending', send: sends, of: policy.maxSends, slow: true }), policy.slowHintMs);
      let error: unknown;
      try {
        const value = await stage(signal => attempt(signal, sends), sendTimeoutMs(policy, options.bytes, sends - 1));
        emit({ kind: 'delivered', via: 'sent' }); return { value, via: 'sent', sends };
      } catch (cause) { error = cause; }
      finally { env.clearTimeout(slow); }
      if (cancelled?.aborted) throw abortError();
      if (backgrounded) return fail('background');
      const reason = retryReason(error);
      if (!reason || (error instanceof ApiError && error.status === 409 && !reconcile)) throw error;
      if (env.now() >= endAt) return fail('deadline');
      if (reconcile) {
        let decision: Reconciled<T>;
        try { decision = await stage(reconcile); }
        catch { if (cancelled?.aborted) throw abortError(); return fail(backgrounded ? 'background' : 'ambiguous'); }
        if (decision.kind === 'stop') return fail('ambiguous');
        if (decision.kind !== 'resend') return { value: decision.value, via: decision.kind === 'adopt' ? 'adopted' : 'moved', sends };
      }
      if (sends >= policy.maxSends) return fail(reason);
      const wait = policy.backoffMs[Math.min(sends - 1, policy.backoffMs.length - 1)] ?? 0;
      if (wait >= endAt - env.now()) return fail('deadline');
      emit({ kind: 'retrying', next: sends + 1, of: policy.maxSends });
      await stage(signal => new Promise<void>(resolve => {
        const timer = env.setTimeout(resolve, wait);
        signal.addEventListener('abort', () => env.clearTimeout(timer), { once: true });
      }), endAt - env.now());
      if (canRetry && !await stage(canRetry)) return fail('ambiguous');
    }
    return fail('deadline');
  } catch (error) {
    if (cancelled?.aborted) throw abortError();
    if (backgrounded) return fail('background');
    if (error instanceof DeliveryFailed && error.sends === 0) return fail(env.now() >= endAt ? 'deadline' : error.reason);
    throw error;
  } finally {
    lifetime.abort(); stopVisibility(); stopHide?.(); cancelled?.removeEventListener('abort', cancel);
  }
}
