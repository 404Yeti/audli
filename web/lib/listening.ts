// API calls for the 2.0 listening lesson. Authentication comes from the shared token provider behind api().
import { ApiError, api, authenticatedFetch, resolveApiUrl } from './api.ts';
import { newRequestId, type AttemptView, type CatalogItem, type Progress } from './listening-core.ts';

export const listCatalog = () => api<CatalogItem[]>('/listening/lessons');
import type { DailyChallenge } from './daily-core.ts';
export const getDaily = () => api<DailyChallenge>('/listening/daily');
export const getProgress = () => api<Progress>('/listening/progress');
export const currentAttemptId = (lessonId: string) =>
  api<{ attempt_id: string | null }>(`/listening/lessons/${encodeURIComponent(lessonId)}/attempts/current`).then(r => r.attempt_id);
export const getAttempt = (attemptId: string) => api<AttemptView>(`/listening/attempts/${encodeURIComponent(attemptId)}`);
/** One answer: `{ option_id }` (multiple choice), `{ text }` (fill in the blank) or `{ order }` (sequencing). */
export type AnswerBody = { option_id: string } | { text: string } | { order: string[] };
export const submitAnswer = (attemptId: string, exerciseId: string, answer: AnswerBody) =>
  api<AttemptView>(`/listening/attempts/${encodeURIComponent(attemptId)}/answers`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ exercise_id: exerciseId, ...answer }) });

const spokenPath = (attemptId: string, exerciseId: string, action: string) =>
  `/listening/attempts/${encodeURIComponent(attemptId)}/exercises/${encodeURIComponent(exerciseId)}/${action}`;
export const skipSpoken = (attemptId: string, exerciseId: string) => api<AttemptView>(spokenPath(attemptId, exerciseId, 'skip'), { method: 'POST' });
export const retrySpoken = (attemptId: string, exerciseId: string) => api<AttemptView>(spokenPath(attemptId, exerciseId, 'retry'), { method: 'POST' });

/** Sends one recording. `uploadId` names this recording, so sending it again after a dropped connection never makes a second answer. */
export async function sendRecording(attemptId: string, exerciseId: string, blob: Blob, uploadId: string): Promise<AttemptView> {
  const url = `/api${spokenPath(attemptId, exerciseId, 'recording')}?upload_id=${encodeURIComponent(uploadId)}`;
  const response = await authenticatedFetch(url, { method: 'POST', headers: { 'Content-Type': blob.type.split(';')[0] || 'audio/webm' }, body: blob });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(typeof data?.detail === 'string' ? data.detail : 'We couldn’t send your recording.', response.status);
  return data as AttemptView;
}

const requestKey = (lessonId: string) => `audli.listening.request.${lessonId}`;

/** Creates (or resumes) the attempt. The request id survives a refresh mid-request, so the server never makes two. */
export async function startAttempt(lessonId: string, fresh = false): Promise<AttemptView> {
  let id: string | null = null;
  try { id = fresh ? null : sessionStorage.getItem(requestKey(lessonId)); } catch { /* storage can be unavailable; the id then lives only for this call */ }
  if (!id) {
    id = newRequestId();
    try { sessionStorage.setItem(requestKey(lessonId), id); } catch { /* ignore */ }
  }
  const view = await api<AttemptView>(`/listening/lessons/${encodeURIComponent(lessonId)}/attempts`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_request_id: id }) });
  try { sessionStorage.removeItem(requestKey(lessonId)); } catch { /* ignore */ }
  return view;
}

/** A short-lived signed link. Absolute (object storage) links are used as given; API paths honour the configured API origin. */
export async function signedAudioUrl(attemptId: string): Promise<{ url: string; durationMs: number }> {
  const info = await api<{ url: string; duration_ms: number }>(`/listening/attempts/${encodeURIComponent(attemptId)}/audio`);
  return { url: info.url.startsWith('/') ? resolveApiUrl(info.url) : info.url, durationMs: info.duration_ms };
}

const startedKey = (attemptId: string) => `audli.listening.started.${attemptId}`;
export function rememberStarted(attemptId: string) { try { sessionStorage.setItem(startedKey(attemptId), '1'); } catch { /* per-viewer convenience only */ } }
export function wasStarted(attemptId: string): boolean { try { return sessionStorage.getItem(startedKey(attemptId)) === '1'; } catch { return false; } }
