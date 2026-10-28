import { OperationProcessing, awaitOperation, type OperationReference, type OperationStatus } from './operation-status.ts';
/** AUD-89 P2: the two recording uploads (exercise answer, opening check-in) sent through the server-delivered retry policy.
 *
 * With the policy disabled (the default, and the answer to any failure to read it) every function here does exactly what the page did
 * before: one request through `sendRecording`. With it enabled, the recording is sent with bounded retries (web/lib/upload-sender.ts).
 * The recording itself is the caller's: it is never cleared here except by a definitive rejection (413/415/422), as before. */
import { ApiError, UnreachableError, authenticatedFetch, resolveApiUrl, type LessonOperation } from './api.ts';
import { decideCheckin, type Known } from './checkin-reconcile.ts';
import type { Recognition } from './hands-free.ts';
import type { LessonCheckpoint } from './lesson-lifecycle.ts';
import { retainUploadId } from './upload-delivery.ts';
import { recordingUpload, sendRecording } from './recording.ts';
import { createPolicyLoader, DISABLED, type UploadPolicy } from './upload-policy.ts';
import { deliver, boundedTask, browserEnv, DeliveryFailed, type DeliveryState, type SenderEnv } from './upload-sender.ts';

const pendingOperations = new WeakMap<Blob, OperationReference>();
async function completeOperation<T>(operation: LessonOperation, blob: Blob, signal: AbortSignal, env: SenderEnv): Promise<T> {
  const reference = pendingOperations.get(blob);
  if (!reference) throw new DeliveryFailed('ambiguous', 0);
  const result = await awaitOperation<T>(reference, (path, abort) => operation.apiWith<OperationStatus<T>>(path, undefined, abort), signal,
    abort => new Promise<void>((resolve, reject) => {
      const stop = () => { env.clearTimeout(timer); reject(new DOMException('Cancelled.', 'AbortError')); };
      const timer = env.setTimeout(() => { abort.removeEventListener('abort', stop); resolve(); }, 500);
      if (abort.aborted) stop(); else abort.addEventListener('abort', stop, { once: true });
    }));
  pendingOperations.delete(blob);
  return result;
}
async function recoverOperation<T>(operation: LessonOperation, blob: Blob, env: SenderEnv): Promise<T> {
  const controller = new AbortController();
  let hidden = !env.visible();
  const cancel = () => controller.abort();
  const hide = () => { hidden = true; cancel(); };
  const stop = env.onVisibilityChange(() => { if (!env.visible()) hide(); });
  const stopPage = env.onPageHide?.(hide);
  operation.signal.addEventListener('abort', cancel, { once: true });
  if (operation.signal.aborted || hidden) cancel();
  try { return await boundedTask(signal => completeOperation<T>(operation, blob, signal, env), 50000, controller.signal, env); }
  catch (error) {
    operation.signal.throwIfAborted();
    if (hidden) throw new DeliveryFailed('background', 0);
    throw error;
  } finally { stop(); stopPage?.(); operation.signal.removeEventListener('abort', cancel); controller.abort(); }
}
async function sendOrRemember<T>(blob: Blob, send: () => Promise<T>): Promise<T> {
  try { return await send(); } catch (error) {
    if (error instanceof OperationProcessing) pendingOperations.set(blob, error.reference);
    throw error;
  }
}

export const uploadPolicy = createPolicyLoader(async () => {
  return boundedTask(async signal => {
    const response = await authenticatedFetch('/api/upload-policy', { signal }, undefined, false);
    if (!response.ok) throw new Error('policy unavailable');
    return response.json();
  }, 1500);
});

/** The current policy; any failure to read it is DISABLED. Recording identity is owned by the recording, independently of the current policy. */
export async function currentPolicy(signal?: AbortSignal, force = false): Promise<UploadPolicy> {
  let policy: UploadPolicy = DISABLED;
  // The existing experimental cross-origin path does not send upload IDs. Do not retry it or change CORS.
  if (resolveApiUrl('/api/upload-policy') !== '/api/upload-policy') return DISABLED;
  if (force) uploadPolicy.reset();
  try { policy = await boundedTask(() => uploadPolicy.get(), 1500, signal); } catch { signal?.throwIfAborted(); policy = DISABLED; }
  return policy;
}

const DEFINITIVE = new Set([413, 415, 422]);
export type Attempt = { id: string; transcription: Recognition; confirmed_text?: string | null };
/** `onState` receives sending / retrying / failed for the learner; `env` is the clock (tests only). */
export type DeliveryExtras = { onState?: (state: DeliveryState) => void; env?: SenderEnv };

export async function deliverExercise(operation: LessonOperation, exerciseId: string, blob: Blob, invalid: () => void,
  extras: DeliveryExtras = {}): Promise<Attempt> {
  const url = `/exercises/${exerciseId}/attempts`;
  const env = extras.env ?? browserEnv;
  const started = env.now();
  const policy = await currentPolicy(operation.signal);
  operation.signal.throwIfAborted();
  // A retained processing acknowledgment is read before any explicit resend, even after a policy kill switch.
  if (pendingOperations.has(blob)) return recoverOperation<Attempt>(operation, blob, env);
  if (!policy.enabled) {
    try { return await sendRecording(() => sendOrRemember(blob, () => operation.api<Attempt>(url, { method: 'POST', body: recordingUpload(blob) })), invalid); }
    catch (error) { if (!pendingOperations.has(blob)) throw error;
      return recoverOperation<Attempt>(operation, blob, env); }
  }
  retainUploadId(blob);
  try {
    // The same recording keeps the same upload id on every send, so the server answers a repeat with the first attempt (P1).
    const delivered = await deliver<Attempt>({ policy: { ...policy, deadlineMs: policy.deadlineMs - ((extras.env ?? browserEnv).now() - started) }, bytes: blob.size, ...extras, cancelled: operation.signal,
      canRetry: async signal => (await currentPolicy(signal, true)).enabled,
      processing: (_ack, signal) => completeOperation<Attempt>(operation, blob, signal, env),
      attempt: signal => sendOrRemember(blob, () => operation.apiWith<Attempt>(url, { method: 'POST', body: recordingUpload(blob) }, signal)) });
    return delivered.value;
  } catch (error) {
    if (error instanceof ApiError && DEFINITIVE.has(error.status)) invalid();
    throw error;
  }
}

const checkinRecordings = new WeakMap<Blob, Known>();
export type CheckinResult = { value: LessonCheckpoint; via: 'sent' | 'adopted' | 'moved' };

/** The opening check-in answer. `lesson` is the checkpoint the form's revision comes from; `serverPending` is what the server held before this send
 * (the page may be hiding it locally after an unreliable answer). */
export async function deliverCheckin(operation: LessonOperation, lesson: LessonCheckpoint, blob: Blob, invalid: () => void,
  serverPending: Recognition | null = lesson.pending, extras: DeliveryExtras = {}): Promise<CheckinResult> {
  const original = checkinRecordings.get(blob);
  if (original && original.lessonId !== lesson.id) {
    extras.onState?.({ kind: 'failed', reason: 'ambiguous', sends: 0 });
    throw new DeliveryFailed('ambiguous', 0);
  }
  const lessonId = original?.lessonId ?? lesson.id;
  const send = (revision: number, signal?: AbortSignal) => {
    const form = recordingUpload(blob); form.append('revision', String(revision));
    const init = { method: 'POST', body: form };
    return signal ? operation.apiWith<LessonCheckpoint>(`/lessons/${lessonId}/attempts`, init, signal) : operation.api<LessonCheckpoint>(`/lessons/${lessonId}/attempts`, init);
  };
  const env = extras.env ?? browserEnv;
  const started = env.now();
  const policy = await currentPolicy(operation.signal);
  operation.signal.throwIfAborted();
  if (pendingOperations.has(blob)) return { value: await recoverOperation<LessonCheckpoint>(operation, blob, env), via: 'adopted' };
  const previous = original;
  if (!policy.enabled) {
    try { return { value: await sendRecording(() => sendOrRemember(blob, () => send(previous?.revision ?? lesson.revision)), invalid), via: 'sent' }; }
    catch (error) { if (!pendingOperations.has(blob)) throw error;
      return { value: await recoverOperation<LessonCheckpoint>(operation, blob, env), via: 'adopted' }; }
  }
  retainUploadId(blob);
  const known: Known = previous ?? { lessonId: lesson.id, revision: lesson.revision, phase: lesson.phase, pending: serverPending };
  const reconcile = async (signal: AbortSignal) => {
    const server = await operation.apiWith<LessonCheckpoint>(`/lessons/${known.lessonId}`, undefined, signal);
    return decideCheckin(known, server).kind === 'resend' ? { kind: 'resend' as const } : { kind: 'stop' as const };
  };
  try {
    // An explicit Send again must reconcile the original checkpoint before sending retained audio.
    if (previous) {
      const decision = await boundedTask(reconcile, Math.min(policy.maxMs, policy.deadlineMs - (env.now() - started)), operation.signal, env);
      if (decision.kind !== 'resend') throw new DeliveryFailed('ambiguous', 0);
    }
    checkinRecordings.set(blob, known);
    const delivered = await deliver<LessonCheckpoint>({ policy: { ...policy, deadlineMs: policy.deadlineMs - (env.now() - started) },
      bytes: blob.size, ...extras, cancelled: operation.signal,
      processing: (_ack, signal) => completeOperation<LessonCheckpoint>(operation, blob, signal, env),
      attempt: signal => sendOrRemember(blob, () => send(known.revision, signal)), reconcile,
      canRetry: async signal => (await currentPolicy(signal, true)).enabled });
    checkinRecordings.delete(blob);
    return { value: delivered.value, via: delivered.via };
  } catch (error) {
    if (error instanceof ApiError && DEFINITIVE.has(error.status)) invalid();
    if ((error instanceof TypeError || error instanceof UnreachableError) && !operation.signal.aborted) {
      extras.onState?.({ kind: 'failed', reason: 'ambiguous', sends: 0 });
      throw new DeliveryFailed('ambiguous', 0);
    }
    if (error instanceof DeliveryFailed) extras.onState?.({ kind: 'failed', reason: error.reason, sends: error.sends });
    throw error;
  }
}

/** Explicit learner action only. Older servers keep their existing recovery path. */
export async function prepareNewRecording(operation: LessonOperation, blob: Blob | null, exerciseId?: string): Promise<void> {
  let enabled = false;
  try { enabled = (await boundedTask(signal => operation.apiWith<{ durable_operations: boolean }>('/operation-capabilities', undefined, signal), 1500, operation.signal)).durable_operations === true; }
  catch (error) { operation.signal.throwIfAborted(); if (!(error instanceof ApiError && error.status === 404)) throw error; }
  if (!enabled) return;
  const reference = blob ? pendingOperations.get(blob) : null;
  if (reference) {
    await boundedTask(signal => operation.apiWith(`/operations/${reference.operation_id}/record-again`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: reference.kind }) }, signal), 10000, operation.signal);
    if (blob) pendingOperations.delete(blob);
  } else if (exerciseId) {
    const current = await boundedTask(signal => operation.apiWith<{ pending_attempt: { id: string } | null; active_followup: { id: string } | null }>(`/exercises/${exerciseId}/conversation`, undefined, signal), 10000, operation.signal);
    if (current.pending_attempt) await boundedTask(signal => operation.apiWith(`/exercises/${exerciseId}/record-again`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pending_attempt_id: current.pending_attempt!.id, followup_id: current.active_followup?.id ?? null }) }, signal), 10000, operation.signal);
  }
}
