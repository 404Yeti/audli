'use client';
import { formatRemaining, nextLessonLabel, spokenRemaining, type HomeView } from '../lib/eligibility';

/** Home's lesson area. `legacy` (off, observe, unknown) is exactly the original markup. */
export function HomeLesson({ view, nowMs, busy, ready, onStart, onReview, onPlan }: {
  view: HomeView; nowMs: number | null; busy: boolean; ready: boolean; onStart: () => void; onReview: () => void; onPlan: () => void;
}) {
  if (view.kind === 'legacy' || view.kind === 'ready') {
    return <>
      <h2>Today’s listening lesson</h2><p>10 minutes · English · adapted for you</p>
      <button className="primary" disabled={busy || !ready} onClick={onStart}>Start today’s session</button>
      {view.kind === 'ready' && <p className="home-note">Your 10-minute lesson starts when you begin and keeps running, even if you pause.</p>}
      <p className="home-support">Your next lesson will build from what Audli heard last time.</p>
    </>;
  }
  if (view.kind === 'resume') {
    const minutes = Math.max(1, Math.ceil(view.remainingMs / 60000));
    return <section className="lesson-resume" aria-labelledby="resume-title">
      <h2 id="resume-title">{view.closing ? 'Let’s wrap up today’s lesson' : 'Your lesson is waiting'}</h2>
      <p>{view.closing ? 'The 10 minutes are nearly done. Come finish the closing.' : `About ${minutes} ${minutes === 1 ? 'minute' : 'minutes'} left in today’s lesson window.`}</p>
      <p className="home-note">The 10 minutes keep running while you’re away, so it’s best to jump back in.</p>
      <button className="primary" disabled={busy || !ready} onClick={onStart}>{view.closing ? 'Finish today’s lesson' : 'Continue today’s lesson'}</button>
    </section>;
  }
  if (view.kind === 'checking') {
    return <section className="lesson-checking" aria-live="polite"><h2>Checking your next lesson…</h2><p role="status">One moment while Audli confirms.</p></section>;
  }
  return <section className="cooldown-card" aria-labelledby="cooldown-title" data-view="cooldown">
    <h2 id="cooldown-title">{view.expired ? 'Today’s lesson time has ended' : 'Today’s lesson is done. Nice work.'}</h2>
    <p className="cooldown-countdown" aria-hidden="true">{formatRemaining(view.remainingMs)}</p>
    <p className="cooldown-caption">until your next lesson</p>
    <p className="sr-only" role="status" aria-live="off">Your next lesson opens in {spokenRemaining(view.remainingMs)}.</p>
    <p>Audli is getting your next daily lesson ready. It opens {nextLessonLabel(view.nextAt, nowMs ?? Date.parse(view.nextAt) - view.remainingMs)}.</p>
    {view.expired && <p className="home-note">We saved what you did. It isn’t counted as a finished lesson, and you can review it any time.</p>}
    <div className="cooldown-actions">
      <button className="secondary" onClick={onReview}>Review what we covered</button>
      <button className="text-button" onClick={onPlan}>See your listening plan</button>
    </div>
  </section>;
}
