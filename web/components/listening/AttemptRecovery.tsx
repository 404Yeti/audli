import { useEffect, useState } from 'react';
import styles from './listening.module.css';
import { AudliMascot } from '../audli-mascot';
import { failureMessage, type LoadFailure } from '../../lib/lesson-access.ts';

/** After a few seconds the learner is told Audli is still working (it is retrying a slow connection), so a wait never looks like a freeze. */
const SLOW_AFTER_MS = 8_000;
function Loading() {
  const [slow, setSlow] = useState(false);
  useEffect(() => { const timer = setTimeout(() => setSlow(true), SLOW_AFTER_MS); return () => clearTimeout(timer); }, []);
  return <div className={styles.status} role="status" aria-live="polite">
    <div className={styles.heroMascot}><AudliMascot state="Listening" small /></div>
    <p>Getting your lesson ready…</p>
    {slow ? <p className={styles.note}>This is taking longer than usual. Still trying, and your progress is safe.</p> : null}
  </div>;
}

/** Shown while a saved attempt is being found, when it cannot be loaded, and (as a banner) when one was picked back up. */
export function AttemptRecovery({ state, onRetry, failure }: { state: 'loading' | 'error'; onRetry?: () => void; failure?: LoadFailure }) {
  if (state === 'loading') return <Loading />;
  return <div className={`${styles.status} ${styles.statusError}`} role="alert">
    <div className={styles.heroMascot}><AudliMascot state="Retry" small /></div>
    <p>{failureMessage(failure?.kind ?? 'rejected').headline}</p>
    <p className={styles.note}>{failureMessage(failure?.kind ?? 'rejected').detail}</p>
    <button type="button" className={styles.primary} onClick={onRetry}>Try again</button>
    {failure ? <p className={styles.note}>Support reference: <code>{failure.reference}</code></p> : null}
  </div>;
}

export function ResumedBanner({ question, total }: { question?: number | null; total?: number }) {
  return <p className={styles.banner} role="status">{question && total ? `Welcome back — you’re on question ${question} of ${total}. Your answers are saved.` : 'Welcome back — we saved your place.'}</p>;
}
