import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseEligibility, snapshotOf, serverNow, msUntil, homeView, formatRemaining, spokenRemaining, nextLessonLabel, tickInterval, shouldRevalidate } from '../web/lib/eligibility.ts';

const T = Date.parse('2026-10-21T12:00:00Z');
const body = (extra = {}) => ({ mode: 'enforce', enforced: true, eligible: false, reason: 'cooldown', server_time: '2026-10-21T12:00:00Z',
  next_available_at: '2026-10-22T12:00:00Z', retry_after_seconds: 86400, last_started_at: '2026-10-21T11:50:00Z', open_lesson: null,
  last_lesson: { id: 'l1', status: 'completed' }, lesson_max_seconds: 600, ...extra });
const view = (data, mono = 1000, at = 1000) => homeView(snapshotOf(parseEligibility(data), at), mono);

test('only a complete, recognizable server answer is accepted; everything else is unknown, never a guess', () => {
  assert.ok(parseEligibility(body()));
  for (const bad of [[], null, 'x', {}, { mode: 'enforce' }, body({ mode: 'strict' }), body({ reason: 'maybe' }), body({ server_time: 'yesterday' }),
    body({ next_available_at: 'soon' }), body({ enforced: 'yes' }), body({ open_lesson: { id: 1 } })]) assert.equal(parseEligibility(bad), null);
  assert.equal(parseEligibility(body({ last_lesson: undefined })).last_lesson, null);
  assert.equal(parseEligibility(body({ retry_after_seconds: undefined })).retry_after_seconds, null);
});

test('off, observe and an unknown or old server keep today\'s Home; only enforce changes it', () => {
  assert.equal(homeView(null, 0).kind, 'legacy');
  assert.equal(view(body({ mode: 'off', enforced: false, eligible: true, reason: 'eligible', next_available_at: null })).kind, 'legacy');
  // Observe computes a cooldown but never blocks: the browser must not show one.
  assert.equal(view(body({ mode: 'observe', enforced: false })).kind, 'legacy');
});

test('enforce: eligible opens Home normally, cooldown counts down, and the end of the countdown only means "ask again"', () => {
  assert.equal(view(body({ eligible: true, reason: 'eligible', next_available_at: null })).kind, 'ready');
  const cooling = view(body());
  assert.equal(cooling.kind, 'cooldown'); assert.equal(cooling.remainingMs, 86400000); assert.equal(cooling.expired, false);
  assert.equal(view(body(), 1000 + 86400000 - 1).kind, 'cooldown');
  // Zero reached locally: never "ready" until the server says so.
  assert.equal(view(body(), 1000 + 86400000).kind, 'checking');
  assert.equal(view(body(), 1000 + 90 * 86400000).kind, 'checking');
  assert.equal(view(body({ last_lesson: { id: 'l1', status: 'expired' } })).expired, true);
});

test('an open lesson shows resume with its wall-clock remainder, then waits for the server once the grace is over', () => {
  const open = { id: 'l1', status: 'paused', started_at: '2026-10-21T11:56:00Z', expires_at: '2026-10-21T12:06:00Z', grace_ends_at: '2026-10-21T12:09:00Z' };
  const data = body({ reason: 'lesson_in_progress', eligible: false, open_lesson: open, next_available_at: null });
  const resume = view(data); assert.deepEqual([resume.kind, resume.remainingMs, resume.closing, resume.paused], ['resume', 360000, false, true]);
  const closing = view(data, 1000 + 361000); assert.deepEqual([closing.kind, closing.closing, closing.remainingMs], ['resume', true, 0]);
  assert.equal(view(data, 1000 + 541000).kind, 'checking');
});

test('clock skew: a wrong device clock or time zone changes nothing; only server time and the monotonic clock count', () => {
  const real = Date.now;
  try {
    for (const skew of [-5 * 86400000, 0, 5 * 86400000, 400 * 86400000]) {
      Date.now = () => real() + skew;
      const snapshot = snapshotOf(parseEligibility(body()), 5000);
      assert.equal(serverNow(snapshot, 5000), T);
      assert.equal(serverNow(snapshot, 65000), T + 60000);
      assert.equal(msUntil(snapshot, 5000, '2026-10-22T12:00:00Z'), 86400000);
      assert.equal(homeView(snapshot, 5000).kind, 'cooldown');
      assert.equal(nextLessonLabel('2026-10-22T12:00:00Z', T, { timeZone: 'UTC', locale: 'en-US' }), 'tomorrow around 12:00 PM');
    }
  } finally { Date.now = real; }
  const snapshot = snapshotOf(parseEligibility(body()), 5000);
  assert.equal(serverNow(snapshot, 1000), T); // The monotonic clock never moves backwards into the past.
});

test('countdown text rounds up, stays calm and never shows zero early', () => {
  assert.equal(formatRemaining(86400000), '24 h 00 min');
  assert.equal(formatRemaining(18 * 3600000 + 42 * 60000), '18 h 42 min');
  assert.equal(formatRemaining(18 * 3600000 + 41 * 60000 + 1), '18 h 42 min');
  assert.equal(formatRemaining(59 * 60000 + 59999), '1 h 00 min');
  assert.equal(formatRemaining(42 * 60000), '42 minutes');
  assert.equal(formatRemaining(61000), '2 minutes');
  assert.equal(formatRemaining(1), 'about a minute');
  assert.equal(spokenRemaining(18 * 3600000 + 42 * 60000), '18 hours 42 minutes');
  assert.equal(spokenRemaining(3600000), '1 hour');
  assert.equal(spokenRemaining(61 * 60000), '1 hour 1 minute');
  assert.equal(spokenRemaining(30000), 'about a minute');
});

test('next-lesson label is judged against server time and the learner\'s own zone', () => {
  const at = '2026-10-22T19:42:00Z';
  assert.equal(nextLessonLabel(at, T, { timeZone: 'UTC', locale: 'en-US' }), 'tomorrow around 7:42 PM');
  assert.equal(nextLessonLabel(at, Date.parse('2026-10-22T08:00:00Z'), { timeZone: 'UTC', locale: 'en-US' }), 'today around 7:42 PM');
  assert.equal(nextLessonLabel(at, Date.parse('2026-10-19T08:00:00Z'), { timeZone: 'UTC', locale: 'en-US' }), 'Thursday around 7:42 PM');
  // Same instant in Auckland (UTC+13): the calendar day shifts for both ends, so it is still "tomorrow" there, at 8:42 AM.
  assert.equal(nextLessonLabel(at, T, { timeZone: 'Pacific/Auckland', locale: 'en-US' }), 'tomorrow around 8:42 AM');
  assert.equal(nextLessonLabel(at, Date.parse('2026-10-22T11:30:00Z'), { timeZone: 'Pacific/Auckland', locale: 'en-US' }), 'today around 8:42 AM');
});

test('ticker cadence is coarse far away and fine near the end; automatic revalidations are coalesced, user actions are not', () => {
  assert.equal(tickInterval({ kind: 'legacy' }), null); assert.equal(tickInterval({ kind: 'ready' }), null);
  assert.equal(tickInterval({ kind: 'cooldown', remainingMs: 3600000 }), 15000);
  assert.equal(tickInterval({ kind: 'cooldown', remainingMs: 119000 }), 1000);
  assert.equal(tickInterval({ kind: 'checking' }), 1500);
  assert.equal(shouldRevalidate(null, 10, false), true);
  assert.equal(shouldRevalidate(1000, 1500, false), false);
  assert.equal(shouldRevalidate(1000, 2000, false), true);
  assert.equal(shouldRevalidate(1000, 1100, true), true);
});
