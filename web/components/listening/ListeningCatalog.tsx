'use client';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import styles from './listening.module.css';
import { LessonShell } from './LessonShell';
import { AttemptRecovery } from './AttemptRecovery';
import { AudliMascot } from '../audli-mascot';
import { ApiError } from '../../lib/api.ts';
import { getProgress, listCatalog } from '../../lib/listening.ts';
import { lengthLabel, lessonAction, progressLine, SPOKEN_NOTE, statusLine, whenLabel, type CatalogItem, type Progress, type RecentItem } from '../../lib/listening-core.ts';

type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'unavailable' } | { kind: 'ready'; items: CatalogItem[]; progress: Progress | null };

const PILL: Record<CatalogItem['status'], string> = { new: styles.pillIdle, in_progress: styles.pillWait, completed: styles.pillOk };
const MARK: Record<CatalogItem['status'], string> = { new: '', in_progress: '…', completed: '✓' };

function Skeleton() {
  return <div role="status" aria-live="polite" aria-busy="true">
    <p className="sr-only" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Loading lessons…</p>
    <ul className={styles.catalog} aria-hidden="true">{[0, 1].map(n => <li key={n} className={`${styles.lessonCard} ${styles.skeleton}`}><span className={styles.skelLine} /><span className={`${styles.skelLine} ${styles.skelShort}`} /></li>)}</ul>
  </div>;
}

function LessonCard({ item }: { item: CatalogItem }) {
  const action = lessonAction(item);
  return <li>
    <Link className={styles.lessonCard} href={`/listening/${item.lesson_id}`}>
      <span className={styles.cardTop}>
        <span className={styles.heading} style={{ fontSize: 20 }}>{item.title}</span>
        <span className={`${styles.tag} ${styles.level}`}><span className="sr-only" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Level </span>{item.cefr}</span>
      </span>
      <span className={styles.cardMeta}>{lengthLabel(item.duration_ms)} of listening · {item.total || 4} questions</span>
      <span className={styles.cardBottom}>
        <span className={`${styles.pill} ${PILL[item.status]}`}><span aria-hidden="true">{MARK[item.status]}</span> {statusLine(item)}</span>
        <span className={styles.cardAction}>{action} <span aria-hidden="true">→</span></span>
      </span>
      {item.status !== 'new' && item.spoken_status ? <span className={styles.note}>{SPOKEN_NOTE[item.spoken_status]}</span> : null}
    </Link>
  </li>;
}

function Recent({ items }: { items: RecentItem[] }) {
  return <section aria-labelledby="recent-title" className={styles.panel}>
    <h2 id="recent-title" className={styles.kicker}>Recent activity</h2>
    <ul className={styles.recent}>
      {items.map(r => <li key={r.attempt_id}>
        <Link className={styles.recentRow} href={`/listening/${r.lesson_id}`}>
          <span><b>{r.title}</b><span className={styles.note}> · {whenLabel(r.when)}</span></span>
          <span className={styles.note}>
            {r.status === 'completed' ? (r.scored ? `${r.correct} of ${r.scored} correct` : 'Finished') : 'In progress'}
            {r.status === 'completed' && r.spoken_status ? ` · ${SPOKEN_NOTE[r.spoken_status].replace('Spoken answer: ', 'spoken answer ')}` : ''}
          </span>
        </Link>
      </li>)}
    </ul>
  </section>;
}

export function ListeningCatalog() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const fetchAll = useCallback(async (): Promise<Load> => {
    try {
      // Progress is a nicety: if only it fails, the library still works.
      const [items, progress] = await Promise.all([listCatalog(), getProgress().catch(() => null)]);
      return { kind: 'ready', items, progress };
    } catch (cause) {
      // While 2.0 is limited to named accounts the server answers 404 to everyone else: say so plainly instead of offering a retry that cannot help.
      return cause instanceof ApiError && cause.status === 404 ? { kind: 'unavailable' } : { kind: 'error' };
    }
  }, []);
  useEffect(() => {
    let live = true;
    void fetchAll().then(result => { if (live) setLoad(result); });
    return () => { live = false; };
  }, [fetchAll]);
  const retry = () => { setLoad({ kind: 'loading' }); void fetchAll().then(setLoad); };

  const shell = (children: React.ReactNode) => <LessonShell phase={null} closeHref="/" closeLabel="Back to Audli home">{children}</LessonShell>;
  if (load.kind === 'unavailable') return shell(<div className={styles.status} role="status"><div className={styles.heroMascot}><AudliMascot state="Idle" small /></div><p><b>Audli listening lessons aren’t available on your account yet.</b></p><Link className={styles.secondary} href="/">Back to Audli</Link></div>);
  if (load.kind === 'error') return shell(<AttemptRecovery state="error" onRetry={retry} />);
  if (load.kind === 'loading') return shell(<><h1 className={`${styles.heading} ${styles.title}`}>Listening lessons</h1><Skeleton /></>);
  const { items, progress } = load;
  return shell(<>
    <header className={styles.libHead}>
      <div className={styles.libMascot}><AudliMascot state="Idle" small /></div>
      <div>
        <h1 className={`${styles.heading} ${styles.title}`}>Listening lessons</h1>
        <p className={styles.note}>Listen first, then answer four questions.</p>
      </div>
    </header>
    {items.length === 0 ? <div className={styles.status} role="status">
      <div className={styles.heroMascot}><AudliMascot state="Thinking" small /></div>
      <p><b>No lessons yet.</b></p>
      <p className={styles.note}>New lessons will appear here. Check back soon.</p>
    </div> : <>
      {progress ? <section aria-labelledby="progress-title" className={styles.panel}>
        <h2 id="progress-title" className={styles.kicker}>Your progress</h2>
        <p className={styles.score}>{progressLine(progress)}</p>
        {progress.questions_scored > 0 ? <p className={styles.note}>{progress.questions_correct} of {progress.questions_scored} scored questions correct in your finished lessons. Spoken answers are not scored.</p> : null}
      </section> : null}
      <ul className={styles.catalog} aria-label="Lessons">{items.map(item => <LessonCard key={item.lesson_id} item={item} />)}</ul>
      {progress && progress.recent.length > 0 ? <Recent items={progress.recent} /> : null}
    </>}
  </>);
}
