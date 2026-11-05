'use client';
import Link from 'next/link';
import type { ReactNode } from 'react';
import styles from './app.module.css';
import { AudliMascot } from '../audli-mascot';
import { useAccount } from '../auth-gate';
import { Icon } from './Icons';
import { useLearnerSettings } from '../../lib/use-settings.ts';
import { displayName, initials } from '../../lib/home-core.ts';

export type NavKey = 'home' | 'discover' | 'create' | 'progress' | 'settings';
/** Screens are listed only when they work. Create lesson and Settings are switched on in the same change that ships them. */
export const NAV: { key: NavKey; href: string; label: string; icon: string; ready: boolean }[] = [
  { key: 'home', href: '/', label: 'Home', icon: 'home', ready: true },
  { key: 'discover', href: '/listening', label: 'Discover', icon: 'discover', ready: true },
  { key: 'create', href: '/listening/create', label: 'Create lesson', icon: 'create', ready: process.env.NEXT_PUBLIC_AUDLI_LISTENING_GENERATOR === 'true' },
  { key: 'progress', href: '/listening/progress', label: 'Progress', icon: 'progress', ready: true },
  { key: 'settings', href: '/listening/settings', label: 'Settings', icon: 'settings', ready: true },
];

/** Figma "App shell": a sidebar from 1024px (icons only until 1280px) and a bottom tab bar below it. Lessons themselves use the focused LessonShell instead. */
export function AppShell({ active, children }: { active: NavKey; children: ReactNode }) {
  const account = useAccount();
  const name = displayName(account.email);
  const items = NAV.filter(n => n.ready);
  const { settings } = useLearnerSettings();
  return <div className={`${styles.app} ${styles.shell}`} data-reduce-motion={settings.reduceMotion}>
    <aside className={styles.sidebar} aria-label="Audli">
      <Link href="/" className={styles.sideBrand} aria-label="Audli home"><span className={styles.brandMascot}><AudliMascot small staticPose face /></span><span className={styles.sideBrandText}>Audli</span></Link>
      <nav className={styles.nav} aria-label="Main">
        {items.map(n => <Link key={n.key} href={n.href} className={styles.navItem} aria-current={n.key === active ? 'page' : undefined} aria-label={n.label}>
          <Icon name={n.icon} /><span className={styles.navLabel}>{n.label}</span></Link>)}
      </nav>
      <div className={styles.userChip}>
        <span className={styles.avatar} aria-hidden="true">{initials(name)}</span>
        <span className={styles.userText}><span className={styles.userName}>{name}</span>
          {account.signOut ? <button type="button" className={`${styles.linkBtn} ${styles.signOut}`} onClick={() => void account.signOut?.()}>Sign out</button> : null}</span>
      </div>
    </aside>
    <main id="app-main" tabIndex={-1} className={styles.content}>
      <div className={styles.topline}>
        <Link href="/" className={styles.brand} aria-label="Audli home"><span className={styles.brandMascot}><AudliMascot small staticPose face /></span>Audli</Link>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {account.signOut ? <button type="button" className={styles.linkBtn} onClick={() => void account.signOut?.()}>Sign out</button> : null}
          <span className={styles.avatar} aria-hidden="true">{initials(name)}</span>
        </span>
      </div>
      {children}
    </main>
    <nav className={styles.bottomNav} aria-label="Main">
      {items.map(n => <Link key={n.key} href={n.href} className={styles.tab} aria-current={n.key === active ? 'page' : undefined}><Icon name={n.icon} /><span>{n.label}</span></Link>)}
    </nav>
  </div>;
}
