'use client';
import { useState } from 'react';
import styles from './listening.module.css';
import { optionState, type Exercise, type OptionState } from '../../lib/listening-core.ts';

const CLASS: Record<OptionState, string> = {
  idle: '', selected: styles.optSelected, correct: styles.optCorrect, incorrect: styles.optIncorrect, dimmed: styles.optDimmed,
};
const LABEL: Partial<Record<OptionState, string>> = { correct: 'Correct answer', incorrect: 'Your answer' };
const MARK: Partial<Record<OptionState, string>> = { selected: '•', correct: '✓', incorrect: '✕' };

/** One question. Answers are final: after feedback arrives the choices lock and the correct one is shown. */
export function MultipleChoiceExercise({ exercise, busy, error, onSubmit }: {
  exercise: Exercise; busy: boolean; error: string | null; onSubmit: (optionId: string) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const feedback = exercise.feedback;
  const choice = feedback ? feedback.selected_option_id : selected;
  return <form className={styles.card} onSubmit={event => { event.preventDefault(); if (selected && !feedback && !busy) onSubmit(selected); }}>
    <p className={styles.kicker}>Question {exercise.position}</p>
    <fieldset className={styles.options} disabled={!!feedback || busy}>
      <legend className={`${styles.heading} ${styles.prompt}`} style={{ marginBottom: 12 }}>{exercise.prompt}</legend>
      {exercise.options.map(option => {
        const state = optionState(option.id, choice, feedback);
        return <label key={option.id} className={`${styles.option} ${CLASS[state]}`}>
          <input type="radio" name={`q-${exercise.id}`} value={option.id} checked={choice === option.id}
            onChange={() => setSelected(option.id)} />
          <span className={styles.mark} aria-hidden="true">{MARK[state] ?? ''}</span>
          <span>{option.text}</span>
          {LABEL[state] ? <span className={styles.tagInline}>{LABEL[state]}</span> : null}
        </label>;
      })}
    </fieldset>
    {feedback ? <div className={`${styles.feedback} ${feedback.is_correct ? styles.feedbackOk : styles.feedbackNo}`} role="status">
      <strong>{feedback.is_correct ? 'Correct' : 'Not quite'}</strong>
      <p>{feedback.explanation}</p>
    </div> : <>
      {error ? <p className={styles.playerMsg} role="alert">{error}</p> : null}
      <button type="submit" className={styles.primary} disabled={!selected || busy}>{busy ? 'Checking…' : 'Check answer'}</button>
    </>}
  </form>;
}
