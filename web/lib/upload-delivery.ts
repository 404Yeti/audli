/** AUD-89 P0, diagnostic only (the same build-time opt-in as turn reporting): what happened to each send of a recording.
 *
 * Answers "was it the phone, the proxy or the API?" for an upload that was slow or never arrived. One record per send:
 * outcome, status, elapsed time, size and type of the audio, how long the page had been quiet, connection hints, and whether the
 * page was hidden during the send. Numbers, enums and two short sanitized strings; never audio, text, URLs, tokens or account
 * identifiers. The upload id is random per recording (the same recording sent again keeps it), never derived from an account. */

export type DeliveryKind = 'exercise' | 'lesson' | 'plan' | 'onboarding' | 'homework';
export type DeliveryOutcome = 'ok' | 'network' | 'timeout' | 'aborted' | 'http';
export type DeliveryMime = 'webm' | 'mp4' | 'ogg' | 'wav' | 'mp3' | 'm4a' | 'other';
export type DeliveryNet = 'slow-2g' | '2g' | '3g' | '4g' | 'unknown';
export type DeliveryRecord = {
  id: string; n: number; kind: DeliveryKind; outcome: DeliveryOutcome; status: number; ms: number; bytes: number; mime: DeliveryMime;
  rec_ms?: number; since_prev_ms?: number; online?: boolean; net: DeliveryNet; rtt?: number; downlink_kbps?: number;
  hidden: number; hidden_ms: number; pagehide: boolean; errname: string; err: string;
};

/** Set by `recordingUpload` on the FormData it builds, so the id (and the recording's own length) follow the recording, not
 * the request. FormData stores a copy of the Blob, so they cannot be looked up from the file inside it. */
export const UPLOAD_ID: unique symbol = Symbol('audli.uploadId');
const RETRY_ID: unique symbol = Symbol('audli.retryUploadId');
export const UPLOAD_REC: unique symbol = Symbol('audli.recordingMs');
type UploadForm = FormData & { [UPLOAD_ID]?: string; [UPLOAD_REC]?: number; [RETRY_ID]?: boolean };

const ids = new WeakMap<Blob, string>();
const retryRecordings = new WeakSet<Blob>();
/** Once a recording enters P2, manual sends retain its ID even after the kill switch is off. */
export function retainUploadId(blob: Blob) { retryRecordings.add(blob); }
export function retryUploadIdOf(body: BodyInit | null | undefined): string | null {
  return typeof FormData !== "undefined" && body instanceof FormData && (body as UploadForm)[RETRY_ID] ? uploadIdOf(body) : null;
}
const lengths = new WeakMap<Blob, number>();
const sends = new Map<string, number>();
const MAX_QUEUE = 16;
const STORE = 'audli:deliveries';
let queue: DeliveryRecord[] | null = null;
let lastApiEnd: number | null = null;

const clamp = (value: number, max = 300000) => Math.min(max, Math.max(0, Math.round(Number.isFinite(value) ? value : 0)));

/** A random 16-hex id for this recording; sending the same Blob again returns the same id. */
export function uploadIdFor(blob: Blob): string {
  let id = ids.get(blob);
  if (!id) {
    const bytes = new Uint8Array(8);
    globalThis.crypto.getRandomValues(bytes);
    id = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    ids.set(blob, id);
  }
  return id;
}
export function rememberRecordingLength(blob: Blob, ms: number) { lengths.set(blob, clamp(ms)); }
export function tagUpload(form: FormData, blob: Blob): FormData {
  const tagged = form as UploadForm;
  tagged[UPLOAD_ID] = uploadIdFor(blob);
  if (retryRecordings.has(blob)) tagged[RETRY_ID] = true;
  const length = lengths.get(blob);
  if (length != null) tagged[UPLOAD_REC] = length;
  return form;
}

/** The upload id a recording's form was tagged with, or null for anything else. */
export function uploadIdOf(body: BodyInit | null | undefined): string | null {
  return typeof FormData !== 'undefined' && body instanceof FormData ? ((body as UploadForm)[UPLOAD_ID] ?? null) : null;
}

const KINDS: readonly [RegExp, DeliveryKind][] = [
  [/^\/api\/exercises\/[^/]+\/attempts$/, 'exercise'], [/^\/api\/lessons\/[^/]+\/attempts$/, 'lesson'],
  [/^\/api\/plan\/checkin\/attempts$/, 'plan'], [/^\/api\/onboarding\/attempts$/, 'onboarding'],
  [/^\/api\/homework\/[^/]+\/decision\/attempts$/, 'homework'], [/^\/api\/lessons\/[^/]+\/homework-review\/attempts$/, 'homework'],
];
export function deliveryKind(path: string): DeliveryKind | null { return KINDS.find(([pattern]) => pattern.test(path))?.[1] ?? null; }

export function mimeClass(type: string): DeliveryMime {
  const value = type.split(';')[0].trim().toLowerCase();
  if (value === 'audio/webm' || value === 'video/webm') return 'webm';
  if (value === 'audio/mp4') return 'mp4';
  if (value === 'audio/ogg') return 'ogg';
  if (value === 'audio/wav' || value === 'audio/x-wav') return 'wav';
  if (value === 'audio/mpeg') return 'mp3';
  if (value === 'audio/x-m4a') return 'm4a';
  return 'other';
}

/** Error name and message reduced to plain words: any word that contains a digit, slash, colon, at-sign or other symbol (a URL,
 * an id, a path, an address) is dropped whole, so none of it can get through; at most 60 characters survive. */
export function sanitizeError(error: unknown): { errname: string; err: string } {
  const name = error && typeof error === 'object' && 'name' in error ? String((error as { name: unknown }).name) : '';
  const message = error && typeof error === 'object' && 'message' in error ? String((error as { message: unknown }).message) : '';
  const words = message.split(/\s+/).filter(word => /^[A-Za-z][A-Za-z.,'-]*$/.test(word));
  return { errname: name.replace(/[^A-Za-z]/g, '').slice(0, 40), err: words.join(' ').slice(0, 60).trim() };
}

export function classifyFailure(error: unknown): DeliveryOutcome {
  const name = error && typeof error === 'object' && 'name' in error ? (error as { name: unknown }).name : '';
  if (name === 'AbortError') return 'aborted';
  if (name === 'TimeoutError') return 'timeout';
  return 'network';
}

function connectionHints(): Pick<DeliveryRecord, 'net' | 'rtt' | 'downlink_kbps'> {
  const connection = typeof navigator === 'undefined' ? undefined : (navigator as Navigator & { connection?: { effectiveType?: string; rtt?: number; downlink?: number } }).connection;
  const type = connection?.effectiveType;
  const net: DeliveryNet = type === 'slow-2g' || type === '2g' || type === '3g' || type === '4g' ? type : 'unknown';
  return {
    net,
    ...(typeof connection?.rtt === 'number' ? { rtt: clamp(connection.rtt, 10000) } : {}),
    ...(typeof connection?.downlink === 'number' ? { downlink_kbps: clamp(connection.downlink * 1000, 1000000) } : {}),
  };
}

function restore(): DeliveryRecord[] {
  if (queue) return queue;
  queue = [];
  try {
    const saved = typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem(STORE);
    if (saved) { const parsed = JSON.parse(saved); if (Array.isArray(parsed)) queue = parsed.slice(-MAX_QUEUE); }
  } catch { /* Optional memory; the queue still works in memory. */ }
  return queue;
}
function persist() {
  try { if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(STORE, JSON.stringify(restore())); } catch { /* Optional. */ }
}
export function pushDelivery(record: DeliveryRecord) {
  const items = restore();
  items.push(record);
  while (items.length > MAX_QUEUE) items.shift();
  persist();
}
/** The oldest records, removed from the queue (and from sessionStorage); `requeueDeliveries` puts them back if the report fails. */
export function takeDeliveries(max = 8): DeliveryRecord[] {
  const items = restore();
  const taken = items.splice(0, max);
  if (taken.length) persist();
  return taken;
}
export function requeueDeliveries(records: DeliveryRecord[]) {
  const items = restore();
  items.unshift(...records);
  while (items.length > MAX_QUEUE) items.shift();
  persist();
}
/** Tests only. */
export function resetDeliveries() { queue = []; sends.clear(); lastApiEnd = null; try { if (typeof sessionStorage !== 'undefined') sessionStorage.removeItem(STORE); } catch { /* Optional. */ } }

/** Every finished API request marks the page as recently active, so the next upload can say how long the page had been quiet. */
export function markApiActivity(now = performance.now()) { lastApiEnd = now; }

export type DeliveryTracker = { id: string; done(status: number): void; fail(error: unknown): void };

/** Start tracking one send. Returns null for anything that is not a recording upload (so nothing else is observed). */
export function startDelivery(path: string, body: BodyInit | null | undefined, now = performance.now()): DeliveryTracker | null {
  try { return trackDelivery(path, body, now); } catch { return null; }   // diagnostics never affect an upload
}

function trackDelivery(path: string, body: BodyInit | null | undefined, now: number): DeliveryTracker | null {
  const kind = deliveryKind(path);
  if (!kind || typeof FormData === 'undefined' || !(body instanceof FormData)) return null;
  const id = (body as UploadForm)[UPLOAD_ID];
  const audio = body.get('audio');
  if (!id || !audio || typeof audio === 'string') return null;
  const n = Math.min(9, (sends.get(id) ?? 0) + 1);
  if (sends.size > 32 && !sends.has(id)) sends.delete(sends.keys().next().value as string);
  sends.set(id, n);
  const sincePrev = lastApiEnd == null ? undefined : clamp(now - lastApiEnd);
  const blob = audio as Blob;
  let hidden = 0, hiddenMs = 0, hiddenAt: number | null = null, pagehide = false, finished = false;
  const onVisibility = () => {
    if (document.visibilityState === 'hidden') { hidden++; hiddenAt = performance.now(); }
    else if (hiddenAt != null) { hiddenMs += performance.now() - hiddenAt; hiddenAt = null; }
  };
  const onPageHide = () => { pagehide = true; };
  const doc = typeof document !== 'undefined' && typeof document.addEventListener === 'function' ? document : null;
  const win = typeof window !== 'undefined' && typeof window.addEventListener === 'function' ? window : null;
  doc?.addEventListener('visibilitychange', onVisibility);
  win?.addEventListener('pagehide', onPageHide);
  const finish = (outcome: DeliveryOutcome, status: number, error?: unknown) => {
    if (finished) return;
    finished = true;
    try {
      doc?.removeEventListener('visibilitychange', onVisibility);
      win?.removeEventListener('pagehide', onPageHide);
      if (hiddenAt != null) hiddenMs += performance.now() - hiddenAt;
      const rec = (body as UploadForm)[UPLOAD_REC];
      pushDelivery({
        id, n, kind, outcome, status: Math.min(599, Math.max(0, Math.round(status))), ms: clamp(performance.now() - now),
        bytes: Math.min(16 * 1024 * 1024, Math.max(0, blob.size)), mime: mimeClass(blob.type),
        ...(rec == null ? {} : { rec_ms: rec }), ...(sincePrev == null ? {} : { since_prev_ms: sincePrev }),
        ...(typeof navigator !== 'undefined' && typeof navigator.onLine === 'boolean' ? { online: navigator.onLine } : {}),
        ...connectionHints(), hidden: Math.min(20, hidden), hidden_ms: clamp(hiddenMs), pagehide,
        ...(error === undefined ? { errname: '', err: '' } : sanitizeError(error)),
      });
    } catch { /* Telemetry never affects an upload. */ }
  };
  return { id, done: status => finish(status >= 200 && status < 300 ? 'ok' : 'http', status), fail: error => finish(classifyFailure(error), 0, error) };
}
