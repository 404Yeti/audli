'use client';
import { useEffect, useRef } from 'react';
import { AudliMascot, type MascotState } from './audli-mascot';

const screens: { label: string; title: string; description: string; state: MascotState }[] = [
  { label: 'Listen', title: 'Train your ears for real English.', description: 'Listen to short stories and conversations designed for your level.', state: 'Listening' },
  { label: 'Respond', title: 'Tell Audli what you understood.', description: 'Answer naturally with your voice. No multiple-choice tests required.', state: 'Speaking' },
  { label: 'Improve', title: 'Get better with every lesson.', description: 'Audli helps you understand your strengths and creates personalized listening practice.', state: 'Success' },
];
export type WalkthroughStep = 0 | 1 | 2 | 'complete' | 'skipped';
export function WelcomeWalkthrough({ step, busy, error, onNavigate }: {
  step: 0 | 1 | 2; busy: boolean; error: string; onNavigate: (step: WalkthroughStep) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const touch = useRef<{ x: number; y: number } | null>(null);
  const previous = useRef(step);
  useEffect(() => {
    if (previous.current !== step) heading.current?.focus();
    previous.current = step;
  }, [step]);
  const screen = screens[step];
  function move(delta: number) {
    if (!busy && step + delta >= 0 && step + delta <= 2) onNavigate((step + delta) as 0 | 1 | 2);
  }
  return <main className="welcome-walkthrough" aria-label="Welcome to Audli">
    <header><strong className="wordmark">audli</strong><button className="walkthrough-skip" disabled={busy} onClick={() => onNavigate('skipped')}>Skip</button></header>
    <section className="walkthrough-content" aria-roledescription="carousel" aria-label="How Audli works"
      onTouchStart={event => { const point = event.touches[0]; touch.current = event.touches.length === 1 ? { x: point.clientX, y: point.clientY } : null; }}
      onTouchCancel={() => { touch.current = null; }}
      onTouchEnd={event => {
        const origin = touch.current; touch.current = null;
        const point = event.changedTouches[0];
        if (!origin || !point) return;
        const dx = point.clientX - origin.x, dy = point.clientY - origin.y;
        if (Math.abs(dx) >= 50 && Math.abs(dx) > Math.abs(dy) * 1.5) move(dx < 0 ? 1 : -1);
      }}>
      <div key={step} className="walkthrough-slide" role="group" aria-roledescription="slide" aria-label={`${step + 1} of 3: ${screen.label}`}>
        <div className={`walkthrough-illustration walkthrough-${step}`}><AudliMascot state={screen.state}/></div>
        <p className="walkthrough-label">{screen.label}</p>
        <h1 ref={heading} tabIndex={-1}>{screen.title}</h1>
        <p className="walkthrough-description">{screen.description}</p>
      </div>
    </section>
    <footer className="walkthrough-footer">
      <div className="walkthrough-indicators" aria-label={`Step ${step + 1} of 3`}>
        {screens.map((item, index) => <button key={item.label} aria-label={`Show ${item.label}, step ${index + 1} of 3`} aria-current={index === step ? 'step' : undefined} disabled={busy} onClick={() => onNavigate(index as 0 | 1 | 2)}><span/></button>)}
      </div>
      {error && <p role="alert" className="walkthrough-error">{error}</p>}
      <div className="walkthrough-actions">
        {step > 0 && <button className="secondary" disabled={busy} onClick={() => move(-1)}>Back</button>}
        <button className="primary" disabled={busy} onClick={() => step === 2 ? onNavigate('complete') : move(1)}>{busy ? 'Saving…' : step === 2 ? "Let's get started" : 'Next'}</button>
      </div>
      <p className="walkthrough-note">Eyes optional. Ears essential.</p>
    </footer>
  </main>;
}
