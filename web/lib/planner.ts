import type { Recognition } from './hands-free';
export type Plan = {
  revision: number; completed_lessons: number; block_progress: number; focus: string;
  readiness: 'onboarding' | 'resume_lesson' | 'ready';
  upcoming: { number: number; title: string; topic: string; objective: string; focus: string;
    difficulty: { speech_rate: number; duration_seconds: number; vocabulary_level: string; information_density: number }; accent_focus: string | null }[];
  completed: { id: string; title: string; focus: string; completed_at: string }[];
  checkin: { after_lesson: number | null; status: 'due' | 'answered' | 'skipped' | 'not_due'; available: boolean; prompt: string | null; pending: Recognition | null };
};
export function planStatus(plan: Plan): string {
  if (plan.readiness === 'onboarding') return 'Complete onboarding to begin.';
  if (plan.readiness === 'resume_lesson') return 'Resume your current lesson first.';
  return 'Ready when you are.';
}
export function difficultyDescription(difficulty: Plan['upcoming'][number]['difficulty']): string {
  const levels: Record<string, string> = { A1: 'Beginner', A2: 'Elementary', B1: 'Intermediate', B2: 'Upper intermediate', C1: 'Advanced', C2: 'Proficient' };
  const length = difficulty.duration_seconds <= 60 ? 'Short listening practice' : difficulty.duration_seconds <= 120 ? 'A little longer listening practice' : 'Extended listening practice';
  const pace = difficulty.speech_rate < .95 ? 'Slower pace' : difficulty.speech_rate <= 1.05 ? 'Natural pace' : 'Brisk pace';
  return `${levels[difficulty.vocabulary_level] ?? 'Practice at your level'} · ${length} · ${pace}`;
}
