import styles from './listening.module.css';
import type { Outcome } from '../../lib/listening-core.ts';

const TONE: Record<Outcome, string> = {
  correct: styles.pillOk, incorrect: styles.pillNo, pending: styles.pillWait, assessed: styles.pillOk, failed: styles.pillNo, skipped: styles.pillIdle, unanswered: styles.pillIdle,
};
const MARK: Record<Outcome, string> = { correct: '✓', incorrect: '✕', pending: '…', assessed: '✓', failed: '!', skipped: '–', unanswered: '' };

/** A state is always words plus a mark, never colour alone. Announced politely so a result arriving later does not interrupt. */
export function StatusPill({ outcome, label, live = false }: { outcome: Outcome; label: string; live?: boolean }) {
  return <span className={`${styles.pill} ${TONE[outcome]}`} {...(live ? { role: 'status', 'aria-live': 'polite' as const } : {})}>
    <span aria-hidden="true">{MARK[outcome]}</span> {label}
  </span>;
}
