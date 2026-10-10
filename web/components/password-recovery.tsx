'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { isAuthRetryableFetchError, type SupabaseClient } from '@supabase/supabase-js';
import { AudliMascot } from './audli-mascot';
import { clearRecoveredLearnerSession, recoveryClient } from '../lib/auth';
import { api } from '../lib/api';
import { invalidRecoveryMessage, parseRecoveryLink, passwordValidation, recoveryConfirmation,
  recoveryPath, recoveryRedirect, recoverySessionExpired } from '../lib/password-recovery';

function RecoveryShell({ children }: { children: ReactNode }) {
  return <main className="auth-screen"><header><strong className="wordmark">audli</strong><span className="language">TRAIN YOUR EARS</span></header>
    <section className="card"><AudliMascot small/>{children}</section></main>;
}

export function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);

  async function requestReset() {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    let client: SupabaseClient | undefined;
    try {
      const config = await api<{ mode: string }>('/auth/config');
      if (config.mode !== 'supabase') throw new Error();
      client = recoveryClient();
      const result = await client.auth.resetPasswordForEmail(email.trim(), {
        redirectTo: recoveryRedirect(window.location.origin, process.env.NODE_ENV === 'development'),
      });
      // Supabase deliberately hides account existence. Do not expose provider
      // messages (including account-specific failures) through this screen.
      if (result.error && isAuthRetryableFetchError(result.error)) throw new Error();
      setEmail(''); setSent(true);
    } catch { setError('Could not connect to password recovery. Please try again.'); }
    finally { void client?.auth.stopAutoRefresh(); pending.current = false; setBusy(false); }
  }

  return <RecoveryShell><h1>Reset your password.</h1><p>Enter your email and we’ll help you get back to listening.</p>
    {sent ? <p role="status" className="status">{recoveryConfirmation}</p> : <form onSubmit={event => { event.preventDefault(); void requestReset(); }} aria-busy={busy}>
      {error && <p role="alert" className="error">{error}</p>}
      <label htmlFor="reset-email">Email</label><input id="reset-email" type="email" autoComplete="email" required maxLength={254} disabled={busy} value={email} onChange={event => setEmail(event.target.value)} />
      <button className="primary" disabled={busy}>{busy ? 'Sending…' : 'Send reset link'}</button>
    </form>}
    <Link className="text-button auth-link" href="/?signin=1" prefetch={false}>Back to sign in</Link>
  </RecoveryShell>;
}

type RecoveryState = 'checking' | 'ready' | 'invalid' | 'unavailable' | 'success';
type VerifiedRecovery = { client: SupabaseClient; userId: string; expiresAt: number };

export function ResetPassword() {
  const [state, setState] = useState<RecoveryState>('checking');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const verified = useRef<VerifiedRecovery | null>(null);
  const initialization = useRef<Promise<VerifiedRecovery> | null>(null);
  const pending = useRef(false);

  useEffect(() => {
    let active = true;
    // Reuse initialization during React's effect replay: a one-time link must
    // never be verified twice. Remove all credentials/errors/redirects first.
    if (!initialization.current) {
      const credential = parseRecoveryLink(window.location.href);
      window.history.replaceState(null, '', recoveryPath);
      initialization.current = (async () => {
        if (credential.kind === 'invalid') throw new Error('invalid');
        const config = await api<{ mode: string }>('/auth/config');
        if (config.mode !== 'supabase') throw new Error('unavailable');
        const client = recoveryClient();
        try {
          const result = credential.kind === 'hash'
            ? await client.auth.verifyOtp({ type: 'recovery', token_hash: credential.tokenHash })
            : await client.auth.setSession({ access_token: credential.accessToken, refresh_token: credential.refreshToken });
          if (result.error || !result.data.session) {
            throw new Error(result.error && isAuthRetryableFetchError(result.error) ? 'unavailable' : 'invalid');
          }
          // Verify against Supabase rather than trusting URL/JWT claims.
          const user = await client.auth.getUser();
          if (user.error || !user.data.user || user.data.user.id !== result.data.session.user.id) {
            throw new Error(user.error && isAuthRetryableFetchError(user.error) ? 'unavailable' : 'invalid');
          }
          if (!result.data.session.expires_at || result.data.session.expires_at <= Date.now() / 1000) throw new Error('invalid');
          return { client, userId: user.data.user.id, expiresAt: result.data.session.expires_at };
        } catch (reason) {
          void client.auth.stopAutoRefresh(); throw reason;
        }
      })();
    }
    const attempt = initialization.current;
    void attempt.then(result => {
      if (active) { verified.current = result; setState('ready'); }
    }).catch(reason => {
      if (active) setState(reason instanceof Error && reason.message === 'invalid' ? 'invalid' : 'unavailable');
    });
    return () => { active = false; void attempt.then(result => result.client.auth.stopAutoRefresh()).catch(() => {}); };
  }, []);

  function invalidateRecovery(client: SupabaseClient) {
    void client.auth.stopAutoRefresh(); verified.current = null; initialization.current = null;
    setPassword(''); setConfirmation(''); setState('invalid');
  }

  async function savePassword() {
    if (pending.current || state !== 'ready' || !verified.current) return;
    const validation = passwordValidation(password, confirmation);
    if (validation) { setError(validation); return; }
    const { client, userId, expiresAt } = verified.current;
    if (expiresAt <= Date.now() / 1000) { invalidateRecovery(client); return; }
    pending.current = true; setBusy(true); setError('');
    try {
      const user = await client.auth.getUser();
      if (user.error || !user.data.user || user.data.user.id !== userId) {
        if (user.error && !recoverySessionExpired(user.error.code, user.error.status)) throw new Error();
        invalidateRecovery(client); return;
      }
      const result = await client.auth.updateUser({ password });
      if (result.error) {
        if (recoverySessionExpired(result.error.code, result.error.status)) { invalidateRecovery(client); return; }
        setError(result.error.code === 'weak_password' ? 'Choose a stronger password that meets your account’s password requirements.'
          : result.error.code === 'same_password' ? 'Choose a different password from your current password.'
          : 'Could not update your password. Please try again.');
        return;
      }
      setPassword(''); setConfirmation(''); verified.current = null;
      clearRecoveredLearnerSession(userId);
      // End the temporary recovery session and revoke this recovered account's
      // refresh sessions. A different learner's session remains untouched.
      const logout = await client.auth.signOut({ scope: 'global' }).catch(() => ({ error: true }));
      void client.auth.stopAutoRefresh(); initialization.current = null;
      if (logout.error) setError('Your password was changed, but signing out other sessions could not be confirmed. Review your account sessions after signing in.');
      setState('success');
    } catch { setError('Could not update your password. Please try again.'); }
    finally { pending.current = false; setBusy(false); }
  }

  return <RecoveryShell><h1>{state === 'success' ? 'Password updated.' : 'Choose a new password.'}</h1>
    {state === 'checking' && <p role="status">Checking your reset link…</p>}
    {state === 'invalid' && <p role="alert" className="error">{invalidRecoveryMessage}</p>}
    {state === 'unavailable' && <p role="alert" className="error">Password recovery is unavailable. Open your email link again, or request a new link.</p>}
    {state === 'success' && <p role="status" className="status">Your password has been changed. You can now sign in with your new password.</p>}
    {state === 'ready' && <form onSubmit={event => { event.preventDefault(); void savePassword(); }} aria-busy={busy}>
      <p id="password-help">Use 8–128 characters. Your account may require a stronger password.</p>
      <label htmlFor="new-password">New password</label><input id="new-password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} aria-describedby="password-help" disabled={busy} value={password} onChange={event => setPassword(event.target.value)} />
      <label htmlFor="confirm-password">Confirm password</label><input id="confirm-password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)} />
      <button className="primary" disabled={busy}>{busy ? 'Updating…' : 'Update password'}</button>
    </form>}
    {error && <p role="alert" className="error">{error}</p>}
    {(state === 'invalid' || state === 'unavailable') && <Link className="primary auth-link" href="/forgot-password" prefetch={false}>Request a new link</Link>}
    {state !== 'checking' && !busy && <Link className="text-button auth-link" href="/?signin=1" prefetch={false}>{state === 'success' ? 'Sign in' : 'Back to sign in'}</Link>}
  </RecoveryShell>;
}
