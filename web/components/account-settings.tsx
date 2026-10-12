'use client';
import { useEffect, useRef, useState } from 'react';
import { LessonLifetime } from '../lib/api';
import { useAccount } from './auth-gate';

type Controls = { delete_available: boolean; restart_available: boolean; reset_description: string; onboarding_revision: number };
export function AccountSettings({ disabled }: { disabled: boolean }) {
  const account = useAccount();
  const lifetime = useRef<LessonLifetime | null>(null);
  const [controls, setControls] = useState<Controls | null>(null);
  const [action, setAction] = useState<'delete' | 'restart' | null>(null);
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
    }).catch(cause => { if (operation.current) setError((cause as Error).message); }).finally(() => operation.release());
    return () => owner.close();
  }, []);
  function close() {
    if (busy) return;
    setAction(null); setPassword(''); setConfirmation(''); setError(''); trigger.current?.focus();
  }
  async function submit() {
    if (!lifetime.current?.current || !action || !controls || busy || disabled) return;
    const operation = lifetime.current.begin(); setBusy(true); setError('');
    try {
      if (action === 'delete') {
        const result = await operation.api<{ deleted: boolean }>('/account/delete', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmation, password }),
        });
        operation.assertCurrent(); setPassword('');
        if (result.deleted !== true) throw new Error('Deletion could not be confirmed. Please retry or contact support.');
        await account.signOut?.(true);
      } else {
        await operation.api('/account/restart-onboarding', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmation, revision: controls.onboarding_revision }),
        });
        operation.assertCurrent();
        try { sessionStorage.removeItem('audli-session:' + account.scope); } catch { /* Optional local timing. */ }
        window.location.reload();
      }
    } catch (cause) { if (operation.current) { setPassword(''); setError((cause as Error).message); } }
    finally { if (operation.current) setBusy(false); operation.release(); }
  }
  const expected = action === 'delete' ? 'DELETE MY ACCOUNT' : 'RESTART ONBOARDING';
  return <section aria-label="Account and data">
    {controls?.delete_available && <button className="secondary" disabled={disabled || busy} onClick={event => {
      trigger.current = event.currentTarget; setError(''); setAction('delete');
    }}>Delete Account</button>}
    {controls?.restart_available && <button className="secondary" disabled={disabled || busy} onClick={event => {
      trigger.current = event.currentTarget; setError(''); setAction('restart');
    }}>Restart Onboarding</button>}
    {!controls?.delete_available && controls && <p>Permanent deletion is available for signed-in accounts on the production backend.</p>}
    {error && !action && <p role="alert">{error}</p>}
    {action && <div className="dialog-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="account-dialog-title" aria-describedby="account-dialog-description" className="exit-dialog account-dialog" onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key === 'Tab') {
        const elements = event.currentTarget.querySelectorAll<HTMLElement>('a[href], input:not(:disabled), button:not(:disabled)');
        if (!elements.length) { event.preventDefault(); return; }
        const first = elements[0], last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <h2 id="account-dialog-title">{action === 'delete' ? 'Permanently delete your account?' : 'Restart onboarding?'}</h2>
      <p id="account-dialog-description">{action === 'delete'
        ? 'This permanently deletes your account, profile, preferences, onboarding, lessons, attempts, and saved audio. You cannot undo this. Security logs, provider records, and existing backups may remain under their retention policies.'
        : controls?.reset_description}</p>
      {action === 'delete' && <p>Accounts protected by MFA need support-assisted deletion. Keep MFA enabled and contact <a href="mailto:robbie@audli.ai?subject=Audli%20account%20deletion">Audli support</a>. Never send your password or recovery codes.</p>}
      {action === 'restart' && <p>Finish your current lesson first. You can then test both spoken and typed preferred names again.</p>}
      <form onSubmit={event => { event.preventDefault(); void submit(); }}>
        {action === 'delete' && <><label htmlFor="delete-password">Confirm your current password</label><input autoFocus id="delete-password" type="password" autoComplete="current-password" required maxLength={1024} disabled={busy} value={password} onChange={event => setPassword(event.target.value)}/></>}
        <label htmlFor="account-confirmation">Type {expected} to confirm</label>
        <input autoFocus={action === 'restart'} id="account-confirmation" autoComplete="off" disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)}/>
        {error && <p role="alert">{error}</p>}
        <button className="primary" disabled={disabled || busy || confirmation !== expected || (action === 'delete' && !password)}>{busy ? 'Please wait…' : action === 'delete' ? 'Permanently delete account' : 'Confirm restart'}</button>
        <button type="button" className="secondary" disabled={busy} onClick={close}>Cancel</button>
      </form>
    </section></div>}
  </section>;
}
