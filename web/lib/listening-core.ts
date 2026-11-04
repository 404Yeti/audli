// Pure helpers for the 2.0 listening lesson. No imports, so Node can test them directly.
export type Cue = { index: number; start_ms: number; end_ms: number };
export type Speaker = { id: string; name: string; role: string };
export type Sentence = { index: number; speaker: string; text: string };
export type Option = { id: string; text: string };
export type Feedback = { selected_option_id: string; is_correct: boolean; correct_option_id: string; explanation: string };
export type Exercise = { id: string; position: number; kind: string; prompt: string; options: Option[]; feedback: Feedback | null };
export type AttemptView = {
  attempt: { id: string; status: 'in_progress' | 'completed'; total: number; correct: number | null; created_at: string; completed_at: string | null };
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
