export const SESSION_DURATION_MS = 10 * 60 * 1000;
export function minutesRemaining(startedAt: number, now: number, duration = SESSION_DURATION_MS): number {
  return Math.max(1, Math.ceil((duration - Math.max(0, now - startedAt)) / 60000));
}
export type Recognition = { text: string; confidence?: number | null; uncertainty: string[] };
export function reliableRecognition(value: Recognition, minimum = .65): boolean {
  return !!value.text.trim() && value.confidence != null && value.confidence >= minimum && !value.uncertainty.length;
}

/** Local turn detection only. ASR and listening evidence remain server/provider concerns. */
export function turnDetector(startedAt: number) {
  let voicedMs = 0, lastVoice = startedAt, previous = startedAt;
  return (now: number, rms: number): { activity: boolean; end: boolean } => {
    const activity = rms >= .015;
    if (activity) { voicedMs += Math.min(100, Math.max(0, now - previous)); lastVoice = now; }
    previous = now;
    return { activity, end: now - startedAt >= 119000
      || (voicedMs >= 300 && now - startedAt >= 2000 && now - lastVoice >= 1800)
      || (voicedMs < 300 && now - startedAt >= 15000) };
  };
}
