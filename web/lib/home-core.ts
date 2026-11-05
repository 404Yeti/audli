// Pure rules for the Audli 2.0 home, Discover and Progress screens. Nothing here claims more than the data says: no streaks, no mastery, no levels earned.
import type { CatalogItem, Progress, RecentItem } from './listening-core.ts';

export const ACCENT_LABEL: Record<string, string> = { american: 'American', british: 'British', australian: 'Australian' };
export const accentLabel = (accent: string): string => ACCENT_LABEL[accent] ?? accent.charAt(0).toUpperCase() + accent.slice(1);
/** Accents that can be generated and recorded today. The others stay visible in the design but are marked unavailable and cannot be chosen. */
export const AVAILABLE_ACCENTS = ['american', 'british', 'australian'] as const;
export const COMING_ACCENTS = ['Irish', 'Indian', 'Scottish', 'Nigerian', 'Canadian', 'South African', 'Singaporean', 'New Zealand', 'Welsh'] as const;
export const LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'] as const;

export function greeting(hour: number): string {
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 18) return 'Good afternoon';
  return 'Good evening';
}

/** The name shown for an account. Until a learner sets a display name (Settings) it comes from the part of the email before the @. */
export function displayName(email?: string | null): string {
  const local = (email ?? '').split('@')[0].replace(/[._-]+/g, ' ').trim();
  if (!local) return 'Learner';
  return local.split(/\s+/).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}
export function initials(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? 'A') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

const when = (item: CatalogItem) => (item.last_activity_at ? Date.parse(item.last_activity_at) : 0);

/** The lesson to resume: the most recently touched unfinished one. */
export function continueItem(items: CatalogItem[]): CatalogItem | null {
  const open = items.filter(i => i.status === 'in_progress');
  return open.length ? open.reduce((a, b) => (when(b) > when(a) ? b : a)) : null;
}

/** Five segments of progress in the stepper design: listen + the four exercises. Never more than the lesson has. */
export function stepsDone(item: Pick<CatalogItem, 'answered' | 'total'>): { done: number; total: number } {
  const total = Math.max(1, item.total || 4) + 1;
  return { done: Math.min(total, Math.max(0, item.answered) + 1), total };
}

/** One recommended lesson, with the only reason Audli can honestly give: it has not been tried, and it is closest to the last level practised. */
export function recommendedNext(items: CatalogItem[], recent: RecentItem[]): { item: CatalogItem; reason: string } | null {
  const unseen = items.filter(i => i.status === 'new');
  if (!unseen.length) return null;
  const lastLevel = recent.find(r => r.status === 'completed')?.cefr;
  const rank = (level: string) => LEVELS.indexOf(level as (typeof LEVELS)[number]);
  const best = lastLevel ? [...unseen].sort((a, b) => Math.abs(rank(a.cefr) - rank(lastLevel)) - Math.abs(rank(b.cefr) - rank(lastLevel)))[0] : unseen[0];
  return { item: best, reason: lastLevel ? `A lesson you haven’t tried yet, close to the ${lastLevel} lesson you finished last.` : 'A lesson you haven’t tried yet.' };
}

export const recentlyCompleted = (progress: Progress | null, count = 3): RecentItem[] =>
  (progress?.recent ?? []).filter(r => r.status === 'completed').slice(0, count);

export type Length = 'any' | 'short' | 'mid' | 'long';
export type Filters = { query: string; cefr: string; accent: string; length: Length; topic: string | null };
export const NO_FILTERS: Filters = { query: '', cefr: 'All', accent: 'All', length: 'any', topic: null };
const MIN = 60_000;
export const LENGTH_LABEL: Record<Exclude<Length, 'any'>, string> = { short: 'Under 3 min', mid: '3–5 min', long: '5+ min' };
const lengthOf = (ms: number): Exclude<Length, 'any'> => (ms < 3 * MIN ? 'short' : ms <= 5 * MIN ? 'mid' : 'long');

export function applyFilters(items: CatalogItem[], f: Filters): CatalogItem[] {
  const q = f.query.trim().toLowerCase();
  return items.filter(i =>
    (!q || `${i.title} ${i.topic} ${i.listen_for}`.toLowerCase().includes(q)) &&
    (f.cefr === 'All' || i.cefr === f.cefr) &&
    (f.accent === 'All' || i.accents.includes(f.accent)) &&
    (f.length === 'any' || lengthOf(i.duration_ms) === f.length) &&
    (!f.topic || i.topic === f.topic));
}
export function topicTiles(items: CatalogItem[]): { topic: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const i of items) counts.set(i.topic, (counts.get(i.topic) ?? 0) + 1);
  return [...counts].map(([topic, count]) => ({ topic, count })).sort((a, b) => b.count - a.count || a.topic.localeCompare(b.topic));
}
export const countLabel = (n: number): string => `${n} lesson${n === 1 ? '' : 's'}`;

export function levelRows(progress: Progress | null): { level: string; count: number }[] {
  return LEVELS.map(level => ({ level, count: progress?.level_counts?.[level] ?? 0 }));
}
export function accentRows(progress: Progress | null): { accent: string; count: number }[] {
  return Object.entries(progress?.accent_counts ?? {}).map(([accent, count]) => ({ accent, count })).sort((a, b) => b.count - a.count || a.accent.localeCompare(b.accent));
}
