import { createClient, type SupabaseClient, type Session } from '@supabase/supabase-js';

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
export function authClient(): SupabaseClient {
  if (client) return client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key?.startsWith('sb_publishable_')) throw new Error('Sign-in is not configured. Please contact Audli support.');
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') {
    throw new Error('Sign-in is not configured. Please contact Audli support.');
  }
  // This page is client rendered. The SDK persists/refreshes browser sessions;
  // FastAPI independently verifies every access token before granting data access.
  client = createClient(url, key, { auth: { storageKey: authStorageKey } });
  return client;
}
