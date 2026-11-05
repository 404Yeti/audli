'use client';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import styles from './listening.module.css';
import { LessonShell } from './LessonShell';
import { LessonPlayer, type PlayerHandle } from './LessonPlayer';
import { TranscriptViewer } from './TranscriptViewer';
import { MultipleChoiceExercise } from './MultipleChoiceExercise';
import { FillBlankExercise } from './FillBlankExercise';
import { SequencingExercise } from './SequencingExercise';
import { SpokenExercise, SpokenSent } from './SpokenExercise';
import { StatusPill } from './StatusPill';
import { Debrief } from './Debrief';
import { AttemptRecovery, ResumedBanner } from './AttemptRecovery';
import { AudliMascot } from '../audli-mascot';
import { ApiError } from '../../lib/api.ts';
import { successPath } from '../../lib/success-core.ts';
import { currentAttemptId, getAttempt, rememberStarted, retrySpoken, sendRecording, skipSpoken, startAttempt, submitAnswer, wasStarted, type AnswerBody } from '../../lib/listening.ts';
import { firstUndecided, hasPendingAssessment, newRequestId, phaseOf, pollDelayMs, resumePosition, type AttemptView, type Exercise } from '../../lib/listening-core.ts';

type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; view: AttemptView; resumed: boolean };

export function ListeningLesson({ lessonId }: { lessonId: string }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [started, setStarted] = useState(false);
  const [guided, setGuided] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);   // the question just answered, kept on screen until the learner continues
  const uploads = useRef(new WeakMap<Blob, string>());                // one id per recording, so sending it again is never a second answer
  const player = useRef<PlayerHandle>(null);

  // Find the saved attempt for this lesson, or start one. Pure async: callers decide when to show "loading".
  const find = useCallback(async (fresh: boolean): Promise<Load> => {
    try {
      const existing = fresh ? null : await currentAttemptId(lessonId);
      const view = existing ? await getAttempt(existing) : await startAttempt(lessonId, fresh);
      // "Welcome back" only for work saved by an earlier visit, not for the attempt this very load created.
      const resumed = !!existing && view.attempt.status === 'in_progress' && Date.now() - Date.parse(view.attempt.created_at) > 10_000;
      return { kind: 'ready', view, resumed };
    } catch { return { kind: 'error' }; }
  }, [lessonId]);
  const apply = useCallback((result: Load) => {
    if (result.kind === 'ready') setStarted(wasStarted(result.view.attempt.id) || resumePosition(result.view.exercises).started);   // saved answers mean we skip the intro
    setLoad(result);
  }, []);
  useEffect(() => {
    let live = true;
    void find(false).then(result => { if (live) apply(result); });
    return () => { live = false; };
  }, [find, apply]);
  const open = async (fresh = false) => { setLoad({ kind: 'loading' }); setError(null); setActive(null); setReviewing(null); apply(await find(fresh)); };

  const phaseNow = load.kind === 'ready' ? (reviewing ? 'question' : phaseOf(load.view.attempt.status, started)) : null;
  const lastPhase = useRef<string | null>(null);
  useEffect(() => {
    if (phaseNow && lastPhase.current && lastPhase.current !== phaseNow) document.getElementById('lesson-main')?.focus();
    lastPhase.current = phaseNow;
  }, [phaseNow]);

  // While a spoken answer is being checked in the background, ask again now and then. Pure async callbacks, no set-state in the effect body.
  const attemptId = load.kind === 'ready' ? load.view.attempt.id : null;
  const waiting = load.kind === 'ready' && hasPendingAssessment(load.view.exercises);
  useEffect(() => {
    if (!attemptId || !waiting) return;
    let live = true, polls = 0, timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      timer = setTimeout(async () => {
        try { const next = await getAttempt(attemptId); if (live) setLoad(previous => previous.kind === 'ready' ? { ...previous, view: next } : previous); } catch { /* keep the last known state and try again */ }
        polls += 1;
        if (live) tick();
      }, pollDelayMs(polls));
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [attemptId, waiting]);

  const onActive = useCallback((index: number | null) => setActive(index), []);
  const onPlaying = useCallback((value: boolean) => setPlaying(value), []);

  if (load.kind === 'loading') return <LessonShell phase={null}><AttemptRecovery state="loading" /></LessonShell>;
  if (load.kind === 'error') return <LessonShell phase={null}><AttemptRecovery state="error" onRetry={() => void open()} /></LessonShell>;

  const { view, resumed } = load;
  const phase = reviewing ? 'question' : phaseOf(view.attempt.status, started);
  const current: Exercise | undefined = reviewing ? view.exercises.find(e => e.id === reviewing) : firstUndecided(view.exercises);
  const showTranscript = guided && phase !== 'question';
  const lastQuestion = view.exercises.every(e => e.id === current?.id || firstUndecided([e]) === undefined);

  async function save(call: () => Promise<AttemptView>, exerciseId: string) {
    setBusy(true); setError(null);
    try { setLoad({ kind: 'ready', resumed, view: await call() }); setReviewing(exerciseId); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) { try { setLoad({ kind: 'ready', resumed, view: await getAttempt(view.attempt.id) }); return; } catch { /* fall through */ } }
      setError(cause instanceof ApiError && ((cause.status >= 400 && cause.status < 500 && cause.status !== 409 && cause.status !== 401) || cause.status === 503)
        ? cause.message : 'We couldn’t save that. Check your connection and try again.');
    } finally { setBusy(false); }
  }
  const answer = (exercise: Exercise, body: AnswerBody) => save(() => submitAnswer(view.attempt.id, exercise.id, body), exercise.id);
  const sendVoice = (exercise: Exercise, blob: Blob) => {
    if (!uploads.current.has(blob)) uploads.current.set(blob, newRequestId());
    return save(() => sendRecording(view.attempt.id, exercise.id, blob, uploads.current.get(blob)!), exercise.id);
  };
  const skip = (exercise: Exercise) => save(() => skipSpoken(view.attempt.id, exercise.id), exercise.id);
  // From the summary: not part of the question flow, so no "continue" step.
  async function fromSummary(call: () => Promise<AttemptView>) {
    setBusy(true); setError(null);
    try { setLoad({ kind: 'ready', resumed, view: await call() }); }
    catch (cause) { setError(cause instanceof ApiError && cause.status < 500 ? cause.message : 'We couldn’t do that. Please try again.'); }
    finally { setBusy(false); }
  }
  const begin = () => { rememberStarted(view.attempt.id); setStarted(true); };
  const next = () => { setReviewing(null); setError(null); };

  const actions = phase === 'listen' ? <>
    <button type="button" className={styles.primary} onClick={begin}>Start exercises</button>
    <p className={styles.note}>Replay as often as you like</p>
  </> : phase === 'result' ? <>
    <Link href={successPath(view.lesson.lesson_id, view.attempt.id)} className={styles.primary}>Finish lesson</Link>
    <button type="button" className={styles.secondary} onClick={() => { setStarted(false); void open(true); }}>Listen again</button>
    <Link href="/listening" className={styles.secondary}>Back to lessons</Link>
  </> : reviewing ? <button type="button" className={styles.primary} onClick={next}>{lastQuestion ? 'See your summary' : 'Next question'}</button> : null;

  const spokenPending = hasPendingAssessment(view.exercises);
  return <LessonShell phase={phase} wide={showTranscript} actions={actions}>
    {resumed && phase !== 'result' && !reviewing ? <ResumedBanner question={resumePosition(view.exercises).next} total={view.attempt.total} /> : null}
    {phase !== 'question' ? <div className={styles.mode}>
      <div className={styles.modeGroup} role="group" aria-label="Transcript">
        <button type="button" className={styles.modeBtn} aria-pressed={!guided} onClick={() => setGuided(false)}>Challenge</button>
        <button type="button" className={styles.modeBtn} aria-pressed={guided} onClick={() => setGuided(true)}>Guided</button>
      </div>
    </div> : null}
    <div className={`${styles.columns} ${showTranscript ? styles.guided : ''}`}>
      {showTranscript ? <div>
        <h1 className={`${styles.heading} ${styles.title}`} style={{ textAlign: 'center', fontSize: 22, marginBottom: 12 }}>{view.lesson.title}</h1>
        <TranscriptViewer sentences={view.lesson.sentences} speakers={view.lesson.speakers} cues={view.audio.cues} activeIndex={active}
          playing={playing} onReplay={ms => player.current?.playFrom(ms)} />
      </div> : null}
      <div className={showTranscript ? styles.sidePlayer : undefined}>
        {phase === 'listen' && !showTranscript ? <div className={styles.hero}>
          <div className={styles.heroMascot}><AudliMascot state={playing ? 'Listening' : 'Idle'} small /></div>
          <h1 className={`${styles.heading} ${styles.title}`}>{view.lesson.title}</h1>
          <div className={styles.tags}><span className={`${styles.tag} ${styles.level}`}>{view.lesson.cefr}</span><span className={styles.tag}>Dialogue</span></div>
        </div> : null}
        {phase === 'listen' ? <div className={styles.hint}>
          <div><span className={styles.hintLabel}>Listen for</span><p className={styles.hintText}>{view.lesson.listen_for}</p></div>
        </div> : null}
        {phase !== 'listen' && !showTranscript ? <h1 className={`${styles.heading}`} style={{ fontSize: 20, margin: '0 0 12px' }}>{view.lesson.title}</h1> : null}
        <LessonPlayer handle={player} attemptId={view.attempt.id} durationMs={view.audio.duration_ms} cues={view.audio.cues}
          speakers={view.lesson.speakers} compact={phase === 'question'} onActive={onActive} onPlaying={onPlaying} />
        {phase === 'question' && current ? <div style={{ marginTop: 16 }}>
          {spokenPending ? <p className={styles.pillRow}><StatusPill outcome="pending" live label="Spoken answer: being checked — keep going" /></p> : null}
          {current.kind === 'multiple_choice' ? <MultipleChoiceExercise key={current.id} exercise={current} busy={busy} error={error} onSubmit={id => void answer(current, { option_id: id })} /> : null}
          {current.kind === 'fill_blank' ? <FillBlankExercise key={current.id} exercise={current} busy={busy} error={error} onSubmit={text => void answer(current, { text })} /> : null}
          {current.kind === 'sequencing' ? <SequencingExercise key={current.id} exercise={current} busy={busy} error={error} onSubmit={order => void answer(current, { order })} /> : null}
          {current.kind === 'spoken' ? (current.spoken && current.spoken.status !== 'receiving'
            ? <SpokenSent skipped={current.spoken.status === 'skipped'} />
            : <SpokenExercise key={current.id} exercise={current} busy={busy} error={error} onSend={blob => void sendVoice(current, blob)} onSkip={() => void skip(current)} />) : null}
        </div> : null}
        {phase === 'result' ? <div style={{ marginTop: 16 }}>
          <Debrief view={view} busy={busy} error={error} onCheckAgain={id => void fromSummary(() => retrySpoken(view.attempt.id, id))}
            onSend={blob => { const spokenQ = view.exercises.find(e => e.kind === 'spoken'); if (spokenQ) { if (!uploads.current.has(blob)) uploads.current.set(blob, newRequestId()); void fromSummary(() => sendRecording(view.attempt.id, spokenQ.id, blob, uploads.current.get(blob)!)); } }} />
        </div> : null}
      </div>
    </div>
  </LessonShell>;
}
