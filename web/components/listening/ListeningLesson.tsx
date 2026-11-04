'use client';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import styles from './listening.module.css';
import { LessonShell } from './LessonShell';
import { LessonPlayer, type PlayerHandle } from './LessonPlayer';
import { TranscriptViewer } from './TranscriptViewer';
import { MultipleChoiceExercise } from './MultipleChoiceExercise';
import { AttemptRecovery, ResumedBanner } from './AttemptRecovery';
import { AudliMascot } from '../audli-mascot';
import { ApiError } from '../../lib/api.ts';
import { currentAttemptId, getAttempt, rememberStarted, startAttempt, submitAnswer, wasStarted } from '../../lib/listening.ts';
import { phaseOf, type AttemptView } from '../../lib/listening-core.ts';

type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; view: AttemptView; resumed: boolean };

export function ListeningLesson({ lessonId }: { lessonId: string }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [started, setStarted] = useState(false);
  const [guided, setGuided] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    if (result.kind === 'ready') setStarted(wasStarted(result.view.attempt.id));
    setLoad(result);
  }, []);
  useEffect(() => {
    let live = true;
    void find(false).then(result => { if (live) apply(result); });
    return () => { live = false; };
  }, [find, apply]);
  const open = async (fresh = false) => { setLoad({ kind: 'loading' }); setError(null); setActive(null); apply(await find(fresh)); };

  const onActive = useCallback((index: number | null) => setActive(index), []);
  const onPlaying = useCallback((value: boolean) => setPlaying(value), []);

  if (load.kind === 'loading') return <LessonShell phase={null}><AttemptRecovery state="loading" /></LessonShell>;
  if (load.kind === 'error') return <LessonShell phase={null}><AttemptRecovery state="error" onRetry={() => void open()} /></LessonShell>;

  const { view, resumed } = load;
  const phase = phaseOf(view.attempt.status, started);
  const exercise = view.exercises[0];
  const showTranscript = guided && phase !== 'question';
  const correct = exercise?.feedback?.is_correct;

  async function answer(optionId: string) {
    setBusy(true); setError(null);
    try { setLoad({ kind: 'ready', resumed, view: await submitAnswer(view.attempt.id, exercise.id, optionId) }); }
    catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) { try { setLoad({ kind: 'ready', resumed, view: await getAttempt(view.attempt.id) }); return; } catch { /* fall through */ } }
      setError('We couldn’t save that answer. Check your connection and try again.');
    } finally { setBusy(false); }
  }
  const begin = () => { rememberStarted(view.attempt.id); setStarted(true); };

  const actions = phase === 'listen' ? <>
    <button type="button" className={styles.primary} onClick={begin}>Start exercises</button>
    <p className={styles.note}>Replay as often as you like</p>
  </> : phase === 'result' ? <>
    <button type="button" className={styles.primary} onClick={() => { setStarted(false); void open(true); }}>Listen again</button>
    <Link href="/listening" className={styles.secondary}>Back to lessons</Link>
  </> : null;

  return <LessonShell phase={phase} wide={showTranscript} actions={actions}>
    {resumed ? <ResumedBanner /> : null}
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
        {phase !== 'listen' && exercise ? <div style={{ marginTop: 16 }}>
          {phase === 'result' ? <div className={styles.result}>
            <div className={styles.resultMascot}><AudliMascot state={correct ? 'Success' : 'Idle'} small /></div>
            <h2 className={`${styles.heading}`} style={{ fontSize: 22 }}>{correct ? 'Nice listening' : 'Good effort'}</h2>
            <p className={styles.note}>{view.attempt.correct} of {view.attempt.total} correct</p>
          </div> : null}
          <MultipleChoiceExercise key={exercise.id} exercise={exercise} busy={busy} error={error} onSubmit={answer} />
        </div> : null}
      </div>
    </div>
  </LessonShell>;
}
