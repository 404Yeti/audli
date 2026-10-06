import type { FlowPhase } from './conversation';

export function shouldAutoRecord(phase: FlowPhase, cueKey: string | null, previousCue: string | null, hasResult: boolean): boolean {
  return !hasResult && !!cueKey && cueKey !== previousCue && (phase === 'AWAITING_SUMMARY' || phase === 'AWAITING_FOLLOWUP');
}
export function feedbackOutcome(insufficient: readonly string[] | undefined, misunderstood: readonly string[]): 'Gap' | 'Misunderstood' | 'Understood' {
  return misunderstood.length ? 'Misunderstood' : insufficient?.length ? 'Gap' : 'Understood';
}
export async function microphonePermission(devices: Pick<MediaDevices, 'getUserMedia'> | undefined): Promise<void> {
  if (!devices?.getUserMedia) throw new Error('Microphone access requires localhost or HTTPS. You can use a spoken recording in the lesson.');
  try {
    const stream = await devices.getUserMedia({ audio: true });
    stream.getTracks().forEach(track => track.stop());
  } catch {
    throw new Error('Microphone access was denied or unavailable. Allow microphone access in your browser and retry, or continue with a spoken audio file.');
  }
}
