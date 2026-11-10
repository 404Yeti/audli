// Pure helpers for the Daily Challenge card. No imports, so Node can test them directly.
import type { CatalogItem } from './listening-core.ts';

export type DailyStreak = { current: number; longest: number; days_completed: number };
export type DailyChallenge = {
  status: 'ready' | 'unavailable'; date: string; next_at: string; lesson: CatalogItem | null; category?: string | null;
  completed_today: boolean; streak: DailyStreak;
};
export type DailyView = 'unavailable' | 'start' | 'resume' | 'completed';

/** What the card shows. Completed wins over anything else; an unavailable day never shows a lesson. */
export function dailyView(d: Pick<DailyChallenge, 'status' | 'lesson' | 'completed_today'>): DailyView {
  if (d.status !== 'ready' || !d.lesson) return 'unavailable';
  if (d.completed_today || d.lesson.status === 'completed') return 'completed';
  return d.lesson.status === 'in_progress' ? 'resume' : 'start';
}

export const dailyButton = (v: DailyView): string => (v === 'resume' ? 'Resume challenge' : v === 'completed' ? 'Review results' : 'Start challenge');

/** "3 h 20 min" until the next challenge; never negative. */
export function untilNext(nextAt: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.ceil((Date.parse(nextAt) - now) / 60_000));
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return h ? `${h} h ${m ? `${m} min` : ''}`.trim() : `${m} min`;
}

export function streakLine(s: DailyStreak): string {
  if (s.current <= 0) return s.days_completed ? `Best streak ${s.longest} day${s.longest === 1 ? '' : 's'}` : 'Finish today’s challenge to start a streak';
  return `${s.current}-day streak${s.longest > s.current ? ` · best ${s.longest}` : ''}`;
}

/** Where "Start/Resume" goes: the lesson page resumes an open attempt by itself. */
export const dailyHref = (d: Pick<DailyChallenge, 'lesson'>): string | null => (d.lesson ? `/listening/${d.lesson.lesson_id}` : null);
