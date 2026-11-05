'use client';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import styles from './app.module.css';
import { AppShell } from './AppShell';
import { AttemptRecovery } from './AttemptRecovery';
import { AudliMascot } from '../audli-mascot';
import { Icon } from './Icons';
import { ApiError } from '../../lib/api.ts';
import { getProgress } from '../../lib/listening.ts';
import { progressLine, SPOKEN_NOTE, whenLabel, type Progress } from '../../lib/listening-core.ts';
import { accentLabel, accentRows, levelRows } from '../../lib/home-core.ts';

type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'unavailable' } | { kind: 'ready'; progress: Progress };

/** Figma 14 Progress (D14 / M14). Facts only: finished lessons, scored answers, accents heard and levels practised. No streaks, scores for speaking, or mastery claims. */
export function ProgressPage() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const fetchAll = useCallback(async (): Promise<Load> => {
    try { return { kind: 'ready', progress: await getProgress() }; }
    catch (cause) { return cause instanceof ApiError && cause.status === 404 ? { kind: 'unavailable' } : { kind: 'error' }; }
  }, []);
  useEffect(() => { let live = true; void fetchAll().then(r => { if (live) setLoad(r); }); return () => { live = false; }; }, [fetchAll]);

  if (load.kind === 'unavailable') return <AppShell active="progress"><div className={styles.status} role="status"><div style={{ width: 96 }}><AudliMascot small face /></div>
    <p><b>Audli listening lessons aren’t available on your account yet.</b></p><Link className={`${styles.btn} ${styles.btnSecondary}`} href="/">Back to Audli</Link></div></AppShell>;
  if (load.kind === 'error') return <AppShell active="progress"><AttemptRecovery state="error" onRetry={() => { setLoad({ kind: 'loading' }); void fetchAll().then(setLoad); }} /></AppShell>;
  if (load.kind === 'loading') return <AppShell active="progress"><h1 className={`${styles.display} ${styles.h1}`}>Your progress</h1>
    <div role="status" aria-live="polite" aria-busy="true"><p className={styles.srOnly}>Loading your progress…</p><div className={styles.skeleton} style={{ minHeight: 160, marginTop: 20 }} /></div></AppShell>;

  const p = load.progress;
  const levels = levelRows(p);
  const accents = accentRows(p);
  const maxAccent = Math.max(1, ...accents.map(a => a.count));
  const recent = p.recent;
  return <AppShell active="progress">
    <h1 className={`${styles.display} ${styles.h1}`}>Your progress</h1>
    <p className={styles.muted} style={{ margin: '4px 0 20px', maxWidth: 560 }}>Listen first, then answer. This page shows what you finished and which questions you got right.</p>
    {p.attempts_completed === 0 ? <div className={styles.status} role="status"><div style={{ width: 96 }}><AudliMascot state="Idle" small face /></div><p><b>Nothing finished yet. Pick a lesson to begin.</b></p>
      <Link className={`${styles.btn} ${styles.btnPrimary}`} href="/listening">Find a lesson</Link></div> : <div className={styles.pairs}>
      <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
        <section className={styles.card} aria-labelledby="summary-title"><h2 id="summary-title" className={`${styles.display} ${styles.h3}`}>Your progress</h2>
          <p style={{ fontWeight: 800, fontSize: 20, margin: '8px 0 0' }}>{progressLine(p)}</p>
          {p.questions_scored > 0 ? <p className={styles.muted} style={{ margin: '4px 0 0' }}>{p.questions_correct} of {p.questions_scored} scored questions correct in your finished lessons. Spoken answers are not scored.</p> : null}</section>
        <section className={styles.card} aria-labelledby="levels-title"><h2 id="levels-title" className={`${styles.display} ${styles.h3}`}>Levels practised</h2>
          <div className={styles.levels} style={{ marginTop: 12 }}>{levels.map(l => <div key={l.level} className={styles.level}><span className={`${styles.levelBar} ${l.count ? styles.levelHas : ''}`} /><span>{l.level}</span>
            <span className={styles.muted} style={{ fontSize: 12 }}>{l.count}</span></div>)}</div>
          <p className={styles.muted} style={{ margin: '12px 0 0', fontSize: 14 }}>Lessons finished at each level.</p></section>
        <section aria-labelledby="recent-title"><div className={styles.sectionHead}><h2 id="recent-title" className={`${styles.display} ${styles.h2}`}>Recent activity</h2></div>
          <ul className={styles.list}>{recent.map((r, n) => <li key={r.attempt_id}><Link className={styles.row} href={`/listening/${r.lesson_id}`}>
            <span className={`${styles.rowIcon} ${n % 2 ? styles.c2 : styles.c1}`}><Icon name={r.status === 'completed' ? 'check' : 'headphones'} /></span>
            <span className={styles.rowBody}><p className={styles.rowTitle}>{r.title} · {whenLabel(r.when)}</p>
              <p className={styles.rowMeta}>{r.status === 'completed' ? (r.scored ? `${r.correct} of ${r.scored} correct` : 'Finished') : 'In progress'}
                {r.status === 'completed' && r.spoken_status ? ` · ${SPOKEN_NOTE[r.spoken_status].replace('Spoken answer: ', 'spoken answer ')}` : ''}</p></span>
            <span className={styles.chev}><Icon name="chevron" /></span></Link></li>)}</ul></section>
      </div>
      <aside style={{ display: 'grid', gap: 16, alignContent: 'start' }} aria-label="Accents">
        <section className={styles.card} aria-labelledby="accent-title"><h2 id="accent-title" className={`${styles.display} ${styles.h3}`}>Accent exposure</h2>
          <p className={styles.muted} style={{ margin: '4px 0 8px', fontSize: 14 }}>Lessons finished per accent</p>
          {accents.map(a => <div key={a.accent} className={styles.metric}><div className={styles.metricHead}><span>{accentLabel(a.accent)}</span><span>{a.count}</span></div>
            <div className={styles.bar}><div className={`${styles.barFill} ${styles.barSun}`} style={{ width: `${Math.round((a.count / maxAccent) * 100)}%` }} /></div></div>)}</section>
      </aside></div>}
  </AppShell>;
}
