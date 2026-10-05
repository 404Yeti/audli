'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { captureRecording, recordingDiagnostic, recordingPreview, previewSource, recordingUpload, type RecordingPreview } from '../lib/recording';

type Difficulty = { speech_rate: number; duration_seconds: number; vocabulary_level: string; information_density: number };
type Profile = { name: string; goal: string; completed_attempts: number; difficulty: Difficulty };
type Exercise = { id: string; audio_url: string; difficulty: Difficulty; completed_attempt_id: string | null };
type Attempt = { id: string; transcription: { text: string; confidence: number | null; uncertainty: string[]; source: string } };
type Evaluation = { overall: number; main_idea: number; details: number; vocabulary: number; inference: number; feedback: string; understood: string[]; missed: string[]; misunderstood: string[] };
type Adaptation = { decision: string; changed_variable: string | null; old_value: string | number | null; new_value: string | number | null; reason: string; focus: string };
type Result = { evaluation: Evaluation; adaptation: Adaptation };
const labels: Record<string, string> = { main_idea: 'Main idea', details: 'Details', vocabulary: 'Vocabulary understanding', inference: 'Inference', speech_rate: 'Speech speed', duration_seconds: 'Clip duration', vocabulary_level: 'Vocabulary level', information_density: 'Information density' };

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export default function Audli() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [mode, setMode] = useState('');
  const [exercise, setExercise] = useState<Exercise | null>(null);
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [busy, setBusy] = useState('Loading your listening space…');
  const [error, setError] = useState('');
  const [listened, setListened] = useState(false);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [preview, setPreview] = useState<RecordingPreview | null>(null);
  const recordingUrl = previewSource(preview, blob);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [text, setText] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [transcript, setTranscript] = useState<{ title: string; script: string } | null>(null);
  const capture = useRef<ReturnType<typeof captureRecording> | null>(null);
  const mounted = useRef(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const listeningAudio = useRef<HTMLAudioElement | null>(null);
  const recordingAudio = useRef<HTMLAudioElement | null>(null);

  async function load() {
    const [data, current] = await Promise.all([api<{ profile: Profile; provider: string }>('/profile'), api<Exercise | null>('/exercises/current')]);
    setProfile(data.profile); setMode(data.provider); setName(data.profile.name); setGoal(data.profile.goal); setExercise(current);
    if (current?.completed_attempt_id) setResult(await api<Result>(`/attempts/${current.completed_attempt_id}`));
  }
  useEffect(() => {
    mounted.current = true;
    load().catch(e => setError(e.message)).finally(() => setBusy(''));
    return () => { mounted.current = false; if (timer.current) clearInterval(timer.current); capture.current?.dispose(); capture.current = null; };
  }, []);
  useEffect(() => {
    if (!blob) { setPreview(null); return; }
    try {
      const owned = recordingPreview(blob);
      setPreview(owned);
      return () => owned.dispose();
    } catch {
      setPreview(null); setError('Recording preview could not be created. You can still try transcription.');
    }
  }, [blob]);

  async function run(message: string, action: () => Promise<void>) {
    setError(''); setBusy(message);
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : 'Something went wrong. Please try again.'); }
    finally { setBusy(''); }
  }
  async function next() {
    await run('Preparing your listening clip…', async () => {
      if (!exercise) await api('/profile', json('PUT', { name, goal }));
      const nextExercise = await api<Exercise>('/exercises', { method: 'POST' });
      setExercise(nextExercise); setResult(null); setTranscript(null); setAttempt(null); setBlob(null); setText(''); setListened(false); setConfirmed(false);
      const data = await api<{ profile: Profile; provider: string }>('/profile'); setProfile(data.profile);
    });
  }
  function stop() { capture.current?.stop(); }
  async function startRecording() {
    await run('Opening your microphone…', async () => {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('Recording needs a supported browser on localhost or HTTPS. You can upload a recording below.');
      listeningAudio.current?.pause();
      recordingAudio.current?.pause();
      const input = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current) { input.getTracks().forEach(track => track.stop()); return; }
      capture.current?.dispose();
      const finish = () => {
        if (timer.current) { clearInterval(timer.current); timer.current = null; }
        setRecording(false);
      };
      const session = captureRecording(input, captured => {
        if (!mounted.current || capture.current !== session) return;
        finish(); setBlob(captured); setAttempt(null); setText(''); setConfirmed(false);
      }, message => {
        if (!mounted.current || capture.current !== session) return;
        finish(); setError(message);
      });
      capture.current = session;
      // Preserve the previous recording until microphone acquisition and start both succeed.
      setBlob(null); setAttempt(null); setText(''); setConfirmed(false); setElapsed(0); setRecording(true);
      const started = Date.now();
      timer.current = setInterval(() => {
        const seconds = Math.floor((Date.now()-started)/1000); setElapsed(seconds);
        // Leave headroom below the backend's 120-second cap.
        if (seconds >= 119) session.stop();
      }, 250);
    });
  }
  async function transcribe() {
    if (!blob || !exercise || recording) return;
    const captured = blob;
    const exerciseId = exercise.id;
    await run('Listening to your summary…', async () => {
      const form = recordingUpload(captured);
      recordingDiagnostic('transcription_upload_started', { uploadSize: captured.size, mimeType: captured.type });
      try {
        const data = await api<Attempt>(`/exercises/${exerciseId}/attempts`, { method: 'POST', body: form },
          status => recordingDiagnostic('transcription_response', { status }));
        if (!data || typeof data.id !== 'string' || typeof data.transcription?.text !== 'string'
            || !Array.isArray(data.transcription.uncertainty)) throw new Error('Audli returned an invalid transcription response. Please try again.');
        setAttempt(data); setText(data.transcription.text); setConfirmed(false);
        // Only release the Blob after a usable transcription. Errors preserve preview/retry.
        setBlob(current => current === captured ? null : current);
        recordingDiagnostic('transcription_completed');
      } catch (error) {
        recordingDiagnostic('transcription_failed', { recordingRetained: true });
        throw error;
      }
    });
  }
  async function evaluate() {
    if (!attempt) return;
    await run('Finding what you understood…', async () => {
      recordingDiagnostic('evaluation_started');
      const data = await api<Result>(`/attempts/${attempt.id}/evaluate`, json('POST', { text, confirmed }),
        status => recordingDiagnostic('evaluation_response', { status }));
      setResult(data); setAttempt(null);
      recordingDiagnostic('feedback_received');
      const updated = await api<{ profile: Profile; provider: string }>('/profile'); setProfile(updated.profile);
    });
  }

  return <main>
    <header><div className="brand"><svg width="38" height="38" viewBox="0 0 40 40" aria-hidden="true"><path d="M8 23v-5a12 12 0 0 1 24 0v5" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round"/><rect x="5" y="20" width="7" height="13" rx="3.5" fill="currentColor"/><rect x="28" y="20" width="7" height="13" rx="3.5" fill="currentColor"/><path d="M17 19v9m6-13v17" stroke="#ef8876" strokeWidth="3" strokeLinecap="round"/></svg><span>audli<span className="brand-dot">.</span></span></div><span className="language">English · V0.1</span></header>
    <div className="intro"><p className="eyebrow">YOUR LISTENING COMPANION</p><h1>Train your ears.</h1><p>A little listening. A little discovery.<br/>One step closer to understanding.</p></div>
    {mode === 'demo' && <p className="notice">Demo mode: original sample audio, manual transcription, and fixed synthetic scores. Use OpenAI mode to test learning quality.</p>}
    {error && <div role="alert" className="error">{error}{!profile && <button onClick={() => run('Connecting…', load)}>Retry connection</button>}</div>}
    {busy && <p className="status" role="status">{busy}</p>}
    {profile && !exercise && <section className="card"><p className="eyebrow">LET’S FIND YOUR STARTING POINT</p><h2>Make yourself at home.</h2><form onSubmit={e => { e.preventDefault(); next(); }}>
      <label htmlFor="name">What should we call you?</label><input id="name" maxLength={80} required value={name} onChange={e => setName(e.target.value)} placeholder="Your name"/>
      <label htmlFor="goal">What do you want to improve your listening for?</label><textarea id="goal" maxLength={500} required value={goal} onChange={e => setGoal(e.target.value)} placeholder="For example, software engineering meetings"/>
      <p className="hint">We’ll start with clear, slower English. Your first three clips help us find your listening edge.</p><button className="primary" disabled={!!busy || !name.trim() || !goal.trim()}>Let’s listen <span aria-hidden="true">→</span></button>
    </form></section>}
    {exercise && <section className="card">
      <div className="card-top"><p className="eyebrow">{!result && profile && profile.completed_attempts < 3 ? `FINDING YOUR STARTING POINT · ${profile.completed_attempts + 1}/3` : 'YOUR LISTENING PRACTICE'}</p><span className="pill">{exercise.difficulty.vocabulary_level} · ~{exercise.difficulty.duration_seconds}s</span></div>
      {!result ? <><h2>Listen carefully.</h2><p>You won’t see the transcript yet. Listen for the main idea, what happened, and why.</p>
        <div className="audio-panel"><span className="audio-icon" aria-hidden="true">▶</span><strong>Your English listening clip</strong>{exercise.audio_url?.trim() && <audio ref={listeningAudio} key={exercise.id} controls src={exercise.audio_url} preload="none" aria-label="Play listening exercise" onEnded={() => setListened(true)} onError={() => setError('Audio could not load. Check the server and reload the page.')} />}</div>
        <p className="hint">AI-generated voice. Replay whenever you need.</p>
        <div className="divider"/><h3>Tell Audli what you understood.</h3><p>Summarize in your own words. Grammar doesn’t affect your comprehension score.</p>
        {!listened && <p className="hint">Play the clip to the end to unlock recording.</p>}
        <button className={`record ${recording ? 'active' : ''}`} disabled={!!busy || !listened} onClick={recording ? stop : startRecording}><span aria-hidden="true">{recording ? '■' : '●'}</span> {recording ? `Stop recording · ${elapsed}s` : blob || attempt ? 'Record again' : 'Tap to record'}</button>
        <p className="hint">Tap once to start, once to stop. Up to 2 minutes.</p>
        {listened && !recording && !attempt && <details className="upload"><summary>Use an existing spoken recording</summary><label htmlFor="upload">Upload your summary (2–120 seconds, up to 12 MB)</label><input id="upload" type="file" accept="audio/webm,audio/mp4,audio/mpeg,audio/wav,audio/ogg" disabled={!!busy} onChange={e => { const file=e.target.files?.[0]; if (file) { setBlob(file); setAttempt(null); setConfirmed(false); } }}/></details>}
        {blob && !recording && <div className="review"><h3>Your recording</h3>{recordingUrl && <audio ref={recordingAudio} controls src={recordingUrl} aria-label="Review your recorded summary" onLoadedMetadata={() => recordingDiagnostic('local_playback_ready')} onPlay={() => recordingDiagnostic('local_playback_started')}/> }<button className="primary" disabled={!!busy} onClick={transcribe}>Transcribe my summary</button></div>}
        {attempt && <div className="review"><h3>Did we hear you correctly?</h3>{attempt.transcription.uncertainty.map((u, i) => <p className="notice" key={i}>{u}</p>)}<label htmlFor="summary">Your spoken summary</label><textarea id="summary" value={text} maxLength={8000} onChange={e => { setText(e.target.value); setConfirmed(false); }}/><p className="hint">Correct recognition errors to match what you said. An uncertain recording won’t lower your score.</p><label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/> This matches what I said.</label><button className="primary" onClick={evaluate} disabled={!!busy || !confirmed || text.trim().split(/\s+/).length < 3}>See what I understood <span aria-hidden="true">→</span></button></div>}
      </> : <><p className="eyebrow">HERE’S WHAT YOU CAUGHT</p><div className="score"><span>{Math.round(result.evaluation.overall*100)}<small>/100</small></span><h2>Audli Score</h2></div><div className="dimensions">{(['main_idea','details','vocabulary','inference'] as const).map(key => <div key={key}><div><span>{labels[key]}</span><strong>{Math.round(result.evaluation[key]*100)}%</strong></div><progress max={1} value={result.evaluation[key]} aria-label={labels[key]}/></div>)}</div><p className="feedback">{result.evaluation.feedback}</p>
        {(['understood','missed','misunderstood'] as const).map(key => result.evaluation[key].length > 0 && <details className="findings" key={key}><summary>{key === 'understood' ? 'What you understood' : key === 'missed' ? 'What to listen for next time' : 'What sounded different'} ({result.evaluation[key].length})</summary><ul>{result.evaluation[key].map((item, i) => <li key={i}>{item}</li>)}</ul></details>)}
        <div className="next"><p className="eyebrow">NEXT EXERCISE</p><h3>{result.adaptation.changed_variable ? `${labels[result.adaptation.changed_variable]}: ${result.adaptation.old_value} → ${result.adaptation.new_value}` : 'Keeping your listening level steady.'}</h3><p>{result.adaptation.reason}</p><button className="primary" onClick={next} disabled={!!busy}>Continue <span aria-hidden="true">→</span></button></div>
        {!transcript ? <button className="secondary" disabled={!!busy} onClick={() => run('Opening transcript…', async () => setTranscript(await api<{ title: string; script: string }>(`/exercises/${exercise.id}/transcript`)))}>Show transcript</button> : <div className="transcript"><h3>{transcript.title}</h3><p>{transcript.script}</p></div>}
      </>}
    </section>}
    <footer>Listen. Understand. Grow.<span>Your voice is used for transcription; Audli doesn’t retain the recording.</span></footer>
  </main>;
}
