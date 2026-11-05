// Pure helpers for the 2.0 lesson Success screen (Figma D13/M13). Everything shown comes from the persisted attempt and the learner's own progress.
// Deliberately absent from the design: a "78% → 80%" listening score and a CEFR change. Audli has no such measure, so none is shown.
import { KIND_LABEL, isDecided, outcomeOf, type AttemptView, type CatalogItem, type Progress } from './listening-core.ts';

export type SuccessState =
  | { kind: 'incomplete'; answered: number; total: number }                 // the attempt exists but is not finished: send the learner back to it
  | { kind: 'complete'; summary: SuccessSummary };

export type SuccessSummary = {
  lessonId: string; attemptId: string; title: string; cefr: string; accent: string;
  correct: number; scored: number;                // deterministic questions only
  mistakes: number;                                // scored questions answered wrongly
  spoken: 'assessed' | 'pending' | 'failed' | 'skipped' | 'none';   // reported in words, never as a number
  practised: string[];                             // what the learner actually did, by exercise type
  lessonsFinished: number | null; lessonsAvailable: number | null;
};

export function successState(view: AttemptView, progress: Progress | null): SuccessState {
  const scoredQs = view.exercises.filter(e => e.kind !== 'spoken');
  const decided = view.exercises.filter(isDecided).length;
  if (view.attempt.status !== 'completed') return { kind: 'incomplete', answered: decided, total: view.exercises.length };
  const spokenQ = view.exercises.find(e => e.kind === 'spoken');
  const spokenStatus = spokenQ?.spoken?.status;
  const spoken: SuccessSummary['spoken'] = !spokenQ || !spokenStatus || spokenStatus === 'receiving' ? 'none'
    : spokenStatus === 'assessed' ? 'assessed' : spokenStatus === 'pending' ? 'pending' : spokenStatus === 'failed' ? 'failed' : 'skipped';
  const practised = view.exercises.filter(e => {
    const outcome = outcomeOf(e);
    return e.kind === 'spoken' ? outcome === 'assessed' : outcome === 'correct' || outcome === 'incorrect';
  }).map(e => KIND_LABEL[e.kind]);
  return { kind: 'complete', summary: {
    lessonId: view.lesson.lesson_id, attemptId: view.attempt.id, title: view.lesson.title, cefr: view.lesson.cefr, accent: view.audio.accent,
    correct: scoredQs.filter(e => outcomeOf(e) === 'correct').length, scored: scoredQs.length,
    mistakes: scoredQs.filter(e => outcomeOf(e) === 'incorrect').length, spoken, practised,
    lessonsFinished: progress?.lessons_completed ?? null, lessonsAvailable: progress?.lessons_available ?? null,
  } };
}

/** "Up next": a lesson the learner has begun, else one they have not, never the one just finished. Null when nothing is left. */
export function pickNext(catalog: CatalogItem[], lessonId: string): CatalogItem | null {
  const others = catalog.filter(c => c.lesson_id !== lessonId && c.status !== 'completed');
  return others.find(c => c.status === 'in_progress') ?? others.find(c => c.status === 'new') ?? null;
}

export const SPOKEN_SENTENCE: Record<SuccessSummary['spoken'], string | null> = {
  assessed: 'Your spoken answer was checked. It is feedback, not part of your score.',
  pending: 'Your spoken answer is still being checked. The result will appear in your summary.',
  failed: 'We couldn’t check your spoken answer. You can try again from your summary.',
  skipped: 'You skipped the spoken question.',
  none: null,
};

export const successPath = (lessonId: string, attemptId: string) => `/listening/${encodeURIComponent(lessonId)}/success?attempt=${encodeURIComponent(attemptId)}`;
/** Only a plain id is accepted from the address bar; anything else is treated as "no attempt". */
export const attemptParam = (value: string | null | undefined): string | null => (value && /^[A-Za-z0-9-]{8,64}$/.test(value) ? value : null);
