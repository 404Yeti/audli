/** Fixed-stage timings, local development or explicit opt-in. No learner content. */
export type TurnStage = 'silence_detection' | 'recording_finalization' | 'upload_transcription' | 'assessment' | 'checkin_response' | 'profile_extraction' | 'tts_readiness' | 'playback_start' | 'turn_to_playback' | 'request_submission' | 'response_headers' | 'first_audio_byte' | 'audio_body_ready' | 'acknowledgment_playback' | 'retry_playback' | 'acknowledgment_duration' | 'coaching_readiness' | 'coaching_wait_after_ack' | 'longest_pause' | 'speech_span';
/** Opt-in batching of numeric stage timings. Integers only; no content, IDs or timestamps. */
export type TurnSink = (stages: Partial<Record<TurnStage, number>>, trace?: TurnTrace | null) => void;
const FLUSH_ON: readonly TurnStage[] = ['turn_to_playback', 'retry_playback'];

/** AUD-85, build-time opt-in (NEXT_PUBLIC_AUDLI_TURN_TRACE=1), diagnostic: a random per-turn id sent as `X-Audli-Turn`
 * plus, per request of the turn, how long the browser waited for its access token and for response headers.
 * The id is random per turn, never derived from the account; integers only. */
export type TraceKind = 'upload' | 'assess' | 'ack' | 'coach';
export type RequestSpan = { kind: TraceKind; start: number; token: number; ttfb: number };
export type TurnTrace = { turn: string; requests: RequestSpan[] };
type ActiveTrace = { turn: string; startedAt: number; requests: RequestSpan[] };
let active: ActiveTrace | null = null;
const MAX_REQUESTS = 16;
const traceEnabled = () => process.env.NEXT_PUBLIC_AUDLI_TURN_TRACE === '1';
const clampMs = (value: number) => Math.min(300000, Math.max(0, Math.round(value)));
/** Called when the end of speech is detected; the turn's requests are tied together from here to the coaching playback. */
export function beginTurnTrace(speechEndAt: number) {
  if (!traceEnabled()) return;
  const bytes = new Uint8Array(6);
  globalThis.crypto.getRandomValues(bytes);
  active = { turn: Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join(''), startedAt: speechEndAt, requests: [] };
}
export function activeTurnId(): string | null { return active?.turn ?? null; }
const KINDS: readonly [RegExp, TraceKind][] = [
  [/^\/api\/(?:exercises|lessons)\/[^/]+\/attempts$/, 'upload'], [/^\/api\/attempts\/[^/]+\/assess$/, 'assess'],
  [/^\/api\/recognition\/acknowledgment-audio$/, 'ack'], [/^\/api\/exercises\/[^/]+\/coach-audio$/, 'coach'],
];
export function traceKind(path: string): TraceKind | null { return KINDS.find(([pattern]) => pattern.test(path))?.[1] ?? null; }
/** `requestStart` is when the request began (before the token wait); times are relative to the detected end of speech. */
export function recordRequestSpan(kind: TraceKind, requestStart: number, tokenMs: number, ttfbMs: number) {
  if (!active || active.requests.length >= MAX_REQUESTS) return;
  active.requests.push({ kind, start: clampMs(requestStart - active.startedAt), token: clampMs(tokenMs), ttfb: clampMs(ttfbMs) });
}
/** Hand over the spans collected so far; the turn itself ends only with the meaningful playback. */
export function takeTurnTrace(final: boolean): TurnTrace | null {
  if (!active) return null;
  const trace = { turn: active.turn, requests: active.requests };
  active.requests = [];
  if (final) active = null;
  return trace.requests.length ? trace : null;
}

export function createTurnReporter(send: TurnSink, maxStages = 20) {
  let batch: Partial<Record<TurnStage, number>> = {};
  const flush = () => {
    const stages = batch; batch = {};
    if (Object.keys(stages).length) { try { send(stages, takeTurnTrace('turn_to_playback' in stages)); } catch { /* Telemetry never affects a lesson. */ } }
  };
  return {
    add(stage: TurnStage, elapsedMs: number) {
      if (!Number.isFinite(elapsedMs)) return;
      batch[stage] = Math.min(300000, Math.max(0, Math.round(elapsedMs)));
      if (FLUSH_ON.includes(stage) || Object.keys(batch).length >= maxStages) flush();
    },
    flush,
  };
}
let reporter: ReturnType<typeof createTurnReporter> | null = null;
export function installTurnSink(sink: TurnSink | null) { reporter = sink ? createTurnReporter(sink) : null; }
export function turnTiming(stage: TurnStage, startedAt: number) {
  reporter?.add(stage, performance.now() - startedAt);
  if (process.env.NODE_ENV === 'development' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY === '1') console.debug('[Audli turn]', { stage, elapsedMs: Math.round(performance.now() - startedAt) });
}

/** AUD-82: report a measured duration (not elapsed-since) such as the longest pause inside a spoken answer. Numbers only. */
export function turnValue(stage: 'longest_pause' | 'speech_span', ms: number) {
  reporter?.add(stage, ms);
  if (process.env.NODE_ENV === 'development' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY === '1') console.debug('[Audli turn]', { stage, elapsedMs: Math.round(ms) });
}

/** Last detected voice is an estimate, not an externally measured speech boundary.
 * Optional/retry audio never consumes the meaningful-response timer. */
export function speechLatency(clock: () => number = () => performance.now()) {
  let speechEnd: number | null = null;
  return {
    begin(lastDetectedVoice: number) { speechEnd = lastDetectedVoice; },
    reset() { speechEnd = null; },
    playback(kind: 'meaningful' | 'acknowledgment' | 'retry') {
      if (speechEnd == null) return null;
      const elapsedMs = Math.max(0, clock() - speechEnd);
      if (kind === 'meaningful') speechEnd = null;
      return { kind, elapsedMs, boundary: 'last_detected_voice' as const };
    },
  };
}

/** Read the complete validated body as before, distinguishing headers from bytes.
 * An empty or failed body cannot manufacture a first-byte measurement. */
export async function measuredAudioBody(response: Response): Promise<Blob> {
  const started = performance.now();
  if (!response.body) return response.blob();
  const reader = response.body.getReader();
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let first = true;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value.byteLength && first) { turnTiming('first_audio_byte', started); first = false; }
      chunks.push(value);
    }
    turnTiming('audio_body_ready', started);
    return new Blob(chunks, { type: response.headers.get('Content-Type') ?? '' });
  } finally { reader.releaseLock(); }
}
export async function measureTurn<T>(stage: TurnStage, work: () => Promise<T>): Promise<T> {
  const startedAt = performance.now();
  try { return await work(); } finally { turnTiming(stage, startedAt); }
}
