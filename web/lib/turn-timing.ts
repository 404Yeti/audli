/** Fixed-stage timings, local development or explicit opt-in. No learner content. */
export type TurnStage = 'silence_detection' | 'recording_finalization' | 'upload_transcription' | 'assessment' | 'checkin_response' | 'profile_extraction' | 'tts_readiness' | 'playback_start' | 'turn_to_playback' | 'request_submission' | 'response_headers' | 'first_audio_byte' | 'audio_body_ready' | 'acknowledgment_playback' | 'retry_playback' | 'acknowledgment_duration' | 'coaching_readiness' | 'coaching_wait_after_ack';
export function turnTiming(stage: TurnStage, startedAt: number) {
  if (process.env.NODE_ENV === 'development' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_TELEMETRY === '1') console.debug('[Audli turn]', { stage, elapsedMs: Math.round(performance.now() - startedAt) });
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
