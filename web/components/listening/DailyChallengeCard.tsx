'use client';
import Link from 'next/link';
import { useState } from 'react';
import styles from './app.module.css';
import daily from './daily.module.css';
import { Icon } from './Icons';
import { AudliMascot } from '../audli-mascot';
import { lengthLabel } from '../../lib/listening-core.ts';
import { accentLabel } from '../../lib/home-core.ts';
import { dailyButton, dailyHref, dailyView, streakLine, untilNext, type DailyChallenge } from '../../lib/daily-core.ts';

/** The dominant element of the learner home: today's shared challenge. It never shows an older lesson in its place; "unavailable" is its own calm state. */
export function DailyChallengeCard({ data, now }: { data: DailyChallenge; now?: number }) {
  const [clock] = useState(() => now ?? Date.now());          // read once on mount: rendering must stay pure
  const view = dailyView(data);
  const lesson = data.lesson;
  const href = dailyHref(data);
  const done = view === 'completed';

  if (view === 'unavailable') return <section className={`${styles.card} ${daily.card} ${daily.quiet}`} aria-labelledby="daily-title" data-testid="daily-card">
    <p className={styles.eyebrow}>Daily Listening Challenge</p>
    <h2 id="daily-title" className={`${styles.display} ${daily.title}`}>Today’s challenge is being prepared</h2>
    <p className={styles.muted} style={{ margin: 0 }}>A brand-new lesson will appear here as soon as it is ready. Every learner gets the same one. In the meantime, the library below has plenty to listen to.</p>
    <p className={daily.streak}><Icon name="clock" size={16} /> {streakLine(data.streak)}</p>
  </section>;

  return <section className={`${styles.card} ${daily.card} ${done ? daily.done : ''}`} aria-labelledby="daily-title" data-testid="daily-card">
    <div className={daily.mascot} aria-hidden="true"><AudliMascot small staticPose face /></div>
    <p className={styles.eyebrow}>{done ? 'Completed today' : 'Daily Listening Challenge'}</p>
    <h2 id="daily-title" className={`${styles.display} ${daily.title}`}>{lesson!.title}</h2>
    <div className={daily.tags}>
      <span className={`${styles.tag} ${styles.tagLevel}`}>{lesson!.cefr}</span>
      {lesson!.accents[0] ? <span className={styles.tag}><Icon name="globe" size={14} />{lesson!.accents.map(accentLabel).join(' + ')}</span> : null}
      <span className={`${styles.tag} ${styles.tagMuted}`}><Icon name="clock" size={14} />{lengthLabel(lesson!.duration_ms)}</span>
    </div>
    <p className={daily.topic}>{lesson!.topic}</p>
    {view === 'resume' ? <p className={styles.muted} style={{ margin: 0 }}>In progress · {lesson!.answered} of {lesson!.total} answered</p> : null}
    {done ? <p className={daily.result} role="status">
      <Icon name="check" size={18} /> Completed today{lesson!.scored ? ` · ${lesson!.correct ?? 0} of ${lesson!.scored} correct` : ''}</p> : null}
    <p className={daily.streak}><span aria-hidden="true">🔥</span> {streakLine(data.streak)}</p>
    {href ? <Link className={`${styles.btn} ${done ? styles.btnSecondary : styles.btnPrimary} ${daily.cta}`} href={href}>
      <Icon name={done ? 'check' : 'play'} size={18} /> {dailyButton(view)}</Link> : null}
    <p className={daily.next}>Next challenge in {untilNext(data.next_at, clock)}</p>
  </section>;
}
