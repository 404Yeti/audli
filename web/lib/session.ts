import type { FlowPhase } from './conversation';

export function shouldAutoRecord(phase: FlowPhase, cueKey: string | null, previousCue: string | null, hasResult: boolean): boolean {
  return !hasResult && !!cueKey && cueKey !== previousCue && (phase === 'AWAITING_SUMMARY' || phase === 'AWAITING_FOLLOWUP');
}
export function feedbackOutcome(insufficient: readonly string[] | undefined, misunderstood: readonly string[]): 'Gap' | 'Misunderstood' | 'Understood' {
  return misunderstood.length ? 'Misunderstood' : insufficient?.length ? 'Gap' : 'Understood';
}
export async function microphonePermission(devices: Pick<MediaDevices, 'getUserMedia'> | undefined): Promise<void> {
  if (!devices?.getUserMedia) throw new Error('Microphone access requires localhost or HTTPS and a supported browser.');
  try {
    const stream = await devices.getUserMedia({ audio: true });
    stream.getTracks().forEach(track => track.stop());
  } catch (error) {
    throw microphoneError(error);
  }
}

/** Distinguish permission, hardware and capture failures without exposing device details. */
export function microphoneError(error: unknown): Error {
  const name = error instanceof Error ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return new Error('Audli needs microphone access to hear your answer. Please enable it and try again.');
  if (name === 'NotFoundError') return new Error('No microphone is available. Connect a microphone and try again.');
  if (name === 'NotReadableError') return new Error('Your microphone could not be opened. Check whether another app is using it and try again.');
  return new Error('Microphone recording could not start. Check your microphone and try again.');
}
