'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AudliMascot, type MascotState } from './audli-mascot';
import { minutesRemaining, SESSION_DURATION_MS } from '../lib/hands-free';

export type Destination = 'Home' | 'Review' | 'Plan' | 'Settings';
const icons: Record<Destination, string> = {
  Home: 'M3 10 12 3l9 7v10h-6v-7H9v7H3Z',
  Review: 'M4 4h16v16H4ZM8 9h8M8 13h8M8 17h5',
  Plan: 'M6 3v18M6 5h14l-3 4 3 4H6',
  Settings: 'M4 7h16M4 17h16M9 4v6M15 14v6',
};
export function BottomNavigation({ destination, onNavigate }: { destination: Destination; onNavigate: (value: Destination) => void }) {
  return <nav className="bottom-nav" aria-label="Main navigation">{(['Home','Review','Plan','Settings'] as const).map(name => <button key={name} onClick={() => onNavigate(name)} aria-current={destination === name ? 'page' : undefined}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true"><path d={icons[name]}/></svg><span>{name}</span></button>)}</nav>;
}
export function SessionCountdown({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  return <p className="session-countdown" aria-label="Session time remaining">{now - startedAt >= SESSION_DURATION_MS ? 'Finishing this conversation' : `${minutesRemaining(startedAt, now)} min left`}</p>;
}
export function SessionShell({ state, activity, label, startedAt, onEnd, onReplay, children }: { state: MascotState; activity: boolean; label?: string; startedAt?: number; onEnd: () => void; onReplay?: () => void; children?: ReactNode }) {
  const [confirmExit, setConfirmExit] = useState(false);
  const endButton = useRef<HTMLButtonElement | null>(null);
  function stay() { setConfirmExit(false); endButton.current?.focus(); }
  return <main className="session-shell"><header><strong className="wordmark">audli</strong><button ref={endButton} className="text-button" onClick={() => setConfirmExit(true)}>End</button></header>
    <div className="session-presence"><AudliMascot state={state} activity={activity}/><p className="state-label" role="status">{label ?? ({ Idle: 'Train your ears.', Speaking: 'Audli is speaking', Listening: 'Listening to you', Thinking: 'Thinking', Success: 'Nice work.', Retry: 'Let’s try that again.' })[state]}</p></div>
    <div className="session-secondary">{children}</div>
    <div className="session-bottom">{startedAt != null && <SessionCountdown startedAt={startedAt}/>}<div className="session-controls">{onReplay && <button className="text-button" onClick={onReplay}>↻ Replay</button>}</div></div>
    {confirmExit && <div className="dialog-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="exit-title" className="exit-dialog" onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); stay(); }
      if (event.key === 'Tab') {
        const buttons = event.currentTarget.querySelectorAll('button');
        if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons[1].focus(); }
        else if (!event.shiftKey && document.activeElement === buttons[1]) { event.preventDefault(); buttons[0].focus(); }
      }
    }}><h2 id="exit-title">End this conversation?</h2><p>Your saved progress will be here when you return.</p><button autoFocus className="primary" onClick={() => { setConfirmExit(false); onEnd(); }}>End session</button><button className="secondary" onClick={stay}>Stay here</button></section></div>}
  </main>;
}
