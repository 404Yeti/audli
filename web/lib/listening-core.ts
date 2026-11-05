// Pure helpers for the 2.0 listening lesson. No imports, so Node can test them directly.
export type Cue = { index: number; start_ms: number; end_ms: number };
export type Speaker = { id: string; name: string; role: string };
export type Sentence = { index: number; speaker: string; text: string };
export type Option = { id: string; text: string };
export type Feedback = { selected_option_id: string; is_correct: boolean; correct_option_id: string; explanation: string };
export type FillResult = { answer: string; is_correct: boolean; accepted: string; explanation: string };
export type SequenceResult = { order: string[]; is_correct: boolean; correct_order: string[]; positions_correct: number; explanation: string };
export type KeyPoint = { id: string; text: string; state: 'covered' | 'partly' | 'missing' | 'unclear'; quote: string | null };
export type SpokenStatus = 'receiving' | 'pending' | 'assessed' | 'failed' | 'skipped';
export type SpokenResult = {
  status: SpokenStatus; retryable: boolean; transcript: string | null; insufficient: boolean; points: KeyPoint[] | null;
  covered: number | null; partly: number | null; total_points: number | null; failure_code: string | null;
};
export type Exercise = {
  id: string; position: number; kind: 'multiple_choice' | 'spoken' | 'fill_blank' | 'sequencing'; prompt: string; options: Option[];
  feedback: Feedback | null; fill: FillResult | null; sequence: SequenceResult | null; spoken: SpokenResult | null;
};
export type AttemptView = {
  attempt: { id: string; status: 'in_progress' | 'completed'; total: number; scored: number; correct: number | null; created_at: string; completed_at: string | null };
  lesson: { lesson_id: string; version_id: string; version_no: number; title: string; topic: string; cefr: string; listen_for: string; speakers: Speaker[]; sentences: Sentence[] };
  audio: { id: string; accent: string; duration_ms: number; cues: Cue[] };
  exercises: Exercise[];
};
export type CatalogItem = { lesson_id: string; slug: string; title: string; topic: string; cefr: string; listen_for: string; accents: string[]; duration_ms: number };
export type Phase = 'listen' | 'question' | 'result';

export const SPEEDS = [1, 1.25, 0.75] as const;
export const SKIP_MS = 10_000;

/** The sentence being spoken: the latest cue that has started. Before the first cue nothing is active. */
export function activeSentence(cues: Cue[], ms: number): number | null {
  let found: number | null = null;
  for (const cue of cues) {
    if (cue.start_ms <= ms) found = cue.index; else break;
  }
  return found;
}

export function formatTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

export const clampMs = (ms: number, duration: number) => Math.min(Math.max(0, ms), duration);

export function nextSpeed(current: number): number {
  const at = SPEEDS.findIndex(s => s === current);
  return SPEEDS[(at + 1) % SPEEDS.length];
}

export const speedLabel = (speed: number) => `${speed}×`;

/** Where the learner is, derived from the saved attempt plus the one local fact the server does not store. */
export function phaseOf(status: 'in_progress' | 'completed', started: boolean): Phase {
  if (status === 'completed') return 'result';
  return started ? 'question' : 'listen';
}

export const STEPS = ['Listen', 'Answer', 'Result'] as const;
export const stepIndex = (phase: Phase) => (phase === 'listen' ? 0 : phase === 'question' ? 1 : 2);

/** Stable per page-load id so a retried or refreshed POST creates the same attempt. */
export function newRequestId(random: () => string = () => globalThis.crypto.randomUUID()): string {
  return random();
}

/** Decorative bars with a fixed shape (not the audio's real waveform), so server and client render identically. */
export function waveformBars(count: number): number[] {
  return Array.from({ length: count }, (_, i) => Math.round(28 + 72 * Math.abs(Math.sin(i * 1.7) * 0.6 + Math.sin(i * 0.55 + 1) * 0.4)));
}

export type OptionState = 'idle' | 'selected' | 'correct' | 'incorrect' | 'dimmed';
/** After an answer: the chosen option is right or wrong, and the right one is revealed; the rest recede. */
export function optionState(optionId: string, selected: string | null, feedback: Feedback | null): OptionState {
  if (!feedback) return selected === optionId ? 'selected' : 'idle';
  if (optionId === feedback.correct_option_id) return 'correct';
  if (optionId === feedback.selected_option_id) return 'incorrect';
  return 'dimmed';
}

export function resultLine(view: AttemptView): string {
  const feedback = view.exercises[0]?.feedback;
  if (!feedback) return '';
  return feedback.is_correct ? 'Correct' : 'Not quite';
}

// ---- the four-exercise journey -------------------------------------------------------------------------------------------------

/** A question is decided once the learner has answered it. A recording still being received is not: it can be sent again. */
export function isDecided(e: Exercise): boolean {
  return !!(e.feedback || e.fill || e.sequence || (e.spoken && e.spoken.status !== 'receiving'));
}
export const firstUndecided = (exercises: Exercise[]): Exercise | undefined => exercises.find(e => !isDecided(e));
export const hasPendingAssessment = (exercises: Exercise[]): boolean => exercises.some(e => e.spoken?.status === 'pending');

/** Seconds to wait before asking again while an assessment is pending: quick at first, never hammering the server. */
export function pollDelayMs(polls: number): number {
  return Math.min(3000 + polls * 1000, 8000);
}

/** Moves one item and returns a new order; moving past either end changes nothing. */
export function moveItem<T>(order: T[], index: number, delta: -1 | 1): T[] {
  const target = index + delta;
  if (index < 0 || index >= order.length || target < 0 || target >= order.length) return order;
  const next = order.slice();
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export type Outcome = 'correct' | 'incorrect' | 'pending' | 'assessed' | 'failed' | 'skipped' | 'unanswered';
export function outcomeOf(e: Exercise): Outcome {
  if (e.feedback) return e.feedback.is_correct ? 'correct' : 'incorrect';
  if (e.fill) return e.fill.is_correct ? 'correct' : 'incorrect';
  if (e.sequence) return e.sequence.is_correct ? 'correct' : 'incorrect';
  if (e.spoken && e.spoken.status !== 'receiving') return e.spoken.status;
  return 'unanswered';
}
export const OUTCOME_LABEL: Record<Outcome, string> = {
  correct: 'Correct', incorrect: 'Not quite', pending: 'Being checked', assessed: 'Checked', failed: 'Couldn’t check', skipped: 'Skipped', unanswered: 'Not answered',
};
export const KIND_LABEL: Record<Exercise['kind'], string> = {
  multiple_choice: 'Multiple choice', spoken: 'Spoken answer', fill_blank: 'Fill in the blank', sequencing: 'Put in order',
};
export const POINT_LABEL: Record<KeyPoint['state'], string> = {
  covered: 'You said this', partly: 'Partly said', missing: 'Not mentioned', unclear: 'Not clear enough to tell',
};

/** What the debrief says about the scored questions. Only deterministic exercises count; the spoken answer is reported separately. */
export function scoreLine(view: AttemptView): string {
  const scored = view.exercises.filter(e => e.kind !== 'spoken');
  const answered = scored.filter(e => isDecided(e)).length;
  const correct = scored.filter(e => outcomeOf(e) === 'correct').length;
  return answered === scored.length ? `${correct} of ${scored.length} correct` : `${correct} of ${answered} correct so far`;
}

/** The microphone container this browser can record into, best first. */
export function pickRecorderType(isSupported: (type: string) => boolean): string | undefined {
  return ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(isSupported);
}

export const MAX_ANSWER_SECONDS = 55;
export const MIN_ANSWER_SECONDS = 2;
export function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`;
}

/** Exactly what the server accepts for the gap: trimmed, one line, at most 80 characters. */
export function cleanGap(value: string): string {
  return value.replace(/\s+/g, ' ').trim().slice(0, 80);
}
export function splitPrompt(prompt: string): [string, string] {
  const at = prompt.indexOf('___');
  return at < 0 ? [prompt, ''] : [prompt.slice(0, at), prompt.slice(at + 3)];
}
