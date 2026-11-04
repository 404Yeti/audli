'use client';
import { useState } from 'react';
import styles from './listening.module.css';
import { cleanGap, splitPrompt, type Exercise } from '../../lib/listening-core.ts';

/** Type the missing word. Scoring happens on the server; the accepted answer is revealed only after this answer is saved. */
export function FillBlankExercise({ exercise, busy, error, onSubmit }: { exercise: Exercise; busy: boolean; error: string | null; onSubmit: (text: string) => void }) {
  const [typed, setTyped] = useState('');
  const result = exercise.fill;
  const [before, after] = splitPrompt(exercise.prompt);
  const value = result ? result.answer : typed;
  const ready = cleanGap(typed).length > 0;
  return <form className={styles.card} onSubmit={event => { event.preventDefault(); if (ready && !result && !busy) onSubmit(cleanGap(typed)); }}>
    <p className={styles.kicker}>Question {exercise.position} · Fill in the blank</p>
    <p className={`${styles.heading} ${styles.prompt} ${styles.sentence}`}>
      {before}
      <input className={`${styles.gap} ${result ? (result.is_correct ? styles.gapOk : styles.gapNo) : ''}`} type="text" aria-label="Missing word" value={value}
        onChange={event => setTyped(event.target.value)} disabled={!!result || busy} maxLength={80} autoComplete="off" autoCapitalize="none" spellCheck={false} enterKeyHint="done" />
      {after}
    </p>
    {result ? <div className={`${styles.feedback} ${result.is_correct ? styles.feedbackOk : styles.feedbackNo}`} role="status">
      <strong>{result.is_correct ? 'Correct' : 'Not quite'}</strong>
      {result.is_correct ? null : <p>The missing word was <b>{result.accepted}</b>.</p>}
      <p>{result.explanation}</p>
    </div> : <>
      {error ? <p className={styles.playerMsg} role="alert">{error}</p> : null}
      <button type="submit" className={styles.primary} disabled={!ready || busy}>{busy ? 'Checking…' : 'Check answer'}</button>
    </>}
  </form>;
}
