import styles from './listening.module.css';
import { AudliMascot } from '../audli-mascot';

/** Shown while a saved attempt is being found, when it cannot be loaded, and (as a banner) when one was picked back up. */
export function AttemptRecovery({ state, onRetry }: { state: 'loading' | 'error'; onRetry?: () => void }) {
  if (state === 'loading') return <div className={styles.status} role="status" aria-live="polite">
    <div className={styles.heroMascot}><AudliMascot state="Listening" small /></div>
    <p>Getting your lesson ready…</p>
  </div>;
  return <div className={`${styles.status} ${styles.statusError}`} role="alert">
    <div className={styles.heroMascot}><AudliMascot state="Retry" small /></div>
    <p>We couldn’t load your lesson. Your progress is safe.</p>
    <button type="button" className={styles.primary} onClick={onRetry}>Try again</button>
  </div>;
}

export function ResumedBanner({ question, total }: { question?: number | null; total?: number }) {
  return <p className={styles.banner} role="status">{question && total ? `Welcome back — you’re on question ${question} of ${total}. Your answers are saved.` : 'Welcome back — we saved your place.'}</p>;
}
