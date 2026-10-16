'use client';
import { useEffect, useId, useRef, useState } from 'react';

export function OnboardingAccountMenu({ onResume, onSignOut, disabled }: {
  onResume: () => void; onSignOut: () => Promise<void>; disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  useEffect(() => {
    if (!open) return;
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !container.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);
  return <div className="onboarding-account-menu" ref={container} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }} onKeyDown={event => {
    if (event.key === 'Escape' && open) {
      event.preventDefault(); setOpen(false); trigger.current?.focus();
    }
  }}>
    <button type="button" className="text-button" ref={trigger} aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}>Account</button>
    <div id={panelId} className="onboarding-account-panel" hidden={!open}>
      <button type="button" className="text-button" disabled={disabled} onClick={() => { setOpen(false); onResume(); }}>Resume onboarding</button>
      <button type="button" className="text-button" onClick={() => { setOpen(false); void onSignOut(); }}>Sign out</button>
    </div>
  </div>;
}
