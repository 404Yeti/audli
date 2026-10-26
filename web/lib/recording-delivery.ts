/** AUD-89 P2: the two recording uploads (exercise answer, opening check-in) sent through the server-delivered retry policy.
 *
 * With the policy disabled (the default, and the answer to any failure to read it) every function here does exactly what the page did
 * before: one request through `sendRecording`. With it enabled, the recording is sent with bounded retries (web/lib/upload-sender.ts).
 * The recording itself is the caller's: it is never cleared here except by a definitive rejection (413/415/422), as before. */
import { ApiError, UnreachableError, authenticatedFetch, setUploadIdHeader, type LessonOperation } from './api.ts';
import { decideCheckin, type Known } from './checkin-reconcile.ts';
import type { Recognition } from './hands-free.ts';
import type { LessonCheckpoint } from './lesson-lifecycle.ts';
import { recordingUpload, sendRecording } from './recording.ts';
import { createPolicyLoader, DISABLED, type UploadPolicy } from './upload-policy.ts';
import { deliver, boundedTask, browserEnv, DeliveryFailed, type DeliveryState, type SenderEnv } from './upload-sender.ts';

export const uploadPolicy = createPolicyLoader(async () => {
  return boundedTask(async signal => {
    const response = await authenticatedFetch('/api/upload-policy', { signal }, undefined, false);
    if (!response.ok) throw new Error('policy unavailable');
    return response.json();
  }, 1500);
});

/** The current policy; any failure to read it is DISABLED. Also switches the recording id header on only while the policy is enabled. */
export async function currentPolicy(signal?: AbortSignal, force = false): Promise<UploadPolicy> {
  let policy: UploadPolicy = DISABLED;
  if (force) uploadPolicy.reset();
  try { policy = await boundedTask(() => uploadPolicy.get(), 1500, signal); } catch { signal?.throwIfAborted(); policy = DISABLED; }
  setUploadIdHeader(policy.enabled);
  return policy;
}

const DEFINITIVE = new Set([413, 415, 422]);
export type Attempt = { id: string; transcription: Recognition; confirmed_text?: string | null };
/** `onState` receives sending / retrying / failed for the learner; `env` is the clock (tests only). */
export type DeliveryExtras = { onState?: (state: DeliveryState) => void; env?: SenderEnv };

export async function deliverExercise(operation: LessonOperation, exerciseId: string, blob: Blob, invalid: () => void,
  extras: DeliveryExtras = {}): Promise<Attempt> {
  const url = `/exercises/${exerciseId}/attempts`;
  const started = (extras.env ?? browserEnv).now();
  const policy = await currentPolicy(operation.signal);
  operation.signal.throwIfAborted();
  if (!policy.enabled) return sendRecording(() => operation.api<Attempt>(url, { method: 'POST', body: recordingUpload(blob) }), invalid);
  try {
    // The same recording keeps the same upload id on every send, so the server answers a repeat with the first attempt (P1).
    const delivered = await deliver<Attempt>({ policy: { ...policy, deadlineMs: policy.deadlineMs - ((extras.env ?? browserEnv).now() - started) }, bytes: blob.size, ...extras, cancelled: operation.signal,
      canRetry: async signal => (await currentPolicy(signal, true)).enabled,
      attempt: signal => operation.apiWith<Attempt>(url, { method: 'POST', body: recordingUpload(blob) }, signal) });
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
  const previous = original;
  if (!policy.enabled) return { value: await sendRecording(() => send(previous?.revision ?? lesson.revision), invalid), via: 'sent' };
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
      attempt: signal => send(known.revision, signal), reconcile,
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
