'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { isAuthRetryableFetchError, type SupabaseClient, type Session } from '@supabase/supabase-js';
import { authClient, authStorageKey, sessionIdentity } from '../lib/auth';
import { api, currentAuthRevision, setAccessTokenProvider } from '../lib/api';

export function AuthGate({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<'loading' | 'local' | 'supabase' | 'failed'>('loading');
  const [client, setClient] = useState<SupabaseClient | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [signup, setSignup] = useState(false);
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
    let active = true;
    let unsubscribe: (() => void) | undefined;
    let supabase: SupabaseClient | undefined;
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
      if (config.mode === 'local') {
        const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
        if (process.env.NODE_ENV === 'production' && !loopback) throw new Error('The server must enable authenticated access for this deployment.');
        setMode('local'); return;
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
        if (blocked.current) { setReady(true); return; }
        if (accountIdentity.current !== sessionIdentity(next)) {
          accountIdentity.current = sessionIdentity(next);
          setAccessTokenProvider(tokenProvider);
        }
        setSession(next); setReady(true);
      });
      unsubscribe = () => data.subscription.unsubscribe();
    }).catch(error => {
      if (active) { setMode('failed'); setMessage(error instanceof Error ? error.message : 'Sign-in is unavailable. Please retry.'); }
    });
    return () => {
      active = false; unsubscribe?.(); window.removeEventListener('audli-session-expired', expire);
      setAccessTokenProvider(null);
    };
  }, []);

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
    blocked.current = true;
    setAccessTokenProvider(async () => null);
    // Unmount private lesson state immediately, including microphone and playback.
    setSession(null);
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

  if (mode === 'local') return children;
  if (mode === 'supabase' && session) return <><div className="account-controls"><span>{session.user.email}</span><button className="secondary" disabled={busy} onClick={logout}>Sign out</button></div><div key={sessionIdentity(session)}>{children}</div></>;
  return <main><header><strong className="wordmark">audli</strong><span className="language">TRAIN YOUR EARS</span></header><section className="card">
    {mode === 'loading' || (mode === 'supabase' && !ready) ? <p role="status">Connecting to your account…</p> : <>
      <h1>{signup ? 'Create your account' : 'Welcome to Audli'}</h1><p>Sign in to continue your own listening practice.</p>
      {message && <p role="status">{message}</p>}
      {mode === 'failed' ? <button className="secondary" onClick={() => window.location.reload()}>Retry connection</button> : <form onSubmit={event => { event.preventDefault(); void authenticate(); }}>
        <label htmlFor="email">Email</label><input id="email" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} />
        <label htmlFor="password">Password</label><input id="password" type="password" autoComplete={signup ? 'new-password' : 'current-password'} required minLength={signup ? 8 : undefined} value={password} onChange={event => setPassword(event.target.value)} />
        <button className="primary" disabled={busy}>{busy ? 'Please wait…' : signup ? 'Sign up' : 'Sign in'}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => { setSignup(!signup); setMessage(''); }}>{signup ? 'Already have an account? Sign in' : 'Create an account'}</button>
      </form>}
    </>}
  </section></main>;
}
