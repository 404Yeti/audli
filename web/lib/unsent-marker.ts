/** AUD-89 P2: a note that an upload was in flight, kept in sessionStorage so a reload can explain itself instead of replaying the question
 * as if the learner had not answered. It holds no audio, no text and no learner identity beyond the per-account storage key. */
export type UnsentKind = 'exercise' | 'checkin';
export type Unsent = { kind: UnsentKind; id: string; at: number };
export const UNSENT_TTL_MS = 30 * 60 * 1000;
const key = (scope: string) => `audli-unsent:${scope}`;

export function markUnsent(scope: string, kind: UnsentKind, id: string, now = Date.now()) {
  try { sessionStorage.setItem(key(scope), JSON.stringify({ kind, id, at: now } satisfies Unsent)); } catch { /* Optional memory. */ }
}
export function clearUnsent(scope: string) {
  try { sessionStorage.removeItem(key(scope)); } catch { /* Optional memory. */ }
}
export function readUnsent(scope: string, now = Date.now()): Unsent | null {
  try {
    const raw = sessionStorage.getItem(key(scope));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<Unsent>;
    if ((value.kind !== 'exercise' && value.kind !== 'checkin') || typeof value.id !== 'string' || (typeof value.at !== 'number' || !Number.isFinite(value.at)) || value.id.length > 128 || Object.keys(value).some(k => !['kind', 'id', 'at'].includes(k))) { clearUnsent(scope); return null; }
    if (now - value.at > UNSENT_TTL_MS || value.at > now + 60_000) { clearUnsent(scope); return null; }
    return { kind: value.kind, id: value.id, at: value.at };
  } catch { clearUnsent(scope); return null; }       // unreadable: forget it
}
