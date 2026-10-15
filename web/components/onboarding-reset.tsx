'use client';
import { useEffect, useRef, useState } from 'react';
import { LessonLifetime, type LessonOperation } from '../lib/api';
import { useAccount } from './auth-gate';

type Controls = { restart_available: boolean; reset_description?: string; onboarding_revision?: number };
export function OnboardingReset({ onConfirm, onRestart }: { onConfirm: () => void; onRestart: (operation: LessonOperation) => Promise<void> }) {
  const account = useAccount();
  const lifetime = useRef<LessonLifetime | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const [controls, setControls] = useState<Controls | null>(null);
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const owner = new LessonLifetime(); lifetime.current = owner;
    const operation = owner.begin();
    void operation.api<Controls>('/onboarding/reset-controls').then(value => {
      operation.assertCurrent(); setControls(value);
    }).catch(() => { /* Unavailable capabilities stay hidden. */ }).finally(() => operation.release());
    return () => owner.close();
  }, []);
  function close() {
    if (busy) return;
    setOpen(false); setConfirmation(''); setError(''); trigger.current?.focus();
  }
  async function submit() {
    if (!lifetime.current?.current || !open || !controls?.restart_available || busy
        || !Number.isInteger(controls.onboarding_revision) || confirmation !== 'RESTART ONBOARDING') return;
    onConfirm();
    const operation = lifetime.current.begin(); setBusy(true); setError('');
    try {
      const result = await operation.api<{ destination: string; revision: number }>('/onboarding/restart', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation, revision: controls.onboarding_revision }),
      });
      operation.assertCurrent();
      if (result.destination !== 'onboarding' || result.revision !== (controls.onboarding_revision as number) + 1)
        throw new Error('Restart could not be confirmed. Reload before trying again.');
      try { sessionStorage.removeItem('audli-session:' + account.scope); } catch { /* Optional local timing. */ }
      await onRestart(operation);
    } catch (cause) { if (operation.current) setError((cause as Error).message); }
    finally { if (operation.current) setBusy(false); operation.release(); }
  }
  if (!controls?.restart_available || !Number.isInteger(controls.onboarding_revision) || !controls.reset_description) return null;
  return <section aria-label="Restart onboarding">
    <button className="secondary" disabled={busy} onClick={event => {
      trigger.current = event.currentTarget; setError(''); setOpen(true);
    }}>Restart Onboarding</button>
    {open && <div className="dialog-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="reset-dialog-title" aria-describedby="reset-dialog-description" className="exit-dialog onboarding-reset-dialog" onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key === 'Tab') {
        const elements = event.currentTarget.querySelectorAll<HTMLElement>('input:not(:disabled), button:not(:disabled)');
        if (!elements.length) { event.preventDefault(); return; }
        const first = elements[0], last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <h2 id="reset-dialog-title">Restart onboarding?</h2>
      <p id="reset-dialog-description">{"You'll start Audli's setup again. If you're in the middle of a lesson, that lesson will end and any unfinished progress may be lost. Your completed lessons and learning history will stay saved."}</p>
      <form onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label htmlFor="reset-confirmation">Type RESTART ONBOARDING to confirm</label>
        <input autoFocus id="reset-confirmation" autoComplete="off" disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)}/>
        {error && <p role="alert">{error}</p>}
        <button className="primary" disabled={busy || confirmation !== 'RESTART ONBOARDING'}>{busy ? 'Please wait…' : 'Restart onboarding'}</button>
        <button type="button" className="secondary" disabled={busy} onClick={close}>Cancel</button>
      </form>
    </section></div>}
  </section>;
}
