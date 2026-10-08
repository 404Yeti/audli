/** Development-only, fixed-stage timings. Never accept learner text or credentials. */
export type TurnStage = 'silence_detection' | 'recording_finalization' | 'upload_transcription' | 'assessment' | 'profile_extraction' | 'tts_readiness' | 'playback_start' | 'turn_to_playback';
export function turnTiming(stage: TurnStage, startedAt: number) {
  if (process.env.NODE_ENV === 'development') console.debug('[Audli turn]', { stage, elapsedMs: Math.round(performance.now() - startedAt) });
}
export async function measureTurn<T>(stage: TurnStage, work: () => Promise<T>): Promise<T> {
  const startedAt = performance.now();
  try { return await work(); } finally { turnTiming(stage, startedAt); }
}
