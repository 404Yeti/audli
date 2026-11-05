'use client';
import styles from './app.module.css';
import { AudliMascot } from '../audli-mascot';
import { Icon } from './Icons';
import { AVAILABLE_ACCENTS, COMING_ACCENTS, accentLabel } from '../../lib/home-core.ts';
import { waveformBars } from '../../lib/listening-core.ts';

/** Figma 01 Landing (D01 / M01). Every claim on this page is something Audli does today; sections for features that have not shipped are added with the feature. */
export function Landing({ onSignUp, onSignIn }: { onSignUp: () => void; onSignIn: () => void }) {
  const bars = waveformBars(40);
  return <div className={`${styles.app} ${styles.landing}`}>
    <header className={styles.lpHead}>
      <div className={styles.lpHeadInner}>
        <span className={styles.brand}><span className={styles.brandMascot}><AudliMascot small staticPose face /></span>Audli</span>
        <nav className={styles.lpNav} aria-label="Sections"><a href="#how">How it works</a><a href="#accents">Accents</a><a href="#transcript">Transcript</a></nav>
        <div className={styles.lpActions}>
          <button type="button" className={styles.linkBtn} onClick={onSignIn}>Log in</button>
          <button type="button" className={`${styles.btn} ${styles.btnPrimary} ${styles.btnSmall}`} onClick={onSignUp}>Start listening</button>
        </div>
      </div>
    </header>
    <main id="landing-main">
      <section className={styles.lpHero}>
        <div className={styles.lpHeroGrid}>
          <div style={{ display: 'grid', gap: 20 }}>
            <span className={styles.lpPill}><Icon name="headphones" size={16} /> Listening-first language learning</span>
            <h1 className={`${styles.display} ${styles.lpBig}`}>Eyes optional.<em>Ears essential.</em></h1>
            <p className={styles.lpLead}>Short audio lessons at your level. Listen first, open the transcript only if you want it, answer four quick exercises, and get feedback that points to what you missed.</p>
            <div className={styles.lpCtas}>
              <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={onSignUp}>Start listening — free</button>
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={onSignIn}>I have an account</button>
            </div>
            <ul className={styles.lpChecks}><li>✓ Real recorded voices</li><li>✓ Three accents</li><li>✓ About five minutes a lesson</li></ul>
          </div>
          <div className={styles.lpPanel} aria-hidden="true">
            <div className={styles.lpMascot}><AudliMascot state="Listening" staticPose face /></div>
            <div className={styles.lpPlayer}>
              <span className={styles.tag}>B1 · American</span>
              <div className={styles.lpLine}>“Could I get a flat white, please?”</div>
              <div className={styles.lpWave}>{bars.map((h, i) => <i key={i} className={i < 16 ? styles.on : undefined} style={{ height: `${h}%` }} />)}</div>
            </div>
          </div>
        </div>
      </section>

      <section id="how" className={`${styles.lpSection} ${styles.lpWhite}`}><div className={styles.lpInner}>
        <div className={styles.lpCenter}><p className={styles.eyebrow}>How it works</p><h2 className={`${styles.display} ${styles.h1}`}>Four steps. About five minutes.</h2>
          <p className={`${styles.muted}`} style={{ margin: 0 }}>Every lesson follows the same calm rhythm, so you always know what comes next.</p></div>
        <div className={styles.lpGrid4}>
          {[['1', 'Pick a lesson', 'Browse saved lessons by topic, level and accent.'], ['2', 'Listen first', 'Audio is the lesson. Open the transcript only when you need it.'],
            ['3', 'Four quick exercises', 'Choose, speak, fill the gap, put events in order.'], ['4', 'See what you understood', 'Your spoken answer is checked against the lesson’s key ideas, with your own words quoted.']]
            .map(([n, t, d]) => <div key={n} className={styles.lpStep}><span className={styles.lpNum}>{n}</span><b>{t}</b><span className={styles.muted}>{d}</span></div>)}
        </div>
      </div></section>

      <section id="accents" className={`${styles.lpSection} ${styles.lpAlt}`}><div className={styles.lpInner}>
        <div className={styles.lpCenter}><p className={styles.eyebrow}>Accent variety</p><h2 className={`${styles.display} ${styles.h1}`}>Hear English the way it’s really spoken</h2>
          <p className={styles.muted} style={{ margin: 0 }}>Lessons are recorded in different accents. More are on the way.</p></div>
        <div className={styles.accents} role="list" aria-label="Accents">
          {AVAILABLE_ACCENTS.map(a => <span role="listitem" key={a} className={styles.chip}>{accentLabel(a)}</span>)}
          {COMING_ACCENTS.map(a => <span role="listitem" key={a} className={`${styles.chip} ${styles.chipOff}`} aria-disabled="true">{a} · coming soon</span>)}
        </div>
      </div></section>

      <section id="transcript" className={`${styles.lpSection} ${styles.lpWhite}`}><div className={`${styles.lpInner} ${styles.lpSplit}`}>
        <div style={{ display: 'grid', gap: 12 }}>
          <p className={styles.eyebrow}>Interactive transcript</p><h2 className={`${styles.display} ${styles.h1}`}>A transcript that listens with you</h2>
          <p className={styles.muted} style={{ margin: 0 }}>Challenge keeps the text hidden, for honest practice. Guided highlights each line as it is spoken. Tap a line to hear it again.</p>
          <ul className={styles.lpMode}><li style={{ display: "flex", gap: 8, alignItems: "center" }}><Icon name="eyeOff" size={18} /> Challenge — audio only</li><li style={{ display: "flex", gap: 8, alignItems: "center" }}><Icon name="lines" size={18} /> Guided — lines follow the voice</li></ul>
        </div>
        <div className={styles.lpTranscript} aria-hidden="true">
          <span className={styles.muted}>Sam · barista</span><b style={{ fontSize: 19 }}>Morning! What can I get for you?</b>
          <span className={styles.muted}>Priya</span><b style={{ fontSize: 19 }}>Hi — could I get a flat white, please?</b>
        </div>
      </div></section>

      {process.env.NEXT_PUBLIC_AUDLI_LISTENING_GENERATOR === 'true' ? <section className={`${styles.lpSection} ${styles.lpAlt}`}><div className={`${styles.lpInner} ${styles.lpSplit}`}>
        <div className={styles.card} aria-hidden="true" style={{ display: 'grid', gap: 12 }}>
          <div className={styles.lpLine} style={{ background: 'var(--white)', borderLeftColor: 'var(--sun-400)' }}>“I want a B1 lesson about ordering food at a restaurant.”</div>
          <span className={styles.muted} style={{ fontSize: 14 }}>→ a couple of minutes later, saved to your library</span>
          <div className={styles.readyCard}><div className={styles.readyArt} style={{ minHeight: 72 }}><span className={`${styles.tag} ${styles.tagLevel}`} style={{ alignSelf: 'flex-start' }}>B1</span></div>
            <div className={styles.readyBody}><b>Ordering food at a restaurant</b><span className={styles.muted} style={{ fontSize: 14 }}>American · Dialogue · about 2 min</span></div></div>
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          <p className={styles.eyebrow}>Personalised lessons</p><h2 className={`${styles.display} ${styles.h1}`}>Describe it. We’ll build it.</h2>
          <p className={styles.muted} style={{ margin: 0 }}>Tell Audli what you want to practise, in your own words. It writes the script at your level, records it in the accent you choose, syncs the transcript and writes four exercises. Every lesson is saved to your library, only for you.</p>
        </div></div></section> : null}

      <section className={`${styles.lpSection} ${styles.lpAlt}`}><div className={styles.lpInner}>
        <div className={styles.lpCenter}><p className={styles.eyebrow}>The four-exercise model</p><h2 className={`${styles.display} ${styles.h1}`}>Four exercises. One clear picture.</h2>
          <p className={styles.muted} style={{ margin: 0 }}>Each exercise checks a different kind of understanding, so feedback is specific, not a vague score.</p></div>
        <div className={styles.lpGrid4}>
          <div className={`${styles.ex} ${styles.exBlue}`}><b>Choose</b><span>Key details, several options.</span></div>
          <div className={`${styles.ex} ${styles.exCoral}`}><b>Speak</b><span>Explain what you understood, in your own words.</span></div>
          <div className={`${styles.ex} ${styles.exTeal}`}><b>Fill the gap</b><span>Recall the exact word or phrase.</span></div>
          <div className={`${styles.ex} ${styles.exSun}`}><b>Put in order</b><span>Show you followed the whole story.</span></div>
        </div>
      </div></section>

      <section className={`${styles.lpSection} ${styles.lpWhite}`}><div className={`${styles.lpInner} ${styles.lpSplit}`}>
        <div style={{ display: 'grid', gap: 12 }}>
          <p className={styles.eyebrow}>Progress tracking</p><h2 className={`${styles.display} ${styles.h1}`}>Progress you can trust</h2>
          <p className={styles.muted} style={{ margin: 0 }}>Audli only reports what happened: the lessons you finished, how many questions you got right, and which accents you have heard. Your spoken answers are checked but never turned into a score.</p>
        </div>
        <div className={styles.card} aria-hidden="true">
          <div className={styles.metric}><div className={styles.metricHead}><span>Lessons finished</span><span>3</span></div><div className={styles.bar}><div className={styles.barFill} style={{ width: '60%' }} /></div></div>
          <div className={styles.metric}><div className={styles.metricHead}><span>Accents heard</span><span>2</span></div><div className={styles.bar}><div className={`${styles.barFill} ${styles.barSun}`} style={{ width: '66%' }} /></div></div>
        </div>
      </div></section>

      <section className={styles.lpCta}>
        <div style={{ width: 120 }}><AudliMascot state="Success" staticPose face /></div>
        <h2 className={`${styles.display} ${styles.h1}`}>Ready when your ears are.</h2>
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={onSignUp}>Start listening — free</button>
      </section>
    </main>
    <footer className={styles.lpFooter}><div className={styles.lpInner} style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'space-between' }}>
      <span><b className={styles.display}>Audli</b> <span className={styles.muted}>Eyes optional. Ears essential.</span></span>
      <span className={styles.muted}>© 2026 Audli</span>
    </div></footer>
  </div>;
}
