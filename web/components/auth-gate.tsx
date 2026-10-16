'use client';

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AudliLoadingScreen } from './audli-loading-screen';
import { STARTUP_TIMEOUT_MS } from '../lib/startup';
import Link from 'next/link';
import { AudliMascot } from './audli-mascot';
import { isAuthRetryableFetchError, type SupabaseClient, type Session } from '@supabase/supabase-js';
import { authClient, authStorageKey, googleAuthClient, sessionIdentity } from '../lib/auth';
import { googlePendingKey, googleRedirect, googleStorageKey } from '../lib/google-oauth';
import { api, currentAuthRevision, setAccessTokenProvider } from '../lib/api';

const AccountContext = createContext<{ scope: string; email?: string; signOut?: () => Promise<void> }>({ scope: 'local' });
export const useAccount = () => useContext(AccountContext);

export function AuthGate({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<'loading' | 'local' | 'supabase' | 'failed'>('loading');
  const [client, setClient] = useState<SupabaseClient | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [signup, setSignup] = useState(false);
  const [landing, setLanding] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const blocked = useRef(false);
  const accountIdentity = useRef<string | null>(null);
  const accessTokenProvider = useRef<(() => Promise<string | null>) | null>(null);

  function clearCredentials() {
    try {
      localStorage.removeItem(authStorageKey);
      localStorage.removeItem(authStorageKey + '-code-verifier');
    } catch { /* The SDK also supports browsers without persistent storage. */ }
  }

  useEffect(() => {
    // Older email templates may fall back to Site URL. Forward only to our
    // fixed recovery route before constructing the persistent learner client.
    const query = new URLSearchParams(window.location.search);
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    if (query.get('type') === 'recovery' || fragment.get('type') === 'recovery' || query.has('token_hash')) {
      window.location.replace('/auth/recovery' + window.location.search + window.location.hash);
      return;
    }
    let active = true;
    let unsubscribe: (() => void) | undefined;
    let supabase: SupabaseClient | undefined;
    const startupDeadline = window.setTimeout(() => {
      if (!active) return;
      active = false; unsubscribe?.();
      setAccessTokenProvider(null);
      setMode('failed');
      setMessage('Your account is taking too long to connect. Please try again.');
    }, STARTUP_TIMEOUT_MS);
    const expire = () => {
      if (!active || blocked.current) return;
      blocked.current = true;
      setAccessTokenProvider(async () => null);
      setSession(null);
      setBusy(true);
      setMessage('Your session expired or is invalid. Please sign in again.');
      // Prevent the old SDK session from remounting private state on a refresh.
      void supabase?.auth.signOut({ scope: 'local' }).catch(() => {}).finally(() => {
        clearCredentials(); if (active) setBusy(false);
      });
    };
    window.addEventListener('audli-session-expired', expire);
    void api<{ mode: string }>('/auth/config').then(config => {
      if (!active) return;
      if (query.get('signin') === '1') setLanding(false);
      if (query.get('oauth') === 'failed') { setLanding(false); setMessage('Google sign-in could not be completed. Please try again or use email.'); }
      if (config.mode === 'local') {
        const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
        if (process.env.NODE_ENV === 'production' && !loopback) throw new Error('The server must enable authenticated access for this deployment.');
        window.clearTimeout(startupDeadline); setMode('local'); return;
      }
      if (config.mode !== 'supabase') throw new Error('Sign-in is not configured.');
      supabase = authClient();
      const tokenProvider = async () => {
        const revision = currentAuthRevision();
        const { data, error } = await supabase!.auth.getSession();
        if (!active || blocked.current || revision !== currentAuthRevision()) return null;
        if (error && isAuthRetryableFetchError(error)) throw new Error('Sign-in refresh is unavailable. Please retry your connection.');
        if (error || !data.session) { expire(); return null; }
        // getSession can observe another tab's account before its auth event arrives.
        const identity = sessionIdentity(data.session);
        if (accountIdentity.current !== identity) {
          accountIdentity.current = identity;
          setAccessTokenProvider(tokenProvider);
          setSession(data.session);
        }
        return data.session.access_token;
      };
      accessTokenProvider.current = tokenProvider;
      setAccessTokenProvider(tokenProvider);
      setClient(supabase); setMode('supabase');
      const { data } = supabase.auth.onAuthStateChange((_event, next) => {
        if (!active) return;
        window.clearTimeout(startupDeadline);
        if (blocked.current) { setReady(true); return; }
        if (accountIdentity.current !== sessionIdentity(next)) {
          accountIdentity.current = sessionIdentity(next);
          setAccessTokenProvider(tokenProvider);
        }
        setSession(next); setReady(true);
      });
      unsubscribe = () => data.subscription.unsubscribe();
    }).catch(error => {
      window.clearTimeout(startupDeadline);
      if (active) { setMode('failed'); setMessage(error instanceof Error ? error.message : 'Sign-in is unavailable. Please retry.'); }
    });
    return () => {
      window.clearTimeout(startupDeadline);
      active = false; unsubscribe?.(); window.removeEventListener('audli-session-expired', expire);
      setAccessTokenProvider(null);
    };
  }, []);

  async function authenticateGoogle() {
    setBusy(true); setMessage('');
    try {
      const redirectTo = googleRedirect(window.location.origin);
      sessionStorage.setItem(googlePendingKey, String(Date.now()));
      const { error } = await googleAuthClient().auth.signInWithOAuth({
        provider: 'google', options: { redirectTo, scopes: 'openid email profile' },
      });
      if (error) throw error;
    } catch {
      try { sessionStorage.removeItem(googlePendingKey); sessionStorage.removeItem(googleStorageKey + '-code-verifier'); } catch { /* Storage unavailable. */ }
      setMessage('Google sign-in could not connect. Please try again or use email.');
    } finally { setBusy(false); }
  }

  async function authenticate() {
    if (!client) return;
    setBusy(true); setMessage('');
    try {
      const result = signup
        ? await client.auth.signUp({ email: email.trim(), password, options: { emailRedirectTo: window.location.origin } })
        : await client.auth.signInWithPassword({ email: email.trim(), password });
      if (result.error) throw result.error;
      if (result.data.session) {
        const identityChanged = blocked.current || accountIdentity.current !== sessionIdentity(result.data.session);
        blocked.current = false;
        accountIdentity.current = sessionIdentity(result.data.session);
        if (identityChanged) setAccessTokenProvider(accessTokenProvider.current);
        setSession(result.data.session);
      }
      setPassword('');
      if (signup && !result.data.session) setMessage('Check your email to confirm your account, then sign in.');
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Sign-in failed. Please retry.'); }
    finally { setBusy(false); }
  }

  async function logout() {
    if (!client) return;
    setBusy(true); setMessage('');
    setSignup(false); setLanding(false); setEmail(''); setPassword('');
    blocked.current = true;
    setAccessTokenProvider(async () => null);
    // Unmount private lesson state immediately, including microphone and playback.
    setSession(null);
    try { sessionStorage.removeItem('audli-session:' + accountIdentity.current); } catch { /* Optional local timing only. */ }
    try {
      const { error } = await client.auth.signOut({ scope: 'local' });
      if (error) setMessage('Signed out here. Server logout was unavailable; close this browser tab if using a shared device.');
    } catch { setMessage('Signed out here. Server logout was unavailable; close this browser tab if using a shared device.');
    } finally {
      // The SDK may leave local storage intact when its logout request fails.
      // Clear this client's credentials even when the Auth service is offline.
      clearCredentials();
      setBusy(false);
    }
  }

  if (mode === 'loading' || (mode === 'supabase' && !ready && !session)) return <AudliLoadingScreen/>;
  if (mode === 'failed') return <AudliLoadingScreen error={message} onRetry={() => window.location.reload()}/>;
  if (mode === 'local') return children;
  if (mode === 'supabase' && session) return <AccountContext.Provider value={{ scope: sessionIdentity(session)!, email: session.user.email, signOut: logout }}><div key={sessionIdentity(session)}>{children}</div></AccountContext.Provider>;
  if (mode === 'supabase' && ready && landing && !message) return <main className="landing"><strong className="wordmark">audli</strong><AudliMascot/><h1>Train your ears.</h1><p>AI listening practice that adapts to what you actually understand.</p><button className="primary" onClick={() => { setSignup(true); setLanding(false); }}>Start listening</button><button className="text-button" onClick={() => { setSignup(false); setLanding(false); }}>I already have an account</button><footer>Eyes optional. Ears essential.</footer></main>;
  return <main className="auth-screen"><header><strong className="wordmark">audli</strong><span className="language">TRAIN YOUR EARS</span></header><section className="card">
    <>
      <AudliMascot small/><h1>{signup ? 'Train your ears with Audli.' : 'Welcome back.'}</h1><p>{signup ? 'Create your account and start listening.' : 'Sign in and pick up where your ears left off.'}</p>
      {message && <p role="status">{message}</p>}
      <button type="button" className="secondary google-signin" disabled={busy} onClick={() => void authenticateGoogle()}><svg aria-hidden="true" width="20" height="20" viewBox="0 0 48 48"><path fill="#4285F4" d="M43.61 24.46c0-1.36-.12-2.66-.35-3.92H24v7.42h11a9.4 9.4 0 0 1-4.08 6.17v5.13h6.61c3.87-3.56 6.08-8.8 6.08-14.8Z"/><path fill="#34A853" d="M24 44c5.51 0 10.13-1.83 13.51-4.94l-6.61-5.13c-1.83 1.23-4.18 1.97-6.9 1.97-5.32 0-9.83-3.59-11.44-8.42H5.74v5.29A20 20 0 0 0 24 44Z"/><path fill="#FBBC05" d="M12.56 27.48A12 12 0 0 1 11.93 24c0-1.21.21-2.38.63-3.48v-5.29H5.74A20 20 0 0 0 4 24c0 3.22.77 6.27 2.14 8.97l6.42-5.49Z"/><path fill="#EA4335" d="M24 12.1c3.01 0 5.69 1.04 7.83 3.08l5.87-5.87A19.7 19.7 0 0 0 24 4 20 20 0 0 0 5.74 15.23l6.82 5.29C14.17 15.69 18.68 12.1 24 12.1Z"/></svg> Continue with Google</button>
      <p className="auth-divider">or continue with email</p>
      <form onSubmit={event => { event.preventDefault(); void authenticate(); }}>
        <label htmlFor="email">Email</label><input id="email" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} />
        <label htmlFor="password">Password</label><input id="password" type="password" autoComplete={signup ? 'new-password' : 'current-password'} required minLength={signup ? 8 : undefined} value={password} onChange={event => setPassword(event.target.value)} />
        <button className="primary" disabled={busy}>{busy ? 'Please wait…' : signup ? 'Sign up' : 'Sign in'}</button>
        {!signup && <Link className="text-button auth-link" href="/forgot-password" prefetch={false}>Forgot password?</Link>}
        <button type="button" className="text-button" disabled={busy} onClick={() => { setSignup(!signup); setMessage(''); }}>{signup ? 'Already have an account? Sign in' : 'New to Audli? Create an account'}</button>
      </form>
    </>
  </section></main>;
}
