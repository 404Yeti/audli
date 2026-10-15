'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { authClient, googleAuthClient } from '../../../lib/auth';
import { googleCallback, googlePendingKey, googleStorageKey } from '../../../lib/google-oauth';

export default function GoogleCallback() {
  const started = useRef(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    let expired = false;
    const deadline = window.setTimeout(() => {
      expired = true;
      setError('Google sign-in is taking too long. Please try again or use email.');
    }, 20000);
    void (async () => {
      try {
        const href = window.location.href;
        // Remove codes/errors before SDK/network requests or navigation.
        window.history.replaceState(null, '', '/auth/callback');
        const code = googleCallback(href, sessionStorage.getItem(googlePendingKey));
        sessionStorage.removeItem(googlePendingKey);
        const { data, error } = await googleAuthClient().auth.exchangeCodeForSession(code);
        if (expired) return;
        if (error || !data.session) throw new Error('Google sign-in could not be completed. Please try again or use email.');
        const result = await authClient().auth.setSession({ access_token: data.session.access_token, refresh_token: data.session.refresh_token });
        if (result.error) throw new Error('Your account could not be connected. Please try again.');
        window.location.replace('/');
      } catch (error) {
        setError(error instanceof Error ? error.message : 'Google sign-in is unavailable. Please try again.');
      } finally {
        window.clearTimeout(deadline);
        // Do not signOut: revoking the temporary session would revoke the learner session too.
        try {
          for (const key of [googlePendingKey, googleStorageKey, googleStorageKey + '-code-verifier', googleStorageKey + '-user']) sessionStorage.removeItem(key);
        } catch { /* Storage unavailable. */ }
      }
    })();
  }, []);
  return <main className="auth-screen"><section className="card"><strong className="wordmark">audli</strong><h1>{error ? 'Let’s try that again.' : 'Connecting your account…'}</h1><p role="status">{error || 'Finishing Google sign-in.'}</p>{error && <Link className="primary auth-link" href="/?signin=1" prefetch={false}>Back to sign in</Link>}</section></main>;
}
