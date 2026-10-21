/** Daily lesson eligibility (AUD-47). Pure helpers: the server decides, the browser only displays.
 *
 *  The countdown is derived from the server's own timestamps plus the browser's MONOTONIC clock
 *  (performance.now), never from Date.now(), so a wrong device clock or timezone cannot unlock or extend anything.
 *  The classroom opens only from a fresh server answer, never because a local timer reached zero. */
export type Mode = 'off' | 'observe' | 'enforce';
export type OpenLesson = { id: string; status: 'active' | 'paused'; started_at: string; expires_at: string; grace_ends_at: string };
export type LastLesson = { id: string; status: 'active' | 'paused' | 'completed' | 'expired' };
export type Eligibility = {
  mode: Mode; enforced: boolean; eligible: boolean; reason: 'eligible' | 'cooldown' | 'lesson_in_progress';
  server_time: string; next_available_at: string | null; retry_after_seconds: number | null;
  last_started_at: string | null; open_lesson: OpenLesson | null; last_lesson: LastLesson | null; lesson_max_seconds: number;
};

const MODES = ['off', 'observe', 'enforce'];
const REASONS = ['eligible', 'cooldown', 'lesson_in_progress'];
const LESSON_STATUSES = ['active', 'paused', 'completed', 'expired'];
const instant = (value: unknown): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value));
const optionalInstant = (value: unknown) => value === null || value === undefined || instant(value);

/** Strict: anything else (an old server's 404 body, a proxy's `[]`, a partial object) is "unknown", never a guess. */
export function parseEligibility(data: unknown): Eligibility | null {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const d = data as Record<string, unknown>;
  if (!MODES.includes(d.mode as string) || typeof d.enforced !== 'boolean' || typeof d.eligible !== 'boolean'
    || !REASONS.includes(d.reason as string) || !instant(d.server_time) || !optionalInstant(d.next_available_at)
    || !optionalInstant(d.last_started_at)) return null;
  let open: OpenLesson | null = null;
  if (d.open_lesson !== null && d.open_lesson !== undefined) {
    const o = d.open_lesson as Record<string, unknown>;
    if (!o || typeof o !== 'object' || typeof o.id !== 'string' || (o.status !== 'active' && o.status !== 'paused')
      || !instant(o.started_at) || !instant(o.expires_at) || !instant(o.grace_ends_at)) return null;
    open = { id: o.id, status: o.status, started_at: o.started_at, expires_at: o.expires_at, grace_ends_at: o.grace_ends_at };
  }
  let last: LastLesson | null = null;
  if (d.last_lesson && typeof d.last_lesson === 'object') {
    const l = d.last_lesson as Record<string, unknown>;
    if (typeof l.id === 'string' && LESSON_STATUSES.includes(l.status as string)) last = { id: l.id, status: l.status as LastLesson['status'] };
  }
  return {
    mode: d.mode as Mode, enforced: d.enforced, eligible: d.eligible, reason: d.reason as Eligibility['reason'], server_time: d.server_time as string,
    next_available_at: (d.next_available_at as string | null | undefined) ?? null,
    retry_after_seconds: typeof d.retry_after_seconds === 'number' ? d.retry_after_seconds : null,
    last_started_at: (d.last_started_at as string | null | undefined) ?? null, open_lesson: open, last_lesson: last,
    lesson_max_seconds: typeof d.lesson_max_seconds === 'number' ? d.lesson_max_seconds : 600,
  };
}

/** A server answer plus the monotonic moment it arrived. */
export type Snapshot = { value: Eligibility; serverMs: number; monoMs: number };
export const snapshotOf = (value: Eligibility, mono: number): Snapshot => ({ value, serverMs: Date.parse(value.server_time), monoMs: mono });
/** Server time now, advanced only by elapsed monotonic time. */
export const serverNow = (snapshot: Snapshot, mono: number) => snapshot.serverMs + Math.max(0, mono - snapshot.monoMs);
export const msUntil = (snapshot: Snapshot, mono: number, iso: string) => Date.parse(iso) - serverNow(snapshot, mono);

export type HomeView =
  | { kind: 'legacy' }
  | { kind: 'ready' }
  | { kind: 'cooldown'; remainingMs: number; nextAt: string; expired: boolean }
  | { kind: 'resume'; remainingMs: number; closing: boolean; paused: boolean }
  | { kind: 'checking' };

/** off, observe and an unreachable/old server all keep today's Home. Only an enforcing server changes it. */
export function homeView(snapshot: Snapshot | null, mono: number): HomeView {
  if (!snapshot || !snapshot.value.enforced) return { kind: 'legacy' };
  const v = snapshot.value;
  if (v.open_lesson) {
    if (msUntil(snapshot, mono, v.open_lesson.grace_ends_at) <= 0) return { kind: 'checking' };
    const remaining = msUntil(snapshot, mono, v.open_lesson.expires_at);
    return { kind: 'resume', remainingMs: Math.max(0, remaining), closing: remaining <= 0, paused: v.open_lesson.status === 'paused' };
  }
  if (v.reason === 'cooldown' && v.next_available_at) {
    const remaining = msUntil(snapshot, mono, v.next_available_at);
    return remaining > 0 ? { kind: 'cooldown', remainingMs: remaining, nextAt: v.next_available_at, expired: v.last_lesson?.status === 'expired' } : { kind: 'checking' };
  }
  return v.eligible ? { kind: 'ready' } : { kind: 'checking' };
}

/** Compact, calm display: "18 h 42 min", "42 minutes", "about a minute". Always rounds up so zero is never shown early. */
export function formatRemaining(ms: number): string {
  const minutes = Math.max(1, Math.ceil(ms / 60000));
  if (minutes >= 60) return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
  return minutes <= 1 ? 'about a minute' : `${minutes} minutes`;
}

/** Full sentence form for screen readers, updated per minute only. */
export function spokenRemaining(ms: number): string {
  const minutes = Math.max(1, Math.ceil(ms / 60000));
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  const part = (n: number, unit: string) => `${n} ${unit}${n === 1 ? '' : 's'}`;
  if (hours > 0) return rest ? `${part(hours, 'hour')} ${part(rest, 'minute')}` : part(hours, 'hour');
  return minutes === 1 ? 'about a minute' : part(minutes, 'minute');
}

/** "today around 7:42 PM" / "tomorrow around 7:42 PM" / "Friday around 7:42 PM". The instant is the server's;
 *  "today" is judged against server-derived now, so a wrong device date cannot mislabel it. */
export function nextLessonLabel(nextIso: string, nowServerMs: number, options: { locale?: string; timeZone?: string } = {}): string {
  const at = new Date(nextIso), now = new Date(nowServerMs);
  const day = (date: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: options.timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
  const time = new Intl.DateTimeFormat(options.locale, { timeZone: options.timeZone, hour: 'numeric', minute: '2-digit' }).format(at);
  const between = Math.round((Date.parse(day(at) + 'T00:00:00Z') - Date.parse(day(now) + 'T00:00:00Z')) / 86400000);
  const label = between <= 0 ? 'today' : between === 1 ? 'tomorrow'
    : new Intl.DateTimeFormat(options.locale, { timeZone: options.timeZone, weekday: 'long' }).format(at);
  return `${label} around ${time}`;
}

/** How often the display needs to move: finer only when the end is near. */
export function tickInterval(view: HomeView): number | null {
  if (view.kind === 'cooldown' || view.kind === 'resume') return view.remainingMs < 120000 ? 1000 : 15000;
  return view.kind === 'checking' ? 1500 : null;
}

/** Automatic revalidations (focus, visibility, online, timers) are coalesced; user actions are not. */
export const MIN_REVALIDATION_GAP_MS = 1000;
export const shouldRevalidate = (lastAtMono: number | null, mono: number, force: boolean) =>
  force || lastAtMono === null || mono - lastAtMono >= MIN_REVALIDATION_GAP_MS;
