// Pure rules for the AI Lesson Generator screens (Figma 04 / 04b / 04c and edge states E01-E04). Nothing here talks to the network.
import { AVAILABLE_ACCENTS, accentLabel } from './home-core.ts';

export const FORMATS = ['story', 'dialogue', 'conversation'] as const;
export type Format = (typeof FORMATS)[number];
export const LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'] as const;
export const LEVEL_HELP: Record<string, string> = {
  A1: 'A1 · Beginner — you understand simple, slow phrases about familiar things.',
  A2: 'A2 · Elementary — you follow short, clear everyday exchanges.',
  B1: 'B1 · Intermediate — you can follow everyday conversations at a natural pace.',
  B2: 'B2 · Upper-intermediate — you follow longer talk and most detail.',
  C1: 'C1 · Advanced — you follow fast, complex speech with little effort.',
  C2: 'C2 · Proficient — you understand virtually everything you hear.' };
export const FORMAT_LABEL: Record<Format, string> = { story: 'Story', dialogue: 'Dialogue', conversation: 'Conversation' };
export const VOICES_LABEL: Record<Format, string> = { story: '1 voice', dialogue: '2 voices', conversation: '2 voices' };
export const ACCENT_NOTE: Record<string, { code: string; place: string; feel: string }> = {
  british: { code: 'UK', place: 'British · London', feel: 'Clear, mid-paced' },
  american: { code: 'US', place: 'American · General', feel: 'Relaxed, slightly faster' },
  australian: { code: 'AU', place: 'Australian · General', feel: 'Warm, relaxed' } };
export const EXAMPLES = ['Job interview', 'Doctor’s appointment', 'Airport delay', 'Team stand-up'] as const;
export const MAX_TEXT = 280;

export type Spec = { topic: string; cefr: string; accent: string; minutes: number; format: Format; scenario: string };
export const DEFAULT_SPEC: Spec = { topic: '', cefr: 'B1', accent: 'american', minutes: 2, format: 'dialogue', scenario: '' };

/** The lengths the learner may pick: only what the server will generate today (minutes <= max). */
export const lengthChoices = (maxMinutes: number): number[] => Array.from({ length: Math.max(1, Math.min(maxMinutes, 5)) }, (_, i) => i + 1);

export type Phase = 'queued' | 'script' | 'audio' | 'transcript_sync' | 'exercises' | 'ready' | 'failed' | 'rejected' | 'unavailable' | 'interpreted' | 'needs_input';
export type GenerationView = {
  id: string; kind: 'interpret' | 'generate'; phase: Phase; spec: Spec | null; notes: string[]; question: string | null; title: string | null;
  lesson_id: string | null; failure: string | null; unavailable_reason: string | null; updated_at: string | null;
};
export type Availability = { available: boolean; reason: string | null; max_minutes: number; accents: string[]; per_learner_daily: number };

/** What the server said about the interpretation, in the learner's words. */
export function noteLines(notes: string[], maxMinutes: number): string[] {
  const out: string[] = [];
  for (const note of notes) {
    if (note.startsWith('minutes_limited')) out.push(`Lessons are up to ${maxMinutes} minute${maxMinutes === 1 ? '' : 's'} for now, so we shortened it.`);
    else if (note === 'accent_unavailable') out.push('That accent isn’t available yet, so we picked American. You can change it.');
  }
  return out;
}
export const defaultedFields = (notes: string[]): string[] => notes.filter(n => n.startsWith('defaulted:')).map(n => n.slice('defaulted:'.length));

/** Steps shown while a lesson is built (Figma 04b). `done`/`active`/`todo` follows the phase the server reports; nothing is claimed that has not happened. */
export const STEPS = [
  { key: 'script', label: 'Writing the script', done: 'Script written' },
  { key: 'check', label: 'Checking questions against the script', done: 'Checked against the script' },
  { key: 'audio', label: 'Recording audio', done: 'Audio recorded' },
  { key: 'transcript_sync', label: 'Syncing the transcript', done: 'Transcript synced' },
  { key: 'exercises', label: 'Writing 4 exercises & answer keys', done: '4 exercises ready' },
] as const;
const ORDER: Record<string, number> = { queued: -1, script: 0, audio: 2, transcript_sync: 3, exercises: 4, ready: 5 };
export type StepState = 'done' | 'active' | 'todo';
export function stepStates(phase: Phase): StepState[] {
  const at = ORDER[phase] ?? -1;
  return STEPS.map((_, i) => (at >= 5 || i < at ? 'done' : i === at ? 'active' : 'todo'));
}
/** The step 'Checking…' is part of the script phase on the server: it is shown done as soon as audio starts. */
export const SLOW_AFTER_MS = 60_000;
export const POLL_MS = 2000;
export const isLive = (phase: Phase): boolean => ['queued', 'script', 'audio', 'transcript_sync', 'exercises'].includes(phase);
export const specLine = (s: Spec, maxLabel = true): string => `${s.cefr} · ${accentLabel(s.accent)} · ${FORMAT_LABEL[s.format]} · about ${s.minutes} min`;

/** Why creation is unavailable, in the learner's words, and what they can do. */
export function unavailableCopy(reason: string | null, perDay: number): { title: string; text: string } {
  switch (reason) {
    case 'daily_limit': return { title: 'That’s today’s lessons made', text: `You can create ${perDay} lessons a day. Your new lessons are in your library — come back tomorrow for more.` };
    case 'budget': case 'credits': return { title: 'Lesson creation is full for now', text: 'Audli has made as many lessons as it can for now. Browse the library, and try again a little later.' };
    case 'paused': return { title: 'Lesson creation is paused', text: 'We’ve paused lesson creation for a little while. Your library is still here.' };
    default: return { title: 'Lesson creation isn’t available yet', text: 'You can still browse and play every lesson in the library.' };
  }
}
export const canCreate = (spec: Spec, text: string): boolean => (spec.topic.trim().length >= 3 || text.trim().length >= 3) && text.length <= MAX_TEXT && AVAILABLE_ACCENTS.includes(spec.accent as never);
