'use client';
import { useEffect, useRef, useState } from 'react';
import { LessonLifetime } from '../lib/api';
import { useAccount } from './auth-gate';

type Controls = { delete_available: boolean };

/** Permanent account deletion (AUD-34). Renders nothing, not even its heading, unless the server reports it available. */
export function AccountSettings({ disabled }: { disabled: boolean }) {
  const account = useAccount();
  const lifetime = useRef<LessonLifetime | null>(null);
  const [controls, setControls] = useState<Controls | null>(null);
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const trigger = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const owner = new LessonLifetime(); lifetime.current = owner;
    const operation = owner.begin();
    void operation.api<Controls>('/account/controls').then(value => {
      operation.assertCurrent(); setControls(value);
    }).catch(() => { /* No controls means no section. */ }).finally(() => operation.release());
    return () => owner.close();
  }, []);
  function close() {
    if (busy) return;
    setOpen(false); setPassword(''); setConfirmation(''); setError(''); trigger.current?.focus();
  }
  async function submit() {
    if (!lifetime.current?.current || !open || busy || disabled) return;
    const operation = lifetime.current.begin(); setBusy(true); setError('');
    try {
      const result = await operation.api<{ deleted: boolean }>('/account/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation, password }),
      });
      operation.assertCurrent(); setPassword('');
      if (result.deleted !== true) throw new Error('Deletion could not be confirmed. Please retry or contact support.');
      await account.signOut?.(true);
    } catch (cause) { if (operation.current) { setPassword(''); setError((cause as Error).message); } }
    finally { if (operation.current) setBusy(false); operation.release(); }
  }
  if (!controls?.delete_available) return null;
  return <><dt>Account &amp; Data</dt><dd><section aria-label="Account and data">
    <button className="secondary" disabled={disabled || busy} onClick={event => { trigger.current = event.currentTarget; setError(''); setOpen(true); }}>Delete Account</button>
    {open && <div className="dialog-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="account-dialog-title" aria-describedby="account-dialog-description" className="exit-dialog account-dialog" onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key === 'Tab') {
        const elements = event.currentTarget.querySelectorAll<HTMLElement>('a[href], input:not(:disabled), button:not(:disabled)');
        if (!elements.length) { event.preventDefault(); return; }
        const first = elements[0], last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <h2 id="account-dialog-title">Permanently delete your account?</h2>
      <p id="account-dialog-description">This permanently deletes your account, profile, preferences, onboarding, lessons, attempts, saved audio, plan and any language suggestions. You cannot undo this. Security logs, AI and voice provider records, and existing backups may remain under their retention policies.</p>
      <p>Accounts protected by MFA need support-assisted deletion. Keep MFA enabled and contact <a href="mailto:robbie@audli.ai?subject=Audli%20account%20deletion">Audli support</a>. Never send your password or recovery codes.</p>
      <form onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label htmlFor="delete-password">Confirm your current password</label>
        <input autoFocus id="delete-password" type="password" autoComplete="current-password" required maxLength={1024} disabled={busy} value={password} onChange={event => setPassword(event.target.value)}/>
        <label htmlFor="account-confirmation">Type DELETE MY ACCOUNT to confirm</label>
        <input id="account-confirmation" autoComplete="off" disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)}/>
        {error && <p role="alert">{error}</p>}
        <button className="primary" disabled={disabled || busy || confirmation !== 'DELETE MY ACCOUNT' || !password}>{busy ? 'Please wait…' : 'Permanently delete account'}</button>
        <button type="button" className="secondary" disabled={busy} onClick={close}>Cancel</button>
      </form>
    </section></div>}
  </section></dd></>;
}
