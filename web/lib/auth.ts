import { createClient, type SupabaseClient, type Session } from '@supabase/supabase-js';

export function isLearnerAuthCallback(params: Record<string, string>): boolean {
  // A custom SDK hook replaces its default implicit-callback detection.
  // Preserve those signals and exclude recovery handled by the isolated client.
  return params.type !== 'recovery' && Boolean(params.access_token || params.error || params.error_description || params.error_code);
}

// An operation identity only; FastAPI remains the authority for authentication.
// Token refresh preserves session_id, whereas a new login starts a new session.
export function sessionIdentity(session: Session | null): string | null {
  if (!session) return null;
  try {
    const payload = JSON.parse(atob(session.access_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (typeof payload.session_id === 'string') return `${session.user.id}:${payload.session_id}`;
  } catch { /* Legacy/test tokens may lack a session_id; account changes still invalidate. */ }
  return session.user.id;
}

let client: SupabaseClient | null = null;
export const authStorageKey = 'audli-auth-session';
function authConfiguration() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key?.startsWith('sb_publishable_')) throw new Error('Sign-in is not configured. Please contact Audli support.');
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') {
    throw new Error('Sign-in is not configured. Please contact Audli support.');
  }
  return { url, key };
}

export function recoveryClient(): SupabaseClient {
  const { url, key } = authConfiguration();
  // Recovery credentials never enter the learner session's storage or channel.
  return createClient(url, key, { auth: {
    storageKey: 'audli-password-recovery', persistSession: false,
    autoRefreshToken: false, detectSessionInUrl: false, flowType: 'implicit',
  } });
}

export function clearRecoveredLearnerSession(userId: string) {
  // A recovery email may belong to a different account than this browser's
  // learner. Only invalidate matching credentials after a successful reset.
  try {
    const stored = JSON.parse(localStorage.getItem(authStorageKey) ?? 'null');
    if (stored?.user?.id !== userId) return;
    localStorage.removeItem(authStorageKey);
    localStorage.removeItem(authStorageKey + '-code-verifier');
    const channel = new BroadcastChannel(authStorageKey);
    channel.postMessage({ event: 'SIGNED_OUT', session: null });
    channel.close();
  } catch { /* Storage/channel may be unavailable; server logout still applies. */ }
}

export function authClient(): SupabaseClient {
  if (client) return client;
  const { url, key } = authConfiguration();
  // This page is client rendered. The SDK persists/refreshes browser sessions;
  // FastAPI independently verifies every access token before granting data access.
  client = createClient(url, key, { auth: {
    storageKey: authStorageKey,
    detectSessionInUrl: (_url, params) => isLearnerAuthCallback(params),
  } });
  return client;
}
