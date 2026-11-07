'use client';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import styles from './app.module.css';
import { AppShell } from './AppShell';
import { AttemptRecovery } from './AttemptRecovery';
import { AudliMascot } from '../audli-mascot';
import { Icon } from './Icons';
import { ApiError } from '../../lib/api.ts';
import { listCatalog } from '../../lib/listening.ts';
import { lengthLabel, lessonAction, statusLine, SPOKEN_NOTE, type CatalogItem } from '../../lib/listening-core.ts';
import { FORMAT_LABEL, LENGTH_LABEL, LEVELS, NO_FILTERS, accentLabel, applyFilters, countLabel, topicTiles, type Filters, type Format, type Length } from '../../lib/home-core.ts';

type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'unavailable' } | { kind: 'ready'; items: CatalogItem[] };

const ICON_TONE = ['c1', 'c2', 'c3', 'c4'] as const;

function Row({ item, n }: { item: CatalogItem; n: number }) {
  const done = item.status === 'completed';
  const detail = [item.cefr, item.accents[0] ? accentLabel(item.accents[0]) : null, item.format ? FORMAT_LABEL[item.format] : null, lengthLabel(item.duration_ms), statusLine(item)].filter(Boolean).join(' · ');
  return <li><Link className={styles.row} href={`/listening/${item.lesson_id}`}>
    <span className={`${styles.rowIcon} ${styles[ICON_TONE[n % 4]]}`}><Icon name={done ? 'check' : 'headphones'} /></span>
    <span className={styles.rowBody}><p className={styles.rowTitle}>{item.title}</p><p className={styles.rowMeta}>{detail}<span className={styles.srOnly}> · {lessonAction(item)}</span></p>
      {done && item.spoken_status ? <p className={styles.rowMeta}>{SPOKEN_NOTE[item.spoken_status]}</p> : null}</span>
    <span className={styles.chev}><Icon name="chevron" /></span></Link></li>;
}

/** Figma 03 Discover (D03 / M03): search, level, accent and length filters, topics, and the lesson list. Filters work on the saved library. */
export function ListeningCatalog() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const fetchAll = useCallback(async (): Promise<Load> => {
    try { return { kind: 'ready', items: await listCatalog() }; }
    catch (cause) {
      // While 2.0 is limited to named accounts the server answers 404 to everyone else: say so plainly instead of offering a retry that cannot help.
      return cause instanceof ApiError && cause.status === 404 ? { kind: 'unavailable' } : { kind: 'error' };
    }
  }, []);
  useEffect(() => { let live = true; void fetchAll().then(r => { if (live) setLoad(r); }); return () => { live = false; }; }, [fetchAll]);
  const retry = () => { setLoad({ kind: 'loading' }); void fetchAll().then(setLoad); };
  const items = useMemo(() => (load.kind === 'ready' ? load.items : []), [load]);
  const shown = useMemo(() => applyFilters(items, filters), [items, filters]);
  const tiles = useMemo(() => topicTiles(items), [items]);
  const accents = useMemo(() => [...new Set(items.flatMap(i => i.accents))].sort(), [items]);
  const set = (patch: Partial<Filters>) => setFilters(f => ({ ...f, ...patch }));
  const filtered = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);

  if (load.kind === 'unavailable') return <AppShell active="discover"><div className={styles.status} role="status"><div style={{ width: 96 }}><AudliMascot small face /></div>
    <p><b>Audli listening lessons aren’t available on your account yet.</b></p><Link className={`${styles.btn} ${styles.btnSecondary}`} href="/">Back to Audli</Link></div></AppShell>;
  if (load.kind === 'error') return <AppShell active="discover"><AttemptRecovery state="error" onRetry={retry} /></AppShell>;
  if (load.kind === 'loading') return <AppShell active="discover"><h1 className={`${styles.display} ${styles.h1}`}>Discover</h1>
    <div role="status" aria-live="polite" aria-busy="true"><p className={styles.srOnly}>Loading lessons…</p>
      <div className={styles.list} aria-hidden="true" style={{ marginTop: 20 }}>{[0, 1, 2].map(n => <div key={n} className={styles.skeleton} />)}</div></div></AppShell>;

  return <AppShell active="discover">
    <h1 className={`${styles.display} ${styles.h1}`} style={{ marginBottom: 16 }}>Discover</h1>
    {items.length === 0 ? <div className={styles.status} role="status"><div style={{ width: 96 }}><AudliMascot state="Thinking" small face /></div>
      <p><b>No lessons yet.</b></p><p className={styles.muted}>New lessons will appear here. Check back soon.</p></div> : <>
      <label className={styles.search}><Icon name="search" /><span className={styles.srOnly}>Search topics</span>
        <input type="search" placeholder="Search topics — “airport”, “small talk”" value={filters.query} onChange={e => set({ query: e.target.value })} /></label>
      <div className={styles.filters}>
        <select className={styles.select} aria-label="Accent" value={filters.accent} onChange={e => set({ accent: e.target.value })}>
          <option value="All">All accents</option>{accents.map(a => <option key={a} value={a}>{accentLabel(a)}</option>)}</select>
      </div>
      <div className={styles.filters} role="group" aria-label="Level">
        {['All', ...LEVELS].map(l => <button key={l} type="button" className={styles.chip} aria-pressed={filters.cefr === l} onClick={() => set({ cefr: l })}>{l}</button>)}
      </div>
      <div className={styles.filters} role="group" aria-label="Format">
        {(['All', 'story', 'dialogue', 'mixed'] as const).map(f => <button key={f} type="button" className={styles.chip} aria-pressed={filters.format === f}
          onClick={() => set({ format: f })}>{f === 'All' ? 'All formats' : FORMAT_LABEL[f as Format]}</button>)}
      </div>
      <div className={styles.discoverGrid}>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <section className={styles.card} aria-labelledby="topics-title"><h2 id="topics-title" className={`${styles.display} ${styles.h3}`} style={{ marginBottom: 12 }}>Topics</h2>
            <div className={styles.topics}>{tiles.map((t, n) => <button key={t.topic} type="button" className={`${styles.topic} ${styles[`tone${n % 4}`]}`} aria-pressed={filters.topic === t.topic}
              onClick={() => set({ topic: filters.topic === t.topic ? null : t.topic })}><b>{t.topic}</b><span>{countLabel(t.count)}</span></button>)}</div></section>
          <section className={styles.card} aria-labelledby="length-title"><h2 id="length-title" className={`${styles.display} ${styles.h3}`} style={{ marginBottom: 12 }}>Length</h2>
            <div className={styles.filters} style={{ margin: 0 }}>{(Object.keys(LENGTH_LABEL) as Exclude<Length, 'any'>[]).map(k => <button key={k} type="button" className={styles.chip}
              aria-pressed={filters.length === k} onClick={() => set({ length: filters.length === k ? 'any' : k })}>{LENGTH_LABEL[k]}</button>)}</div></section>
        </div>
        <section aria-labelledby="results-title">
          <div className={styles.sectionHead} style={{ marginTop: 0 }}><h2 id="results-title" className={`${styles.display} ${styles.h2}`}>{countLabel(shown.length)}</h2>
            {filtered ? <button type="button" className={styles.linkBtn} onClick={() => setFilters(NO_FILTERS)}>Clear filters</button> : null}</div>
          {shown.length === 0 ? <div className={styles.status} role="status"><p><b>No lessons match your filters.</b></p>
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={() => setFilters(NO_FILTERS)}>Clear filters</button></div>
            : <ul className={styles.list} aria-label="Lessons">{shown.map((item, n) => <Row key={item.lesson_id} item={item} n={n} />)}</ul>}
        </section>
      </div>
    </>}
  </AppShell>;
}
