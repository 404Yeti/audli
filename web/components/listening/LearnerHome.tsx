'use client';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import styles from './app.module.css';
import { AppShell } from './AppShell';
import { AttemptRecovery } from './AttemptRecovery';
import { AudliMascot } from '../audli-mascot';
import { Icon } from './Icons';
import { ApiError } from '../../lib/api.ts';
import { getProgress, listCatalog } from '../../lib/listening.ts';
import { lengthLabel, whenLabel, type CatalogItem, type Progress } from '../../lib/listening-core.ts';
import { accentLabel, continueItem, countLabel, greeting, recentlyCompleted, recommendedNext, stepsDone } from '../../lib/home-core.ts';

const GENERATOR = process.env.NEXT_PUBLIC_AUDLI_LISTENING_GENERATOR === 'true';
type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'unavailable' } | { kind: 'ready'; items: CatalogItem[]; progress: Progress | null };

const meta = (i: CatalogItem) => [i.cefr, i.accents[0] ? accentLabel(i.accents[0]) : null, lengthLabel(i.duration_ms)].filter(Boolean).join(' · ');

/** Figma 02 Learner Home (D02 / M02): a Continue card, what you finished, one recommendation, and where you are in the library. */
export function LearnerHome() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const fetchAll = useCallback(async (): Promise<Load> => {
    try {
      const [items, progress] = await Promise.all([listCatalog(), getProgress().catch(() => null)]);
      return { kind: 'ready', items, progress };
    } catch (cause) { return cause instanceof ApiError && cause.status === 404 ? { kind: 'unavailable' } : { kind: 'error' }; }
  }, []);
  useEffect(() => { let live = true; void fetchAll().then(r => { if (live) setLoad(r); }); return () => { live = false; }; }, [fetchAll]);
  const [hour] = useState(() => new Date().getHours());

  if (load.kind === 'unavailable') return <AppShell active="home"><div className={styles.status} role="status"><div style={{ width: 96 }}><AudliMascot small face /></div>
    <p><b>Audli listening lessons aren’t available on your account yet.</b></p></div></AppShell>;
  if (load.kind === 'error') return <AppShell active="home"><AttemptRecovery state="error" onRetry={() => { setLoad({ kind: 'loading' }); void fetchAll().then(setLoad); }} /></AppShell>;
  if (load.kind === 'loading') return <AppShell active="home"><h1 className={`${styles.display} ${styles.h1}`}>{greeting(hour)}</h1>
    <div role="status" aria-live="polite" aria-busy="true"><p className={styles.srOnly}>Loading your lessons…</p><div className={styles.skeleton} style={{ minHeight: 200, marginTop: 20 }} /></div></AppShell>;

  const { items, progress } = load;
  const resume = continueItem(items);
  const next = recommendedNext(items, progress?.recent ?? []);
  const done = recentlyCompleted(progress);
  const finished = progress?.lessons_completed ?? 0;
  const total = progress?.lessons_available ?? items.length;
  const steps = resume ? stepsDone(resume) : null;

  return <AppShell active="home">
    <h1 className={`${styles.display} ${styles.h1}`} style={{ marginBottom: 20 }}>{greeting(hour)}</h1>
    <div className={styles.twoCol}>
      <div>
        <section className={`${styles.card} ${styles.continue}`} aria-labelledby="continue-title">
          <div className={styles.continueMascot}><AudliMascot small staticPose face /></div>
          {resume ? <>
            <p className={styles.eyebrow}>Continue</p>
            <h2 id="continue-title" className={`${styles.display} ${styles.h2}`} style={{ paddingRight: 84 }}>{resume.title}</h2>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><span className={`${styles.tag} ${styles.tagLevel}`}>{resume.cefr}</span>
              {resume.accents[0] ? <span className={styles.tag}><Icon name="globe" size={14} />{accentLabel(resume.accents[0])}</span> : null}
              <span className={`${styles.tag} ${styles.tagMuted}`}><Icon name="clock" size={14} />{lengthLabel(resume.duration_ms)}</span></div>
            <div className={styles.steps} aria-hidden="true">{Array.from({ length: steps!.total }, (_, n) => <span key={n} className={`${styles.step} ${n < steps!.done - 1 ? styles.stepDone : n === steps!.done - 1 ? styles.stepNow : ''}`} />)}</div>
            <p className={styles.muted} style={{ margin: 0 }}>Step {steps!.done} of {steps!.total}</p>
            <Link className={`${styles.btn} ${styles.btnPrimary} ${styles.btnBlock}`} href={`/listening/${resume.lesson_id}`}><Icon name="play" size={18} /> Resume lesson</Link>
          </> : <>
            <p className={styles.eyebrow}>Start</p>
            <h2 id="continue-title" className={`${styles.display} ${styles.h2}`} style={{ paddingRight: 84 }}>{next ? 'Ready for your next lesson?' : 'You’ve finished everything for now'}</h2>
            <p className={styles.muted} style={{ margin: 0 }}>{next ? 'Pick up a new lesson whenever you like.' : 'New lessons will appear here.'}</p>
            {next ? <Link className={`${styles.btn} ${styles.btnPrimary} ${styles.btnBlock}`} href={`/listening/${next.item.lesson_id}`}><Icon name="play" size={18} /> Start {next.item.title}</Link> : null}
          </>}
        </section>

        {done.length ? <><div className={styles.sectionHead}><h2 className={`${styles.display} ${styles.h2}`}>Recently completed</h2></div>
          <ul className={styles.list}>{done.map((r, n) => <li key={r.attempt_id}><Link className={styles.row} href={`/listening/${r.lesson_id}`}>
            <span className={`${styles.rowIcon} ${n % 2 ? styles.c2 : styles.c1}`}><Icon name="check" /></span>
            <span className={styles.rowBody}><p className={styles.rowTitle}>{r.title}</p>
              <p className={styles.rowMeta}>{r.cefr} · {whenLabel(r.when)}{r.scored ? ` · ${r.correct} of ${r.scored} correct` : ''}</p></span>
            <span className={styles.chev}><Icon name="chevron" /></span></Link></li>)}</ul></> : null}

        <section className={styles.card} style={{ marginTop: 28 }} aria-labelledby="library-title">
          <div className={styles.metricHead}><h2 id="library-title" className={`${styles.display} ${styles.h3}`}>Your library</h2><span className={styles.muted}>{finished} of {total} lessons</span></div>
          <div className={styles.bar} role="img" aria-label={`${finished} of ${total} lessons completed`} style={{ marginTop: 12 }}><div className={styles.barFill} style={{ width: `${total ? Math.round((finished / total) * 100) : 0}%` }} /></div>
        </section>
      </div>

      <aside aria-label="Next steps">
        <div className={styles.sectionHead} style={{ marginTop: 0 }}><h2 className={`${styles.display} ${styles.h2}`}>Recommended next</h2><Link href="/listening" className={styles.muted}>See all</Link></div>
        {next ? <>
          <p className={styles.notice}><span aria-hidden="true" style={{ flex: "none", color: "var(--amber-700)" }}><Icon name="info" size={18} /></span>{next.reason}</p>
          <Link href={`/listening/${next.item.lesson_id}`} className={styles.row} style={{ marginTop: 12, minHeight: 110 }}>
            <span className={`${styles.rowIcon} ${styles.c3}`}><Icon name="headphones" /></span>
            <span className={styles.rowBody}><p className={styles.rowTitle}>{next.item.title}</p><p className={styles.rowMeta}>{meta(next.item)}</p></span>
            <span className={styles.chev}><Icon name="chevron" /></span></Link>
        </> : <p className={styles.muted}>Nothing new to recommend yet.</p>}
        <div className={styles.tiles} style={{ marginTop: 16 }}>
          {GENERATOR ? <Link href="/listening/create" className={styles.tile}><Icon name="create" /><b>Create a lesson</b><span>Any topic, your level</span></Link> : null}
          <Link href="/listening" className={`${styles.tile} ${styles.t2}`}><Icon name="discover" /><b>Browse library</b><span>{countLabel(total)}</span></Link>
          {GENERATOR ? null : <Link href="/listening/progress" className={styles.tile}><Icon name="progress" /><b>Your progress</b><span>{countLabel(finished)} finished</span></Link>}
        </div>
      </aside>
    </div>
  </AppShell>;
}
