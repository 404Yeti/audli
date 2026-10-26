/** AUD-89 P2: the recording-upload retry policy, as served by `GET /api/upload-policy`.
 *
 * Fail closed. Anything that is not a complete, in-range, correctly typed policy with `enabled: true` is DISABLED, and DISABLED means the
 * browser behaves exactly as before this feature existed. The bounds below mirror app/upload_policy.py; both are tested against the same
 * cases (tests/fixtures/upload_policy_cases.json). */
export type PolicyNumbers = {
  baseMs: number; bytesPerSecond: number; minMs: number; maxMs: number; growth: number[]; maxSends: number;
  deadlineMs: number; backoffMs: number[]; slowHintMs: number;
};
export type UploadPolicy = { enabled: false } | ({ enabled: true } & PolicyNumbers);
export const DISABLED: UploadPolicy = { enabled: false };

const KEYS = ['enabled', 'base_ms', 'bytes_per_second', 'min_ms', 'max_ms', 'growth', 'max_sends', 'deadline_ms', 'backoff_ms', 'slow_hint_ms'];
const whole = (value: unknown, low: number, high: number): value is number => typeof value === 'number' && Number.isInteger(value) && value >= low && value <= high;
const list = (value: unknown, min: number, max: number, item: (entry: unknown) => boolean): value is number[] =>
  Array.isArray(value) && value.length >= min && value.length <= max && value.every(item);

export function parseUploadPolicy(value: unknown): UploadPolicy {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return DISABLED;
  const doc = value as Record<string, unknown>;
  if (doc.enabled !== true || Object.keys(doc).some(key => !KEYS.includes(key))) return DISABLED;
  const { base_ms, bytes_per_second, min_ms, max_ms, growth, max_sends, deadline_ms, backoff_ms, slow_hint_ms } = doc;
  if (!whole(base_ms, 1000, 60000) || !whole(bytes_per_second, 8000, 2_000_000) || !whole(min_ms, 2000, 60000) || !whole(max_ms, 2000, 120000)
    || !list(growth, 1, 5, entry => typeof entry === 'number' && Number.isFinite(entry) && entry >= 1 && entry <= 3)
    || !whole(max_sends, 1, 5) || !whole(deadline_ms, 5000, 300000) || !list(backoff_ms, 0, 4, entry => whole(entry, 0, 10000))
    || !whole(slow_hint_ms, 500, 30000)) return DISABLED;
  if (min_ms > max_ms || deadline_ms < min_ms) return DISABLED;
  return { enabled: true, baseMs: base_ms, bytesPerSecond: bytes_per_second, minMs: min_ms, maxMs: max_ms, growth, maxSends: max_sends,
    deadlineMs: deadline_ms, backoffMs: backoff_ms, slowHintMs: slow_hint_ms };
}

/** clamp(base + bytes / rate, min, max) x growth[send], in milliseconds; `sendIndex` is 0 for the first send (the last growth factor repeats). */
export function sendTimeoutMs(policy: PolicyNumbers, bytes: number, sendIndex: number): number {
  const size = Number.isFinite(bytes) ? Math.min(Math.max(0, bytes), 2 ** 31) : 0;
  const clamped = Math.min(policy.maxMs, Math.max(policy.minMs, policy.baseMs + (size / policy.bytesPerSecond) * 1000));
  const factor = policy.growth[Math.min(Math.max(0, sendIndex), policy.growth.length - 1)];
  return Math.min(policy.maxMs, Math.round(clamped * factor));
}

/** The policy is read when needed and cached briefly: an enabled policy for a minute (so the production kill switch reaches open pages
 * quickly), a disabled or failed answer for longer. A failing, slow or odd answer is DISABLED. */
export function createPolicyLoader(read: () => Promise<unknown>, clock: () => number = () => Date.now(), enabledTtlMs = 60_000, disabledTtlMs = 300_000, failedTtlMs = 30_000) {
  let cached: { policy: UploadPolicy; until: number } | null = null;
  let pending: Promise<UploadPolicy> | null = null;
  return {
    get(): Promise<UploadPolicy> {
      if (cached && clock() < cached.until) return Promise.resolve(cached.policy);
      pending ??= (async () => {
        try {
          const policy = parseUploadPolicy(await read());
          cached = { policy, until: clock() + (policy.enabled ? enabledTtlMs : disabledTtlMs) };
        } catch { cached = { policy: DISABLED, until: clock() + failedTtlMs }; }
        finally { pending = null; }
        return cached!.policy;
      })();
      return pending;
    },
    reset() { cached = null; pending = null; },
  };
}
