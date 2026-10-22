export type ReviewLesson = {
  id: string; kind: 'lesson' | 'earlier_practice'; completed: boolean; completed_at: string | null;
  title: string; topics: string[]; levels: string[]; takeaway: string; assessed_passages: number;
};
export type ReviewUnit = { dimension: string; index: number; status: 'demonstrated' | 'partial' | 'misunderstood' | 'not_demonstrated'; meaning: string; evidence: string | null };
export type ImprovementNote = { kind: 'grammar' | 'vocabulary'; before: string; after: string; why: string };
export type DiffSegment = { op: 'equal' | 'delete' | 'insert'; text: string };
/** Advisory language coaching (AUD-77). Absent unless the server feature is on; never affects progress. */
export type ImprovementState = {
  state: 'unavailable' | 'available' | 'pending' | 'ready' | 'rejected'; reason: string | null; improved_text: string | null;
  unchanged: boolean; notes: ImprovementNote[]; diff: DiffSegment[];
};
export type SavedAnswer = { attempt_id: string; question: string | null; text: string; uncertain: boolean };
export type PassageReport = {
  exercise_id: string; title: string; topic: string | null; level: string | null; accent: string | null;
  accent_fallback: boolean; units: ReviewUnit[]; answers?: SavedAnswer[]; /* absent from an API that predates AUD-77 */ assessment_available: boolean; saved_feedback: string | null;
  next_focus: string; adaptation_reason: string | null; transcript_url: string; audio_url: string | null;
  improvement?: ImprovementState | null;
};
export type LessonReport = { lesson: ReviewLesson; passages: PassageReport[] };
export type ListeningPattern = {
  dimension: string; title: string; label: 'Consistent strength' | 'Developing' | 'Needs more practice' | 'Not enough evidence';
  observations: number; practice_days: number; first_date: string | null; last_date: string | null; context: string | null;
};
export type ReviewDashboard = {
  summary: { completed_lessons: number; structured_lessons: number; earlier_practice: number; practice_days: number;
    undated_lessons: number; timezone_used: string; timezone_fallback: boolean; current_practice_level: string | null };
  insight: string; patterns_version: string; patterns: ListeningPattern[]; lessons: ReviewLesson[];
  assessed_practice: ReviewLesson[]; next_cursor: string | null;
};
export function reviewDate(value: string | null, zone: string) {
  if (!value) return 'Date unavailable';
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: zone }).format(new Date(value));
}
export function browserTimezone(): string | null {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
}
