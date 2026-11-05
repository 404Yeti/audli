'use client';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import styles from './app.module.css';
import { AppShell } from './AppShell';
import { AudliMascot, type MascotState } from '../audli-mascot';
import { Icon } from './Icons';
import { ApiError } from '../../lib/api.ts';
import { AVAILABLE_ACCENTS, COMING_ACCENTS, accentLabel } from '../../lib/home-core.ts';
import { formatTime, newRequestId } from '../../lib/listening-core.ts';
import { createGeneration, getAvailability, getGeneration, interpretRequest, rememberRequest, rememberedRequest } from '../../lib/generation.ts';
import {
  ACCENT_NOTE, DEFAULT_SPEC, EXAMPLES, FORMATS, FORMAT_LABEL, LEVELS, LEVEL_HELP, MAX_TEXT, POLL_MS, SLOW_AFTER_MS, STEPS, VOICES_LABEL, canCreate, defaultedFields,
  isLive, lengthChoices, noteLines, specLine, stepStates, unavailableCopy, type Availability, type GenerationView, type Spec,
} from '../../lib/generation-core.ts';

type Screen = 'loading' | 'form' | 'reading' | 'building' | 'ready' | 'failed' | 'rejected' | 'unavailable' | 'broken';

function Panel({ mascot = 'Idle', title, children }: { mascot?: MascotState; title: string; children?: React.ReactNode }) {
  return <div className={styles.centerPanel}><div className={styles.mascotBox}><AudliMascot state={mascot} face staticPose /></div>
    <h1 className={`${styles.display}`}>{title}</h1>{children}</div>;
}

/** Figma 04 Lesson Generator: describe it in your own words or use the controls, review the settings, then watch it being built. One pipeline for both. */
export function CreateLesson() {
  const [screen, setScreen] = useState<Screen>('loading');
  const [avail, setAvail] = useState<Availability | null>(null);
  const [text, setText] = useState('');
  const [spec, setSpec] = useState<Spec>(DEFAULT_SPEC);
  const [notes, setNotes] = useState<string[]>([]);
  const [question, setQuestion] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState(false);          // the settings were filled in from the description and shown to the learner
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<GenerationView | null>(null);
  const [slow, setSlow] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const started = useRef<number>(0);
  const max = avail?.max_minutes ?? 2;
  const set = (patch: Partial<Spec>) => setSpec(s => ({ ...s, ...patch }));

  const show = useCallback((next: GenerationView) => {
    setView(next);
    if (next.phase === 'ready') { setScreen('ready'); rememberRequest(null); }
    else if (next.phase === 'failed') { setScreen('failed'); rememberRequest(null); }
    else if (next.phase === 'rejected') { setScreen('rejected'); rememberRequest(null); }
    else if (next.phase === 'unavailable') { setScreen('unavailable'); rememberRequest(null); }
    else if (isLive(next.phase)) setScreen('building');
  }, []);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const a = await getAvailability();
        if (!live) return;
        setAvail(a);
        const id = rememberedRequest();
        if (id) { try { const v = await getGeneration(id); if (live && v.kind === 'generate') { started.current = Date.now(); show(v); return; } } catch { rememberRequest(null); } }
        setScreen(a.available ? 'form' : 'unavailable');
      } catch (cause) { if (live) setScreen(cause instanceof ApiError && cause.status === 404 ? 'broken' : 'broken'); }
    })();
    return () => { live = false; };
  }, [show]);

  // Poll the building lesson; the lesson is never shown as ready until the server says so.
  useEffect(() => {
    if (screen !== 'building' || !view) return;
    let live = true;
    const timer = window.setInterval(async () => {
      try { const next = await getGeneration(view.id); if (live) { setSlow(Date.now() - started.current > SLOW_AFTER_MS); show(next); } } catch { /* a dropped poll is retried on the next tick */ }
    }, POLL_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [screen, view, show]);

  const read = async () => {
    setBusy(true); setProblem(null); setQuestion(null); setScreen('reading');
    try {
      let v = await interpretRequest(text.trim());
      for (let n = 0; n < 40 && v.phase === 'queued'; n++) { await new Promise(r => setTimeout(r, 1000)); v = await getGeneration(v.id); }
      if (v.phase === 'interpreted' && v.spec) { setSpec(v.spec); setNotes(v.notes); setReviewed(true); setScreen('form'); }
      else if (v.phase === 'needs_input') { setQuestion(v.question); setScreen('form'); }
      else if (v.phase === 'rejected') { setView(v); setScreen('rejected'); }
      else { setProblem('We couldn’t read that just now. Please try again, or use the settings below.'); setScreen('form'); }
    } catch (cause) {
      setProblem(cause instanceof ApiError && cause.status === 503 ? 'Lesson creation is paused for a little while.' : 'We couldn’t read that just now. Please try again, or use the settings below.');
      setScreen('form');
    } finally { setBusy(false); }
  };

  const create = async () => {
    if (!reviewed && text.trim().length >= 3 && spec.topic.trim().length < 3) { await read(); return; }       // describe first, then review, then create
    setBusy(true); setProblem(null);
    try {
      const v = await createGeneration({ ...spec, topic: spec.topic.trim() || text.trim().slice(0, 120) }, text.trim() || null);
      started.current = Date.now(); setSlow(false); rememberRequest(v.id); show(v);
    } catch (cause) {
      setProblem(cause instanceof ApiError && cause.status === 503 ? 'Lesson creation is paused for a little while.' : 'We couldn’t start your lesson. Nothing was used. Please try again.');
    } finally { setBusy(false); }
  };

  const editRequest = () => { setScreen('form'); setView(null); setProblem(null); };
  const again = () => { setText(''); setSpec(DEFAULT_SPEC); setNotes([]); setReviewed(false); setQuestion(null); setView(null); setScreen('form'); getAvailability().then(a => { setAvail(a); if (!a.available) setScreen('unavailable'); }).catch(() => undefined); };

  if (screen === 'loading') return <AppShell active="create"><h1 className={`${styles.display} ${styles.h1}`}>Create a lesson</h1><div role="status" aria-busy="true"><p className={styles.srOnly}>Loading…</p><div className={styles.skeleton} style={{ minHeight: 240, marginTop: 20 }} /></div></AppShell>;
  if (screen === 'broken') return <AppShell active="create"><Panel mascot="Retry" title="Lesson creation isn’t available yet"><p className={styles.muted}>You can still browse and play every lesson in the library.</p>
    <div className={styles.actionsStack}><Link className={`${styles.btn} ${styles.btnPrimary}`} href="/listening">Browse library</Link></div></Panel></AppShell>;
  if (screen === 'unavailable') {
    const copy = unavailableCopy(view?.unavailable_reason ?? avail?.reason ?? null, avail?.per_learner_daily ?? 2);
    return <AppShell active="create"><div role="status"><Panel mascot="Thinking" title={copy.title}><p className={styles.muted}>{copy.text}</p>
      <div className={styles.actionsStack}><Link className={`${styles.btn} ${styles.btnPrimary}`} href="/listening">Browse library</Link></div></Panel></div></AppShell>;
  }
  if (screen === 'rejected') return <AppShell active="create"><div role="alert"><Panel mascot="Retry" title="We can’t make that lesson"><p className={styles.muted}>Audli builds everyday listening practice. Try a different topic — for example, “a doctor’s appointment” or “a job interview”.</p>
    <div className={styles.actionsStack}><button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={editRequest}>Edit request →</button><Link className={`${styles.btn} ${styles.btnSecondary}`} href="/listening">Browse library</Link></div></Panel></div></AppShell>;
  if (screen === 'failed') return <AppShell active="create"><div role="alert"><Panel mascot="Retry" title="We couldn’t finish this lesson">
    <p className={styles.muted}>{view?.failure ?? 'Something went wrong. Nothing was saved.'} Your request and settings are saved — nothing is lost.</p>
    <p className={styles.infoBar}><span aria-hidden="true"><Icon name="info" size={18} /></span><span>We already retried automatically. Ref: {view?.id.slice(0, 8).toUpperCase()}</span></p>
    <div className={styles.actionsStack}><button type="button" className={`${styles.btn} ${styles.btnPrimary}`} disabled={busy} onClick={() => void create()}>Try again →</button>
      <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={editRequest}>Edit request</button></div></Panel></div></AppShell>;

  if (screen === 'building' && view) {
    const states = stepStates(view.phase);
    const s = view.spec ?? spec;
    return <AppShell active="create"><div className={styles.centerPanel}>
      <div className={styles.mascotBox}><AudliMascot state="Thinking" face staticPose /></div>
      <h1 className={styles.display}>{slow ? 'Still building your lesson' : view.title || s.topic}</h1>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}><span className={`${styles.tag} ${styles.tagLevel}`}>{s.cefr}</span><span className={styles.tag}>{accentLabel(s.accent)}</span>
        <span className={`${styles.tag} ${styles.tagMuted}`}>{FORMAT_LABEL[s.format]}</span><span className={`${styles.tag} ${styles.tagMuted}`}>~{s.minutes} min</span></div>
      {slow ? <p className={styles.muted}>This one is taking longer than usual. You can leave — it will appear in your library when it’s ready.</p> : null}
      <div className={styles.checklist} role="status" aria-live="polite" aria-label="Lesson progress">
        {STEPS.map((step, i) => <div key={step.key} className={`${styles.stepRow} ${styles[states[i] === 'active' ? 'now' : states[i]]}`}>
          <span className={styles.stepIcon} aria-hidden="true">{states[i] === 'done' ? <Icon name="check" size={14} /> : null}</span>
          <span><b>{states[i] === 'done' ? step.done : step.label}</b><span className={styles.srOnly}>{states[i] === 'active' ? ' — in progress' : states[i] === 'todo' ? ' — waiting' : ''}</span>
            {step.key === 'audio' && states[i] !== 'todo' ? <><br /><span className={styles.muted} style={{ fontSize: 14 }}>{accentLabel(s.accent)} voices · {VOICES_LABEL[s.format]}</span></> : null}</span></div>)}
      </div>
      <p className={styles.infoBar}><span aria-hidden="true"><Icon name="info" size={18} /></span><span>You can leave this screen. The lesson will be in your library when it’s ready.</span></p>
      <div className={styles.actionsStack}><Link className={`${styles.btn} ${styles.btnSecondary}`} href="/listening">Browse while you wait</Link></div>
    </div></AppShell>;
  }

  if (screen === 'ready' && view) {
    const s = view.spec ?? spec;
    return <AppShell active="create"><div className={styles.centerPanel} style={{ maxWidth: 660 }}>
      <p className={styles.ok} role="status" style={{ width: '100%' }}><Icon name="check" size={18} /> Saved to your library · you can replay it any time</p>
      <div className={styles.readyCard}>
        <div className={styles.readyArt}><span className={`${styles.tag} ${styles.tagLevel}`} style={{ alignSelf: 'flex-start' }}>{s.cefr}</span>
          <span className={`${styles.tag}`} style={{ alignSelf: 'flex-start' }}><Icon name="clock" size={14} />~{s.minutes} min</span></div>
        <div className={styles.readyBody}><h1 className={`${styles.display} ${styles.h2}`} style={{ fontSize: 24 }}>{view.title}</h1>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><span className={styles.tag}><Icon name="globe" size={14} />{accentLabel(s.accent)}</span><span className={`${styles.tag} ${styles.tagMuted}`}>{FORMAT_LABEL[s.format]}</span></div></div></div>
      <div className={styles.infoRows}>
        <div className={styles.infoRow}><Icon name="headphones" /><span><b>Audio</b><br /><span className={styles.muted}>{accentLabel(s.accent)} · {VOICES_LABEL[s.format]}</span></span></div>
        <div className={styles.infoRow}><Icon name="lines" /><span><b>Transcript</b><br /><span className={styles.muted}>Synced to the audio</span></span></div>
        <div className={styles.infoRow}><Icon name="check" /><span><b>4 exercises</b><br /><span className={styles.muted}>Choose · Speak · Fill · Order</span></span></div>
      </div>
      <p className={styles.infoBar}><span aria-hidden="true"><Icon name="info" size={18} /></span><span>Tip: try it in Challenge mode first (no transcript). You can switch any time.</span></p>
      <div className={styles.actionsStack}><Link className={`${styles.btn} ${styles.btnPrimary}`} href={`/listening/${view.lesson_id}`}><Icon name="play" size={18} /> Start listening</Link>
        <button type="button" className={`${styles.btn} ${styles.btnSecondary}`} onClick={again}>Create another</button></div>
    </div></AppShell>;
  }

  const defaulted = new Set(defaultedFields(notes));
  const lines = noteLines(notes, max);
  const choices = lengthChoices(max);
  const label = spec.topic.trim() || text.trim().slice(0, 60) || 'Your lesson';
  return <AppShell active="create"><div className={styles.createGrid}>
    <div>
      <h1 className={`${styles.display} ${styles.h1}`}>Create a lesson</h1>
      <p className={styles.muted} style={{ margin: '4px 0 0', maxWidth: 620 }}>Describe what you want to practise. Audli builds the audio, transcript and four exercises — and saves it to your library.</p>
      <label className={styles.fieldLabel} htmlFor="gen-text">What do you want to listen to?</label>
      <div className={styles.counterWrap}>
        <textarea id="gen-text" className={styles.textarea} maxLength={MAX_TEXT} value={text} placeholder="e.g. I want a B1 listening lesson about ordering coffee in New York, with American accents, around two minutes."
          onChange={e => { setText(e.target.value); setReviewed(false); }} aria-describedby="gen-count" />
        <span id="gen-count" className={styles.counter}>{text.length} / {MAX_TEXT}</span>
      </div>
      <p className={styles.muted} style={{ margin: '4px 0 0', maxWidth: 620, fontSize: 14 }} data-testid="generation-disclosure">Your description is sent to OpenAI to write the lesson, and the script to ElevenLabs to make the voices. The lesson is private to your account. <a href="/privacy" target="_blank" rel="noopener">Privacy details</a></p>
      <div className={styles.filters} aria-label="Examples">{EXAMPLES.map(x => <button key={x} type="button" className={styles.chip} onClick={() => { setText(x); setReviewed(false); }}>{x}</button>)}</div>
      {question ? <p className={styles.notice} role="status"><span aria-hidden="true"><Icon name="info" size={18} /></span>{question}</p> : null}
      {problem ? <p className={styles.notice} role="alert"><span aria-hidden="true"><Icon name="info" size={18} /></span>{problem}</p> : null}
      {reviewed ? <p className={styles.ok} role="status"><Icon name="check" size={18} /> We filled in the details below from your description — change anything.</p> : null}
      {lines.map(l => <p key={l} className={styles.notice} role="status"><span aria-hidden="true"><Icon name="info" size={18} /></span>{l}</p>)}

      <span className={styles.fieldLabel} id="gen-lang">Language</span>
      <div className={`${styles.select} ${styles.wide}`} role="img" aria-labelledby="gen-lang" style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Icon name="globe" size={18} /> English</div>

      <span className={styles.fieldLabel} id="gen-level">CEFR level{defaulted.has('cefr') ? <span className={styles.muted} style={{ fontWeight: 600 }}> · we assumed B1</span> : null}</span>
      <div className={styles.filters} role="radiogroup" aria-labelledby="gen-level" style={{ margin: 0 }}>{LEVELS.map(l => <button key={l} type="button" role="radio" aria-checked={spec.cefr === l} className={styles.chip}
        aria-pressed={spec.cefr === l} onClick={() => set({ cefr: l })}>{l}</button>)}</div>
      <p className={styles.muted} style={{ fontSize: 14, margin: '8px 0 0' }}>{LEVEL_HELP[spec.cefr]}</p>

      <span className={styles.fieldLabel} id="gen-accent">Accent</span>
      <div className={styles.radioCards} role="radiogroup" aria-labelledby="gen-accent">
        {AVAILABLE_ACCENTS.map(a => <button key={a} type="button" role="radio" aria-checked={spec.accent === a} className={styles.radioCard} onClick={() => set({ accent: a })}>
          <span className={styles.code} aria-hidden="true">{ACCENT_NOTE[a].code}</span><span><b>{ACCENT_NOTE[a].place}</b><br /><span className={styles.muted} style={{ fontSize: 14 }}>{ACCENT_NOTE[a].feel}</span></span><span className={styles.dot} aria-hidden="true" /></button>)}
      </div>
      <p className={styles.muted} style={{ fontSize: 14, margin: '8px 0 0' }}>Coming soon: {COMING_ACCENTS.join(', ')}.</p>

      <span className={styles.fieldLabel} id="gen-format">Format</span>
      <div className={styles.filters} role="radiogroup" aria-labelledby="gen-format" style={{ margin: 0 }}>{FORMATS.map(f => <button key={f} type="button" role="radio" aria-checked={spec.format === f}
        className={styles.chip} aria-pressed={spec.format === f} onClick={() => set({ format: f })}>{spec.format === f ? <Icon name="check" size={16} /> : null}{FORMAT_LABEL[f]}</button>)}</div>

      <span className={styles.fieldLabel} id="gen-length">Length</span>
      <div className={styles.filters} role="radiogroup" aria-labelledby="gen-length" style={{ margin: 0 }}>{choices.map(m => <button key={m} type="button" role="radio" aria-checked={spec.minutes === m}
        className={styles.chip} aria-pressed={spec.minutes === m} onClick={() => set({ minutes: m })}>{spec.minutes === m ? <Icon name="check" size={16} /> : null}~{m} min</button>)}
        {max < 3 ? <span className={`${styles.chip} ${styles.chipOff}`} aria-disabled="true">Longer · coming soon</span> : null}</div>
    </div>
    <aside className={styles.stickySide} aria-label="Your lesson" style={{ marginTop: 24 }}>
      <div className={styles.summary}>
        <p className={styles.eyebrow}>Your lesson</p><h2 className={`${styles.display} ${styles.h3}`}>{label}</h2>
        <div className={styles.summaryRow}><span>Language</span><span>English</span></div><div className={styles.summaryRow}><span>Level</span><span>{spec.cefr}</span></div>
        <div className={styles.summaryRow}><span>Accent</span><span>{ACCENT_NOTE[spec.accent]?.place ?? accentLabel(spec.accent)}</span></div>
        <div className={styles.summaryRow}><span>Format</span><span>{FORMAT_LABEL[spec.format]} · {VOICES_LABEL[spec.format]}</span></div>
        <div className={styles.summaryRow}><span>Length</span><span>About {spec.minutes} minute{spec.minutes === 1 ? '' : 's'}</span></div>
        <p className={styles.muted} style={{ fontSize: 13, margin: '8px 0 0' }}>Includes audio, a synced transcript and 4 exercises with answer keys. You can leave this screen while it’s built.</p>
      </div>
      <button type="button" className={`${styles.btn} ${styles.btnPrimary} ${styles.btnBlock}`} style={{ marginTop: 16 }} disabled={busy || !canCreate(spec, text)} onClick={() => void create()}>
        {screen === 'reading' ? 'Reading your request…' : busy ? 'Starting…' : !reviewed && text.trim().length >= 3 && spec.topic.trim().length < 3 ? 'Fill in the details' : 'Create lesson'}</button>
      <p className={styles.muted} style={{ textAlign: 'center', fontSize: 13, marginTop: 8 }}>{avail ? `Up to ${avail.per_learner_daily} lessons a day · you can leave this screen` : ''}</p>
    </aside>
  </div></AppShell>;
}
