'use client';
import { useState } from 'react';
import styles from './listening.module.css';
import { moveItem, type Exercise } from '../../lib/listening-core.ts';

/** Put the items in order with plain buttons (works with a keyboard, a screen reader and a thumb). Order is checked on the server. */
export function SequencingExercise({ exercise, busy, error, onSubmit }: { exercise: Exercise; busy: boolean; error: string | null; onSubmit: (order: string[]) => void }) {
  const text = new Map(exercise.options.map(o => [o.id, o.text]));
  const [order, setOrder] = useState(() => exercise.options.map(o => o.id));
  const [announce, setAnnounce] = useState('');
  const result = exercise.sequence;
  const shown = result ? result.order : order;
  const move = (index: number, delta: -1 | 1) => {
    const next = moveItem(order, index, delta);
    if (next === order) return;
    setOrder(next);
    setAnnounce(`${text.get(order[index])} moved to position ${index + delta + 1} of ${order.length}`);
  };
  return <form className={styles.card} onSubmit={event => { event.preventDefault(); if (!result && !busy) onSubmit(order); }}>
    <p className={styles.kicker}>Question {exercise.position} · Put in order</p>
    <h2 className={`${styles.heading} ${styles.prompt}`}>{exercise.prompt}</h2>
    <ol className={styles.seq} aria-label="Your order, first to last">
      {shown.map((id, index) => {
        const right = result ? result.correct_order[index] === id : null;
        return <li key={id} className={`${styles.seqItem} ${right === true ? styles.optCorrect : right === false ? styles.optIncorrect : ''}`}>
          <span className={styles.seqNo} aria-hidden="true">{index + 1}</span>
          <span className={styles.seqText}>{text.get(id)}</span>
          {result ? <span className={styles.tagInline}>{right ? 'In place' : 'Misplaced'}</span> : <span className={styles.seqMoves}>
            <button type="button" className={styles.mover} onClick={() => move(index, -1)} disabled={index === 0 || busy} aria-label={`Move “${text.get(id)}” up`}>↑</button>
            <button type="button" className={styles.mover} onClick={() => move(index, 1)} disabled={index === shown.length - 1 || busy} aria-label={`Move “${text.get(id)}” down`}>↓</button>
          </span>}
        </li>;
      })}
    </ol>
    <p className="sr-only" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }} role="status" aria-live="polite">{announce}</p>
    {result ? <div className={`${styles.feedback} ${result.is_correct ? styles.feedbackOk : styles.feedbackNo}`} role="status">
      <strong>{result.is_correct ? 'Correct order' : `Not quite — ${result.positions_correct} of ${result.order.length} in the right place`}</strong>
      {result.is_correct ? null : <>
        <p>The order was:</p>
        <ol className={styles.answerOrder}>{result.correct_order.map(id => <li key={id}>{text.get(id)}</li>)}</ol>
      </>}
      <p>{result.explanation}</p>
    </div> : <>
      {error ? <p className={styles.playerMsg} role="alert">{error}</p> : null}
      <button type="submit" className={styles.primary} disabled={busy}>{busy ? 'Checking…' : 'Check order'}</button>
    </>}
  </form>;
}
