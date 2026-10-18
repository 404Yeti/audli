'use client';
import { useCallback, useEffect, useRef } from 'react';
import { RiveMascot } from './rive-mascot';

/** Presentation only: lesson completion has already been saved. */
export function LessonCelebration({ completionId, scope, reducedMotion, onComplete }: {
  completionId: string; scope: string; reducedMotion: boolean; onComplete: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const completed = useRef(false);
  const latestComplete = useRef(onComplete);
  useEffect(() => { latestComplete.current = onComplete; }, [onComplete]);
  const finish = useCallback(() => {
    if (completed.current) return;
    completed.current = true;
    latestComplete.current();
  }, []);
  useEffect(() => {
    window.scrollTo(0, 0);
    heading.current?.focus({ preventScroll: true });
    // Covers failed imports, assets, initialization and missing completion events.
    const timer = setTimeout(finish, 15000);
    return () => clearTimeout(timer);
  }, [finish]);
  return <main className="lesson-celebration" aria-labelledby="celebration-title">
    <header><strong className="wordmark">audli</strong></header>
    <div className="celebration-content">
      <RiveMascot completionId={completionId} scope={scope} state="celebrate" reducedMotion={reducedMotion} onComplete={finish}/>
      <h1 id="celebration-title" ref={heading} tabIndex={-1}>Great job!</h1>
      <p>You’ve finished today’s listening lesson.</p>
      <button className="text-button" onClick={finish}>Continue to results</button>
    </div>
  </main>;
}
