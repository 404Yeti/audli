import type { Recognition } from './hands-free.ts';
import type { LessonCheckpoint } from './lesson-lifecycle.ts';

export type Known = { lessonId: string; revision: number; phase: LessonCheckpoint['phase']; pending: Recognition | null };
export type CheckinDecision =
  | { kind: 'adopt'; checkpoint: LessonCheckpoint }
  | { kind: 'moved'; checkpoint: LessonCheckpoint }
  | { kind: 'resend'; revision: number }
  | { kind: 'stop' };

export function sameRecognition(a: Recognition | null | undefined, b: Recognition | null | undefined): boolean {
  if (!a || !b) return !a && !b;
  const notes = (value: Recognition) => (value.uncertainty ?? []).join('\u0000');
  return a.text === b.text && (a.confidence ?? null) === (b.confidence ?? null) && notes(a) === notes(b);
}

export function decideCheckin(known: Known, server: LessonCheckpoint): CheckinDecision {
  if (server.id === known.lessonId && server.status === 'active' && server.phase === known.phase
    && server.revision === known.revision && sameRecognition(server.pending, known.pending)) {
    return { kind: 'resend', revision: known.revision };
  }
  return { kind: 'stop' };
}
