export const recoveryPath = '/auth/recovery';
export const recoveryConfirmation = 'If an account exists for that email, you’ll receive a password-reset link. Check your inbox and spam folder.';
export const invalidRecoveryMessage = 'This password-reset link is invalid, expired, or has already been used. Request a new link.';

export function recoveryRedirect(origin: string, development: boolean): string {
  const url = new URL(origin);
  if (development && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
      && ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
    return url.origin + recoveryPath;
  }
  // Never trust Host, next, redirectTo, preview URLs, or user input for email links.
  return 'https://app.audli.ai' + recoveryPath;
}

export type RecoveryCredential =
  | { kind: 'hash'; tokenHash: string }
  | { kind: 'session'; accessToken: string; refreshToken: string }
  | { kind: 'invalid' };

export function parseRecoveryLink(href: string, nowSeconds = Date.now() / 1000): RecoveryCredential {
  const invalid = { kind: 'invalid' } as const;
  if (href.length > 24000) return invalid;
  let url: URL;
  try { url = new URL(href); } catch { return invalid; }
  const query = url.searchParams;
  const fragment = new URLSearchParams(url.hash.slice(1));
  for (const key of ['type', 'token_hash', 'access_token', 'refresh_token', 'error', 'error_code']) {
    if (query.getAll(key).length + fragment.getAll(key).length > 1) return invalid;
  }
  if (query.has('error') || query.has('error_code') || fragment.has('error') || fragment.has('error_code')) return invalid;
  if ((query.get('type') ?? fragment.get('type')) !== 'recovery') return invalid;
  const tokenHash = query.get('token_hash');
  if (tokenHash && /^[a-zA-Z0-9_-]{16,512}$/.test(tokenHash)
      && !fragment.has('access_token') && !fragment.has('refresh_token')) {
    return { kind: 'hash', tokenHash };
  }
  const accessToken = fragment.get('access_token');
  const refreshToken = fragment.get('refresh_token');
  if (!query.has('token_hash') && !query.has('access_token') && !query.has('refresh_token')
      && accessToken && refreshToken && accessToken.length <= 8192 && refreshToken.length <= 8192
      && /^[a-zA-Z0-9_.-]+$/.test(accessToken) && /^[a-zA-Z0-9_-]+$/.test(refreshToken)) {
    // Do not let setSession silently refresh an expired email callback token.
    // This is an expiry precheck only; Supabase verifies the token/user below.
    try {
      const payload = JSON.parse(atob(accessToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
      if (!Number.isFinite(payload.exp) || payload.exp <= nowSeconds) return invalid;
    } catch { return invalid; }
    return { kind: 'session', accessToken, refreshToken };
  }
  return invalid;
}

export function passwordValidation(password: string, confirmation: string): string | null {
  if (password.length < 8) return 'Use at least 8 characters for your password.';
  if (password.length > 128) return 'Use no more than 128 characters for your password.';
  if (!password.trim()) return 'Your password cannot contain only spaces.';
  if (password !== confirmation) return 'Your passwords do not match.';
  return null;
}

export function recoverySessionExpired(code?: string, status?: number): boolean {
  return status === 401 || ['session_not_found', 'refresh_token_not_found', 'refresh_token_already_used',
    'bad_jwt', 'jwt_expired', 'otp_expired', 'user_not_found'].includes(code ?? '');
}
