'use client';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import styles from './success.module.css';
import { AudliMascot } from '../audli-mascot';
import { ApiError } from '../../lib/api.ts';
import { getAttempt, getProgress, listCatalog } from '../../lib/listening.ts';
import { hasPendingAssessment, pollDelayMs, type AttemptView, type CatalogItem, type Progress } from '../../lib/listening-core.ts';
import { accentLabel } from '../../lib/home-core.ts';
import { SPOKEN_SENTENCE, attemptParam, pickNext, successState } from '../../lib/success-core.ts';

type Load = { kind: 'loading' } | { kind: 'missing' } | { kind: 'error' } | { kind: 'ready'; view: AttemptView; progress: Progress | null; catalog: CatalogItem[] };

/** Figma D13/M13. A read-only view of one persisted attempt: it creates nothing, so a refresh, a second tab or the back button can never duplicate a result. */
export function LessonSuccess({ lessonId, attemptId }: { lessonId: string; attemptId?: string }) {
  const id = attemptParam(attemptId);
  const [load, setLoad] = useState<Load>(id ? { kind: 'loading' } : { kind: 'missing' });

  const fetchAll = useCallback(async (attempt: string): Promise<Load> => {
    try {
      const [view, progress, catalog] = await Promise.all([getAttempt(attempt), getProgress().catch(() => null), listCatalog().catch(() => [] as CatalogItem[])]);
      if (view.lesson.lesson_id !== lessonId) return { kind: 'missing' };          // an attempt id from another lesson is not this lesson's result
      return { kind: 'ready', view, progress, catalog };
    } catch (cause) { return cause instanceof ApiError && (cause.status === 404 || cause.status === 422) ? { kind: 'missing' } : { kind: 'error' }; }
  }, [lessonId]);

  useEffect(() => {
    if (!id) return;
    let live = true;
    void fetchAll(id).then(result => { if (live) setLoad(result); });
    return () => { live = false; };
  }, [id, fetchAll]);

  // The spoken answer is checked in the background: keep asking, gently, until it is settled.
  const pending = load.kind === 'ready' && hasPendingAssessment(load.view.exercises);
  useEffect(() => {
    if (!id || !pending) return;
    let live = true, polls = 0, timer: ReturnType<typeof setTimeout>;
    const tick = () => { timer = setTimeout(async () => {
      try { const view = await getAttempt(id); if (live) setLoad(prev => prev.kind === 'ready' ? { ...prev, view } : prev); } catch { /* keep what we have */ }
      polls += 1; if (live) tick();
    }, pollDelayMs(polls)); };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [id, pending]);

  const retry = () => { if (id) { setLoad({ kind: 'loading' }); void fetchAll(id).then(setLoad); } };

  if (load.kind === 'loading') return <main className={styles.page} aria-busy="true"><p role="status" className={styles.muted}>Loading your result…</p></main>;
  if (load.kind === 'error') return <main className={styles.page}><div className={styles.column}><h1 className={styles.title}>We couldn’t load your result</h1>
    <p className={styles.muted} role="alert">Check your connection and try again. Your answers are saved.</p>
    <button type="button" className={styles.primary} onClick={retry}>Try again</button><Link href="/listening" className={styles.ghost}>Back to lessons</Link></div></main>;
  if (load.kind === 'missing') return <main className={styles.page}><div className={styles.column}><h1 className={styles.title}>We couldn’t find that result</h1>
    <p className={styles.muted}>It may belong to another lesson or account, or the link may be incomplete.</p>
    <Link href="/listening" className={styles.primary}>Back to lessons</Link><Link href="/" className={styles.ghost}>Back to home</Link></div></main>;

  const state = successState(load.view, load.progress);
  if (state.kind === 'incomplete') return <main className={styles.page}><div className={styles.column}><h1 className={styles.title}>You haven’t finished this lesson yet</h1>
    <p className={styles.muted}>{state.answered} of {state.total} questions answered. Pick up where you left off.</p>
    <Link href={`/listening/${encodeURIComponent(lessonId)}`} className={styles.primary}>Continue the lesson</Link><Link href="/" className={styles.ghost}>Back to home</Link></div></main>;

  const s = state.summary;
  const next = pickNext(load.catalog, s.lessonId);
  const spokenLine = SPOKEN_SENTENCE[s.spoken];
  return <main className={styles.page} id="success">
    <div className={styles.column}>
      <div className={styles.mascot} aria-hidden="true"><AudliMascot state="Success" /></div>
      <h1 className={styles.title} tabIndex={-1} ref={el => el?.focus()}>Lesson complete</h1>
      <p className={styles.sub}>{s.title} <span className={styles.badge} aria-label={`Level ${s.cefr}`}>{s.cefr}</span></p>

      <section className={styles.card} aria-labelledby="result-h">
        <h2 id="result-h" className={styles.srOnly}>Your result</h2>
        <div className={styles.row}><span>Questions correct</span><strong data-testid="score">{s.correct} of {s.scored}</strong></div>
        <div className={styles.bar} role="progressbar" aria-valuemin={0} aria-valuemax={s.scored} aria-valuenow={s.correct} aria-label="Questions correct"><span style={{ width: `${s.scored ? (s.correct / s.scored) * 100 : 0}%` }} /></div>
        {s.lessonsFinished !== null && s.lessonsAvailable !== null
          ? <div className={styles.row}><span>Lessons finished</span><strong>{s.lessonsFinished} of {s.lessonsAvailable}</strong></div> : null}
        {spokenLine ? <p className={styles.spoken} role="status">{spokenLine}</p> : null}
      </section>

      <section aria-labelledby="done-h"><h2 id="done-h" className={styles.label}>What you practised</h2>
        <ul className={styles.tags}>{[...s.practised, `${accentLabel(s.accent)} accent`].map(t => <li key={t} className={styles.tag}>{t}</li>)}</ul></section>

      {next ? <section aria-labelledby="next-h"><h2 id="next-h" className={styles.label}>Up next</h2>
        <Link href={`/listening/${encodeURIComponent(next.lesson_id)}`} className={styles.next}>
          <span className={styles.nextText}><strong>{next.title}</strong><span className={styles.muted}>{next.cefr} · {next.accents.map(accentLabel).join(', ')} · {Math.max(1, Math.round(next.duration_ms / 60000))} min</span></span>
          <span aria-hidden="true">›</span></Link></section> : null}

      <div className={styles.actions}>
        {next ? <Link href={`/listening/${encodeURIComponent(next.lesson_id)}`} className={styles.primary}>Start next lesson</Link> : <Link href="/listening" className={styles.primary}>Choose another lesson</Link>}
        {s.mistakes > 0 ? <Link href={`/listening/${encodeURIComponent(s.lessonId)}`} className={styles.secondary}>Review my {s.mistakes === 1 ? 'mistake' : 'mistakes'} ({s.mistakes})</Link> : null}
        <Link href="/" className={styles.ghost}>Back to home</Link>
      </div>
    </div>
  </main>;
}
