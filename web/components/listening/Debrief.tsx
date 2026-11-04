'use client';
import { useState } from 'react';
import styles from './listening.module.css';
import { AudliMascot } from '../audli-mascot';
import { StatusPill } from './StatusPill';
import { SpokenExercise } from './SpokenExercise';
import { KIND_LABEL, OUTCOME_LABEL, POINT_LABEL, outcomeOf, scoreLine, type AttemptView, type Exercise } from '../../lib/listening-core.ts';

function Detail({ e }: { e: Exercise }) {
  const text = new Map(e.options.map(o => [o.id, o.text]));
  if (e.feedback) {
    const chosen = text.get(e.feedback.selected_option_id);
    return <>
      <p className={styles.said2}>Your answer: {chosen}</p>
      {e.feedback.is_correct ? null : <p className={styles.said2}>Correct answer: {text.get(e.feedback.correct_option_id)}</p>}
      {e.feedback.is_correct ? null : <p className={styles.why}>{e.feedback.explanation}</p>}
    </>;
  }
  if (e.fill) return <>
    <p className={styles.said2}>Your answer: {e.fill.answer}</p>
    {e.fill.is_correct ? null : <><p className={styles.said2}>Missing word: {e.fill.accepted}</p><p className={styles.why}>{e.fill.explanation}</p></>}
  </>;
  if (e.sequence) return e.sequence.is_correct ? null : <>
    <p className={styles.said2}>{e.sequence.positions_correct} of {e.sequence.order.length} were in the right place.</p>
    <p className={styles.said2}>Correct order: {e.sequence.correct_order.map(id => text.get(id)).join(' → ')}</p>
    <p className={styles.why}>{e.sequence.explanation}</p>
  </>;
  return null;
}

/** The end of the lesson: what actually happened to each question, including answers that are still being checked or could not be. */
export function Debrief({ view, busy, error, onCheckAgain, onSend }: {
  view: AttemptView; busy: boolean; error: string | null; onCheckAgain: (exerciseId: string) => void; onSend: (blob: Blob) => void;
}) {
  const [recording, setRecording] = useState(false);
  const spokenQ = view.exercises.find(e => e.kind === 'spoken');
  const spoken = spokenQ?.spoken ?? null;
  const scored = view.exercises.filter(e => e.kind !== 'spoken');
  const allRight = scored.length > 0 && scored.every(e => outcomeOf(e) === 'correct');
  return <div className={styles.debrief}>
    <section className={styles.result} aria-labelledby="debrief-title">
      <div className={styles.resultMascot}><AudliMascot state={allRight ? 'Success' : 'Idle'} small /></div>
      <h2 id="debrief-title" className={styles.heading} style={{ fontSize: 24 }}>{view.lesson.title}</h2>
      <p className={styles.score}>{scoreLine(view)}</p>
      {spoken ? <StatusPill outcome={outcomeOf(spokenQ!)} live label={spoken.status === 'pending' ? 'Spoken answer: being checked' : `Spoken answer: ${OUTCOME_LABEL[outcomeOf(spokenQ!)].toLowerCase()}`} /> : null}
    </section>

    <section className={styles.card} aria-labelledby="answers-title">
      <h3 id="answers-title" className={styles.kicker}>Your answers</h3>
      <ol className={styles.review}>
        {view.exercises.map(e => <li key={e.id} className={styles.reviewItem}>
          <div className={styles.reviewHead}>
            <span><span className={styles.kicker}>{e.position}. {KIND_LABEL[e.kind]}</span><span className={styles.reviewPrompt}>{e.prompt.replace('___', '…')}</span></span>
            <StatusPill outcome={outcomeOf(e)} label={OUTCOME_LABEL[outcomeOf(e)]} />
          </div>
          <Detail e={e} />
        </li>)}
      </ol>
    </section>

    {spokenQ && spoken ? <section className={styles.card} aria-labelledby="spoken-title">
      <h3 id="spoken-title" className={styles.kicker}>Your spoken answer</h3>
      {spoken.status === 'pending' ? <p role="status" aria-live="polite">We’re checking your answer. This page updates on its own — you can leave and come back.</p> : null}
      {spoken.status === 'skipped' ? <p>You skipped this question, so it isn’t counted either way.</p> : null}
      {spoken.status === 'assessed' && spoken.insufficient ? <p>We couldn’t hear enough in that answer to check it, so it isn’t counted either way.</p> : null}
      {spoken.transcript ? <div><p className={styles.kicker}>What Audli heard</p><blockquote className={styles.quote}>{spoken.transcript}</blockquote></div> : null}
      {spoken.status === 'assessed' && spoken.points ? <>
        <p className={styles.note}>{spoken.covered} of {spoken.total_points} key ideas clearly said{spoken.partly ? `, ${spoken.partly} partly` : ''}. This is feedback on your answer, not a grade.</p>
        <ul className={styles.points}>
          {spoken.points.map(p => <li key={p.id} className={styles.point} data-state={p.state}>
            <span className={styles.pointState}>{POINT_LABEL[p.state]}</span>
            <span>{p.text}</span>
            {p.quote ? <q className={styles.evidence}>{p.quote}</q> : null}
          </li>)}
        </ul>
      </> : null}
      {spoken.status === 'failed' ? <div role="alert" className={styles.failed}>
        <p>We couldn’t check your spoken answer this time.{spoken.transcript ? ' Your words are saved.' : ''}</p>
        {spoken.retryable ? (spoken.transcript
          ? <button type="button" className={styles.primary} disabled={busy} onClick={() => onCheckAgain(spokenQ.id)}>{busy ? 'Starting…' : 'Check again'}</button>
          : <button type="button" className={styles.primary} disabled={busy} onClick={() => setRecording(true)}>Record again</button>)
          : <p className={styles.note}>That was the last try for this question, so it isn’t counted.</p>}
      </div> : null}
      {error ? <p className={styles.playerMsg} role="alert">{error}</p> : null}
    </section> : null}

    {recording && spokenQ ? <SpokenExercise exercise={{ ...spokenQ, spoken: null }} busy={busy} error={null} onSend={blob => { setRecording(false); onSend(blob); }} /> : null}
  </div>;
}
