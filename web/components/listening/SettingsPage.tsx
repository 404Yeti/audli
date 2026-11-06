'use client';
import Link from 'next/link';
import styles from './app.module.css';
import { AppShell } from './AppShell';
import { Icon } from './Icons';
import { useAccount } from '../auth-gate';
import { useLearnerSettings } from '../../lib/use-settings.ts';
import { SETTINGS_SPEEDS, SPEED_LABEL, type LearnerSettings } from '../../lib/settings-core.ts';
import { displayName, initials } from '../../lib/home-core.ts';

const Soon = () => <span className={styles.setPill}>Not available yet</span>;

function Switch({ label, hint, checked, onChange, id }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; id: string }) {
  return <li className={styles.setRow}>
    <span className={styles.setText} id={`${id}-l`}>{label}{hint ? <span className={styles.setSub} id={`${id}-h`}>{hint}</span> : null}</span>
    <button type="button" role="switch" id={id} className={styles.setSwitch} aria-checked={checked} aria-labelledby={`${id}-l`} aria-describedby={hint ? `${id}-h` : undefined} onClick={() => onChange(!checked)} />
  </li>;
}

function Unavailable({ label, hint, value }: { label: string; hint: string; value?: string }) {
  return <li className={styles.setRow} data-unavailable="true">
    <span className={styles.setText}>{label}<span className={styles.setSub}>{hint}</span></span>
    {value ? <span className={styles.setValue}>{value}</span> : null}<Soon />
  </li>;
}

/** Figma D15 (desktop two columns) and M15 (one column). Only what the 2.0 player honours is interactive; the rest says so plainly. */
export function SettingsPage() {
  const account = useAccount();
  const { settings, update, saved } = useLearnerSettings();
  const name = displayName(account.email);
  const set = (patch: Partial<LearnerSettings>) => update(patch);
  return <AppShell active="settings">
    <h1 className={`${styles.display} ${styles.h1}`}>Settings</h1>
    <p className={styles.muted} style={{ margin: '4px 0 20px' }}>Changes save automatically on this device.</p>
    <p className={styles.setSaved} role="status" aria-live="polite" data-testid="settings-status">
      {saved === true ? 'Saved' : saved === false ? 'This browser blocked saving, so changes last until you leave this page.' : ''}</p>
    <div className={styles.setCols}>
      <div className={styles.setCols} style={{ gridTemplateColumns: '1fr', alignContent: 'start' }}>
        <div className={styles.setList} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 16 }}>
          <span className={styles.avatar} aria-hidden="true" style={{ width: 48, height: 48 }}>{initials(name)}</span>
          <span className={styles.setText}><strong>{name}</strong><span className={styles.setSub}>{account.email ?? 'Local development account'}</span></span>
        </div>

        <section className={styles.setGroup} aria-labelledby="g-learning"><h2 className={styles.setCaps} id="g-learning">Learning</h2>
          <ul className={styles.setList}>
            <li className={styles.setRow}><span className={styles.setText}>Target language<span className={styles.setSub}>More languages are not available yet</span></span><span className={styles.setValue}>English</span></li>
            <Unavailable label="Preferred accent" hint="Pick an accent when you start a lesson" />
            <Unavailable label="Current level" hint="Audli does not estimate your level in this version" />
            <Unavailable label="Learning goals" hint="Describe a goal when you create a lesson" />
          </ul></section>

        <section className={styles.setGroup} aria-labelledby="g-listening"><h2 className={styles.setCaps} id="g-listening">Listening</h2>
          <div className={`${styles.setList} ${styles.setPad}`}>
            <div role="radiogroup" aria-labelledby="mode-l" style={{ display: 'grid', gap: 8 }}>
              <strong id="mode-l">Default transcript mode</strong>
              <div className={styles.setSeg}>
                <button type="button" role="radio" aria-checked={settings.transcriptMode === 'challenge'} onClick={() => set({ transcriptMode: 'challenge' })}><Icon name="eyeOff" size={16} />Challenge</button>
                <button type="button" role="radio" aria-checked={settings.transcriptMode === 'guided'} onClick={() => set({ transcriptMode: 'guided' })}><Icon name="lines" size={16} />Guided</button>
                <button type="button" role="radio" aria-checked={false} aria-disabled="true" disabled title="Vocabulary help is not available yet">Review</button>
              </div>
              <span className={styles.setSub}>Challenge keeps the transcript hidden. Guided shows it. Review (with vocabulary help) is not available yet.</span>
            </div>
            <div role="radiogroup" aria-labelledby="speed-l" style={{ display: 'grid', gap: 8 }}>
              <strong id="speed-l">Default playback speed</strong>
              <div className={styles.setChips}>{SETTINGS_SPEEDS.map(s => <button key={s} type="button" role="radio" className={styles.setChip} aria-checked={settings.speed === s} onClick={() => set({ speed: s })}>
                {settings.speed === s ? <Icon name="check" size={16} /> : null}{SPEED_LABEL(s)}</button>)}</div>
            </div>
          </div></section>
      </div>

      <div className={styles.setCols} style={{ gridTemplateColumns: '1fr', alignContent: 'start' }}>
        <section className={styles.setGroup} aria-labelledby="g-access"><h2 className={styles.setCaps} id="g-access">Accessibility</h2>
          <ul className={styles.setList}>
            <li className={styles.setRow}><span className={styles.setText} id="size-l">Transcript text size</span>
              <span className={styles.setSeg} role="radiogroup" aria-labelledby="size-l">
                <button type="button" role="radio" aria-checked={settings.textSize === 'default'} onClick={() => set({ textSize: 'default' })}>Default</button>
                <button type="button" role="radio" aria-checked={settings.textSize === 'large'} onClick={() => set({ textSize: 'large' })}>Large</button></span></li>
            <Switch id="s-motion" label="Reduce motion" hint="Mascot and transitions stay still" checked={settings.reduceMotion} onChange={v => set({ reduceMotion: v })} />
            <Switch id="s-contrast" label="High-contrast transcript" checked={settings.highContrast} onChange={v => set({ highContrast: v })} />
            <Unavailable label="Prefer typing over speaking" hint="Spoken questions can be skipped; a typed version is not available yet" />
          </ul></section>

        <section className={styles.setGroup} aria-labelledby="g-privacy"><h2 className={styles.setCaps} id="g-privacy">Account &amp; privacy</h2>
          <ul className={styles.setList}>
            <li className={styles.setRow}><span className={styles.setText}>Voice recordings<span className={styles.setSub}>Deleted as soon as they are turned into text. The text stays in your account.</span></span></li>
            <li className={styles.setRow}><Link href="/privacy" className={styles.setBtn} style={{ padding: 0, minHeight: 0 }}><span className={styles.setText}>Privacy and data use</span><Icon name="chevron" size={18} /></Link></li>
            <Unavailable label="Download my data" hint="Email robbie@audli.ai to ask for a copy" />
            <Unavailable label="Delete my account" hint="Email robbie@audli.ai to ask for deletion" />
            {account.signOut ? <li className={styles.setRow} style={{ padding: 0 }}><button type="button" className={styles.setBtn} onClick={() => void account.signOut?.()}><span className={styles.setText}>Sign out</span></button></li> : null}
          </ul></section>
      </div>
    </div>
  </AppShell>;
}
