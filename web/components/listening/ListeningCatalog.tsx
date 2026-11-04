'use client';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import styles from './listening.module.css';
import { LessonShell } from './LessonShell';
import { AttemptRecovery } from './AttemptRecovery';
import { listCatalog } from '../../lib/listening.ts';
import { formatTime, type CatalogItem } from '../../lib/listening-core.ts';

export function ListeningCatalog() {
  const [items, setItems] = useState<CatalogItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    listCatalog().then(result => { if (live) setItems(result); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, []);
  const load = () => { setFailed(false); listCatalog().then(setItems, () => setFailed(true)); };
  if (failed) return <LessonShell phase={null}><AttemptRecovery state="error" onRetry={load} /></LessonShell>;
  if (!items) return <LessonShell phase={null}><AttemptRecovery state="loading" /></LessonShell>;
  return <LessonShell phase={null}>
    <h1 className={`${styles.heading} ${styles.title}`}>Listening lessons</h1>
    {items.length === 0 ? <p className={styles.note} role="status">No lessons are published yet.</p> : <ul className={styles.catalog}>
      {items.map(item => <li key={item.lesson_id}>
        <Link className={styles.lessonCard} href={`/listening/${item.lesson_id}`}>
          <span className={`${styles.heading}`} style={{ fontSize: 20 }}>{item.title}</span>
          <span className={styles.tags} style={{ justifyContent: 'flex-start' }}>
            <span className={`${styles.tag} ${styles.level}`}>{item.cefr}</span>
            <span className={styles.tag}>{formatTime(item.duration_ms)} listen</span>
          </span>
        </Link>
      </li>)}
    </ul>}
  </LessonShell>;
}
