/** AUD-89 P2: what the learner is told while a recording is being delivered. Text only (no audio, so no text-to-speech call),
 * announced politely by the component; the wording is fixed here so tests and the design document can quote it. */
import type { DeliveryState } from './upload-sender.ts';

export type Notice =
  | { kind: 'sending' | 'retrying' | 'moved'; text: string }
  | { kind: 'failed'; text: string; ambiguous?: boolean }          // Send again / Record again
  | { kind: 'recordAgain'; text: string }     // after Record again: the question stays as text, no audio is replayed unasked
  | { kind: 'unsent'; text: string };         // after a reload: an upload was in flight and the recording is gone; Continue

export const NOTICE_TEXT = {
  sending: 'Sending your answer…',
  retrying: (next: number, of: number) => `Trying to send again (${next} of ${of})…`,
  failed: "We couldn't confirm your answer was received. Your recording is available while this page stays open.",
  recordAgain: "Okay, let's try that again.",
  unsent: "Your last send was interrupted. The recording is no longer available. Resume to check your saved lesson.",
  moved: "Your saved lesson changed. Resume to continue from its current checkpoint.",
} as const;

/** The notice for one delivery state; a send that is going normally shows nothing (no flash on every answer). */
export function noticeFor(state: DeliveryState): Notice | null {
  switch (state.kind) {
    case 'sending': return state.slow ? { kind: 'sending', text: NOTICE_TEXT.sending } : null;
    case 'retrying': return { kind: 'retrying', text: NOTICE_TEXT.retrying(state.next, state.of) };
    case 'failed': return { kind: 'failed', text: NOTICE_TEXT.failed, ...(state.reason === 'ambiguous' ? { ambiguous: true } : {}) };
    case 'moved': return { kind: 'moved', text: NOTICE_TEXT.moved };
    case 'delivered': return null;
  }
}
