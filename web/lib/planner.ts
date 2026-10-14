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
  return 'Ready to start. Content and audio are prepared when you begin.';
}
export function checkinStatus(plan: Plan): string {
  if (plan.checkin.status === 'due') return plan.checkin.available ? 'An optional spoken check-in is ready.' : 'Your optional check-in will be ready between lessons.';
  if (plan.checkin.status === 'answered') return 'Check-in saved. Your next lessons include your feedback.';
  if (plan.checkin.status === 'skipped') return 'Check-in skipped. Your plan uses your listening evidence.';
  return `Next optional check-in in ${3 - plan.block_progress} completed lessons.`;
}
