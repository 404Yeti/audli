import Link from 'next/link';
import type { ReactNode } from 'react';
import styles from './listening.module.css';
import { LessonProgress } from './LessonProgress';
import type { Phase } from '../../lib/listening-core.ts';

/** Top bar, progress and the page frame. Actions render in a sticky bar so the primary button stays reachable on a phone. */
export function LessonShell({ phase, wide = false, actions, children }: { phase: Phase | null; wide?: boolean; actions?: ReactNode; children: ReactNode }) {
  return <div className={styles.root}>
    <header className={styles.top}>
      <div className={styles.topInner}>
        <Link href="/listening" className={styles.close} aria-label="Leave lesson">×</Link>
        {phase ? <LessonProgress phase={phase} /> : <span style={{ flex: 1 }} />}
      </div>
    </header>
    <main className={`${styles.main} ${wide ? styles.mainWide : ''}`}>{children}</main>
    {actions ? <div className={styles.actions}><div className={styles.actionsInner}>{actions}</div></div> : null}
  </div>;
}
