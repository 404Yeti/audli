export const googleStorageKey = 'audli-google-oauth';
export const googlePendingKey = 'audli-google-pending';
const origins = new Set(['https://app.audli.ai', 'http://localhost:3000', 'http://127.0.0.1:3000', 'http://127.0.0.1:3100']);

export function googleRedirect(origin: string): string {
  if (!origins.has(origin)) throw new Error('Google sign-in is unavailable on this address. Please use app.audli.ai.');
  return origin + '/auth/callback';
}

export function googleCallback(href: string, started: string | null, now = Date.now()): string {
  const url = new URL(href);
  googleRedirect(url.origin);
  if (url.pathname !== '/auth/callback' || url.hash) throw new Error('Google sign-in could not be completed. Please try again.');
  if (url.searchParams.has('error')) throw new Error('Google sign-in was canceled or denied. You can try again or use email.');
  const age = now - Number(started);
  const codes = url.searchParams.getAll('code');
  if (!started || !Number.isFinite(age) || age < 0 || age > 600_000 || codes.length !== 1 || !codes[0] || codes[0].length > 4096) {
    throw new Error('Google sign-in expired or was opened in another browser. Please try again here.');
  }
  return codes[0];
}
