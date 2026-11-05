'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import styles from './listening.module.css';
import { captureRecording, recordingPreview, type RecordingPreview } from '../../lib/recording.ts';
import { clock, MAX_ANSWER_SECONDS, MIN_ANSWER_SECONDS, type Exercise } from '../../lib/listening-core.ts';

type Stage = 'ready' | 'starting' | 'recording' | 'recorded';
type Capture = ReturnType<typeof captureRecording>;

/**
 * Answer aloud. The learner records (or picks a file), listens back if they like, then sends it. Nothing here waits for the AI:
 * once the recording is accepted the lesson moves on and the result arrives in the debrief.
 */
export function SpokenExercise({ exercise, busy, error, onSend, onSkip }: {
  exercise: Exercise; busy: boolean; error: string | null; onSend: (blob: Blob) => void; onSkip?: () => void;
}) {
  const [stage, setStage] = useState<Stage>('ready');
  const [seconds, setSeconds] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [preview, setPreview] = useState<RecordingPreview | null>(null);
  const capture = useRef<Capture | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const canRecord = typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined';

  const stopTimer = () => { if (timer.current) clearInterval(timer.current); timer.current = null; };
  useEffect(() => () => { stopTimer(); capture.current?.dispose(); }, []);
  useEffect(() => () => { preview?.dispose(); }, [preview]);

  const keep = useCallback((next: Blob) => {
    setBlob(next);
    try { setPreview(recordingPreview(next)); } catch { setPreview(null); }
    setStage('recorded'); setNote(null);
  }, []);

  async function start() {
    setNote(null); setStage('starting');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      capture.current = captureRecording(stream, next => { stopTimer(); keep(next); }, message => { stopTimer(); setStage('ready'); setNote(message); });
      await capture.current.ready;
      setSeconds(0); setStage('recording');
      timer.current = setInterval(() => setSeconds(value => {
        if (value + 1 >= MAX_ANSWER_SECONDS) capture.current?.stop();
        return value + 1;
      }), 1000);
    } catch {
      capture.current?.dispose(); capture.current = null; stopTimer(); setStage('ready');
      setNote('We couldn’t use your microphone. You can allow it in your browser settings, upload an audio file instead, or skip this question.');
    }
  }
  const stop = () => {
    if (seconds < MIN_ANSWER_SECONDS) { setNote('Keep going — say a little more before you stop.'); return; }
    capture.current?.stop();
  };
  const again = () => { setBlob(null); setPreview(null); setStage('ready'); setNote(null); };

  return <section className={styles.card} aria-labelledby={`spoken-${exercise.id}`}>
    <p className={styles.kicker}>Question {exercise.position} · Say it aloud</p>
    <h2 id={`spoken-${exercise.id}`} className={`${styles.heading} ${styles.prompt}`}>{exercise.prompt}</h2>
    {exercise.spoken?.status === 'receiving' ? <p className={styles.note}>We didn’t finish saving your last recording. Please record again.</p> : null}
    <div className={styles.recorder} data-stage={stage}>
      {stage === 'ready' || stage === 'starting' ? <>
        <button type="button" className={styles.primary} onClick={() => void start()} disabled={!canRecord || busy || stage === 'starting'}>
          {stage === 'starting' ? 'Starting…' : 'Start recording'}
        </button>
        <label className={styles.fileLabel}>Or upload an audio file
          <input className={styles.file} type="file" accept="audio/*" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) keep(file); event.target.value = ''; }} />
        </label>
      </> : null}
      {stage === 'recording' ? <>
        <p className={styles.recording} role="status"><span className={styles.recDot} aria-hidden="true" /> Recording {clock(seconds)}</p>
        <button type="button" className={styles.primary} onClick={stop}>Stop</button>
        <p className={styles.note}>Up to {MAX_ANSWER_SECONDS} seconds. Take your time.</p>
      </> : null}
      {stage === 'recorded' && blob ? <>
        {preview ? <audio className={styles.preview} controls src={preview.url} aria-label="Listen to your recording" /> : null}
        <button type="button" className={styles.primary} onClick={() => onSend(blob)} disabled={busy}>{busy ? 'Sending…' : 'Send answer'}</button>
        <button type="button" className={styles.secondary} onClick={again} disabled={busy}>Record again</button>
      </> : null}
    </div>
    {!canRecord ? <p className={styles.playerMsg} role="status">Recording isn’t available in this browser. You can upload an audio file instead, or skip this question.</p> : null}
    {note ? <p className={styles.playerMsg} role="alert">{note}</p> : null}
    {error ? <p className={styles.playerMsg} role="alert">{error}</p> : null}
    {onSkip ? <button type="button" className={styles.linkBtn} onClick={onSkip} disabled={busy || stage === 'recording'}>Skip this question</button> : null}
    <p className={styles.note}>Your recording is used only to turn your answer into text, then it is deleted.</p>
  </section>;
}

/** Shown right after the recording is accepted: the lesson carries on while the answer is checked in the background. */
export function SpokenSent({ skipped }: { skipped: boolean }) {
  return <section className={styles.card}>
    <p className={styles.kicker}>Spoken answer</p>
    <h2 className={`${styles.heading} ${styles.prompt}`}>{skipped ? 'Skipped' : 'Answer sent'}</h2>
    <p className={styles.note} role="status">{skipped ? 'You can move on. This question won’t be scored.' : 'We’ll check it while you keep going. Your result will be waiting in the summary.'}</p>
  </section>;
}
