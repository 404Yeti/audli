import type { Recognition } from './hands-free';

/** Server checkpoint only. The browser orchestrates media, never phase policy. */
export type LessonCheckpoint = {
  id: string; revision: number; status: 'active' | 'paused' | 'completed';
  phase: 'WELCOME' | 'WELCOME_ACK' | 'REVIEW' | 'REVIEW_ACK' | 'TRANSITION' | 'EXERCISE' | 'CLOSING' | 'COMPLETED';
  prompt: string | null; pending: Recognition | null;
  exercise: { id: string; audio_url: string; completed_attempt_id: string | null; topic?: string | null } | null;
  introduction_pending?: boolean;
  introduction_prompt?: string | null;
  elapsed_seconds: number; remaining_seconds: number; server_time: string;
  focus: string; exercises_completed: number; strength: string | null;
  improvement_focus: string | null; encouragement: string;
};
