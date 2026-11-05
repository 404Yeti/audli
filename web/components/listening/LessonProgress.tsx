import styles from './listening.module.css';
import { STEPS, stepIndex, type Phase } from '../../lib/listening-core.ts';

/** Three saved-state steps: Listen, Answer, Result. Text and aria carry the state, not colour alone. */
export function LessonProgress({ phase }: { phase: Phase }) {
  const current = stepIndex(phase);
  return <>
    <ol className={styles.segments} aria-label="Lesson progress">
      {STEPS.map((step, i) => <li key={step} aria-current={i === current ? 'step' : undefined}
        className={`${styles.segment} ${i < current ? styles.segmentDone : i === current ? styles.segmentCurrent : ''}`}>
        <span className="sr-only" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{step}{i < current ? ' done' : i === current ? ' current' : ''}</span>
      </li>)}
    </ol>
    <span className={styles.stepLabel} aria-hidden="true">{current + 1}/{STEPS.length}</span>
  </>;
}
