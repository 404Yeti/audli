'use client';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { AuthGate, useAccount } from '../components/auth-gate';
import { SpokenOnboarding } from '../components/onboarding';
import { AudliMascot } from '../components/audli-mascot';
import { BottomNavigation, SessionShell, type Destination } from '../components/product-shell';
import { ApiError, LessonLifetime, type LessonOperation } from '../lib/api';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { reliableRecognition, SESSION_DURATION_MS, type Recognition } from '../lib/hands-free';
import { recordingUpload } from '../lib/recording';
import { microphonePermission } from '../lib/session';
import type { ConversationPhase } from '../lib/conversation';

type Profile = { name: string; goal: string; focus?: string; interests?: string[]; target_situations?: string[]; completed_attempts: number };
type ProfileResponse = { destination: string; profile: Profile; provider: string; recognition_min_confidence?: number };
type Exercise = { id: string; audio_url: string; completed_attempt_id: string | null };
type Attempt = { id: string; transcription: Recognition; confirmed_text?: string | null };
type Conversation = { state: ConversationPhase; cue_id: string | null; prompt: string | null; active_followup: { id: string } | null; pending_attempt: Attempt | null; result: unknown };
type History = { id: string; exercise_id: string; created_at: string; evaluation: { feedback: string }; adaptation: { focus?: string } };
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const focusCopy: Record<string, string> = { main_idea: 'Follow the speaker’s main point.', details: 'Catch the details that change the meaning.', vocabulary: 'Understand useful words in context.', inference: 'Hear what the speaker means beyond the words.' };
export default function AudliPage() { return <AuthGate><ApplicationFlow/></AuthGate>; }

function ApplicationFlow() {
  const [destination, setDestination] = useState<string | null>(null);
  const [error, setError] = useState('');
  const owner = useRef<LessonLifetime | null>(null);
  async function load(operation: LessonOperation) {
    const data = await operation.api<ProfileResponse>('/profile'); operation.assertCurrent();
    if (!['onboarding','session_ready'].includes(data.destination)) throw new Error('Your listening space could not be restored.');
    setDestination(data.destination); setError('');
  }
  const restore = useEffectEvent(load);
  useEffect(() => {
    const lifetime = new LessonLifetime(); owner.current = lifetime;
    const operation = lifetime.begin();
    // State updates occur only after authenticated profile resolution.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void restore(operation).catch(error => { if (operation.current) setError(error.message); }).finally(() => operation.release());
    return () => lifetime.close();
  }, []);
  async function retry() {
    if (!owner.current?.current) return;
    const operation = owner.current.begin();
    try { await load(operation); } catch (error) { if (operation.current) setError((error as Error).message); } finally { operation.release(); }
  }
  if (!destination) return <main className="restore-screen"><AudliMascot state="Thinking"/><p role="status">Loading your listening space…</p>{error && <div role="alert">{error}<button className="secondary" onClick={retry}>Retry connection</button></div>}</main>;
  if (destination === 'onboarding') return <SpokenOnboarding onComplete={load}/>;
  return <Product/>;
}

function Product() {
  const account = useAccount();
  const voice = useVoiceLifecycle();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [history, setHistory] = useState<History[]>([]);
  const [destination, setDestination] = useState<Destination>('Home');
  const [active, setActive] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [prompt, setPrompt] = useState('');
  const [transcript, setTranscript] = useState<{ title: string; script: string } | null>(null);
  const [eligibleExercise, setEligibleExercise] = useState<string | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const current = useRef<Exercise | null>(null);
  const minimum = useRef(.65);
  const running = useRef(false);
  const retained = useRef<{ exercise: string; blob: Blob } | null>(null);
  const storageKey = 'audli-session:' + account.scope;
  function saveTiming(start: number, status: 'active' | 'complete' | 'paused') {
    try { sessionStorage.setItem(storageKey, JSON.stringify({ startedAt: start, status })); } catch { /* Server checkpoints remain durable without timer storage. */ }
  }
  async function refresh(operation: LessonOperation) {
    const [data, exercise, past] = await operation.wait(() => Promise.all([
      operation.api<ProfileResponse>('/profile'), operation.api<Exercise | null>('/exercises/current'), operation.api<History[]>('/history'),
    ]));
    operation.assertCurrent(); setProfile(data.profile); setHistory(past); current.current = exercise; minimum.current = data.recognition_min_confidence ?? .65;
  }
  async function restore(operation: LessonOperation) {
    await refresh(operation); operation.assertCurrent(); setBusy(false);
    try {
      const timing = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
      if (timing && Number.isFinite(timing.startedAt) && timing.startedAt > 0 && timing.startedAt <= Date.now()) {
        if (timing.status === 'complete' && current.current?.completed_attempt_id) { setCompleted(true); setStartedAt(timing.startedAt); }
        else if (timing.status === 'active' && current.current) { setStartedAt(timing.startedAt); void start(timing.startedAt, false); }
      }
    } catch { /* Local timing never decides onboarding or assessment state. */ }
  }
  const initialRestore = useEffectEvent(restore);
  useEffect(() => {
    const operation = voice.begin();
    // State updates occur only after the external checkpoint resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void initialRestore(operation).catch(error => { if (operation.current) { setError(error.message); setBusy(false); } }).finally(() => operation.release());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (reducedMotion) document.documentElement.dataset.reducedMotion = 'true';
    else delete document.documentElement.dataset.reducedMotion;
    return () => { delete document.documentElement.dataset.reducedMotion; };
  }, [reducedMotion]);
  async function playCue(operation: LessonOperation, exercise: Exercise, state: Conversation) {
    voice.thinking();
    setPrompt(state.prompt ?? '');
    const data = await operation.api<{ audio_url: string }>(`/exercises/${exercise.id}/coach-audio`, json({ cue_id: state.cue_id }));
    await voice.speak(operation, await operation.audio(data.audio_url));
  }
  async function captureAnswer(operation: LessonOperation, exercise: Exercise): Promise<Attempt> {
    let blob = retained.current?.exercise === exercise.id ? retained.current.blob : null;
    if (!blob) { blob = await voice.listen(operation); operation.assertCurrent(); retained.current = { exercise: exercise.id, blob }; }
    voice.thinking();
    const attempt = await operation.api<Attempt>(`/exercises/${exercise.id}/attempts`, { method: 'POST', body: recordingUpload(blob) });
    operation.assertCurrent(); retained.current = null; return attempt;
  }
  async function session(operation: LessonOperation, startTime: number) {
    let exercise = current.current ?? await operation.api<Exercise>('/exercises', { method: 'POST' });
    operation.assertCurrent(); current.current = exercise;
    let state = await operation.api<Conversation>(`/exercises/${exercise.id}/conversation`);
    let retries = 0;
    while (operation.current) {
      setEligibleExercise(state.result ? exercise.id : null);
      if (state.state === 'LISTENING') {
        setPrompt('Listen carefully.');
        await voice.speak(operation, await operation.audio(exercise.audio_url));
        state = await operation.api<Conversation>(`/exercises/${exercise.id}/conversation/listened`, { method: 'POST' });
      } else if (['AWAITING_SUMMARY','AWAITING_FOLLOWUP','ASSESSING','ASSESSING_FOLLOWUP'].includes(state.state)) {
        let attempt = state.pending_attempt;
        if (attempt) retained.current = null; // A lost upload response must not reuse its audio for a follow-up.
        if (!attempt) { await playCue(operation, exercise, state); attempt = await captureAnswer(operation, exercise); }
        while (operation.current) {
          // A correction explicitly confirmed before AUD-17 is already a server checkpoint.
          const confirmed = attempt.confirmed_text?.trim();
          const corrected = confirmed && confirmed !== attempt.transcription.text.trim() ? confirmed : null;
          if (!corrected && !reliableRecognition(attempt.transcription, minimum.current)) {
            if (++retries > 2) throw new Error('Your microphone is hard to hear. Check it, then retry this turn.');
            setPrompt("I didn't quite catch that. Could you say it again?");
            await voice.retrySpeech(operation); attempt = await captureAnswer(operation, exercise); continue;
          }
          voice.thinking();
          try {
            state = await operation.api<Conversation>(`/attempts/${attempt.id}/assess`, json({ text: corrected ?? attempt.transcription.text, confirmed: true, hands_free: !corrected, followup_id: state.active_followup?.id ?? null }));
            retries = 0; break;
          } catch (error) {
            if (!(error instanceof ApiError) || !error.uncertain) throw error;
            if (++retries > 2) throw new Error('Recognition needs another try. Check your microphone, then retry this turn.');
            setPrompt("I didn't quite catch that. Could you say it again?");
            await voice.retrySpeech(operation); attempt = await captureAnswer(operation, exercise);
          }
        }
      } else if (state.state === 'GIVING_FEEDBACK') {
        await playCue(operation, exercise, state);
        state = await operation.api<Conversation>(`/exercises/${exercise.id}/conversation/ready`, { method: 'POST' });
      } else if (state.state === 'READY_FOR_NEXT') {
        if (Date.now() - startTime >= SESSION_DURATION_MS) {
          await refresh(operation); operation.assertCurrent(); saveTiming(startTime, 'complete'); setCompleted(true); setActive(false); return;
        }
        voice.thinking();
        const next = await operation.api<Exercise>('/exercises', { method: 'POST' });
        if (next.id === exercise.id) throw new Error('Your next clip could not be prepared. Retry your session.');
        exercise = next; current.current = exercise; setTranscript(null);
        state = await operation.api<Conversation>(`/exercises/${exercise.id}/conversation`);
      } else throw new Error('Your conversation could not be restored. Please retry.');
    }
  }
  async function start(startTime = Date.now(), preflight = true) {
    if (running.current) return;
    const operation = voice.begin(); running.current = true; setError(''); setBusy(true);
    setActive(true); setEligibleExercise(null); setTranscript(null); voice.thinking();
    let microphoneReady = !preflight;
    try {
      if (preflight) await operation.wait(() => microphonePermission(navigator.mediaDevices));
      microphoneReady = true;
      operation.assertCurrent(); setStartedAt(startTime); saveTiming(startTime, 'active'); setActive(true); setCompleted(false); setBusy(false);
      await session(operation, startTime);
    } catch (error) { if (operation.current) { setError((error as Error).message); setBusy(false); if (!microphoneReady) setActive(false); } }
    finally { if (operation.current) running.current = false; operation.release(); }
  }
  function end() {
    voice.stop(); running.current = false; retained.current = null;
    if (startedAt != null) saveTiming(startedAt, 'paused');
    setActive(false); setBusy(false); setError(''); setPrompt(''); setDestination('Home');
  }
  async function reviewTranscript(exerciseId: string) {
    const operation = voice.begin(); setError('');
    try { const data = await operation.api<{ title: string; script: string }>(`/exercises/${exerciseId}/transcript`); operation.assertCurrent(); setTranscript(data); }
    catch (error) { if (operation.current) setError((error as Error).message); } finally { operation.release(); }
  }
  async function retry() {
    if (active) { running.current = false; await start(startedAt ?? Date.now(), false); }
    else { const operation = voice.begin(); try { await refresh(operation); setError(''); } catch (error) { if (operation.current) setError((error as Error).message); } finally { operation.release(); } }
  }
  async function navigate(value: Destination) {
    setDestination(value); setTranscript(null); setError('');
    if (value !== 'Review') return;
    const operation = voice.begin();
    try { const data = await operation.api<History[]>('/history'); operation.assertCurrent(); setHistory(data); }
    catch (error) { if (operation.current) setError((error as Error).message); } finally { operation.release(); }
  }
  if (active) return <><SessionShell state={voice.state} activity={voice.activity} startedAt={startedAt ?? undefined} onEnd={end} onReplay={voice.state === 'Speaking' ? voice.replay : undefined}>
    <p className="sr-only">{prompt}</p>{error && <div role="alert" className="session-error">{error}<p>{prompt}</p><button className="secondary" onClick={retry}>Retry conversation</button></div>}
    {eligibleExercise && <details className="session-transcript"><summary>Transcript</summary>{transcript ? <p>{transcript.script}</p> : <button className="text-button" onClick={() => void reviewTranscript(eligibleExercise)}>Read transcript</button>}</details>}
  </SessionShell><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/></>;
  return <main className="product-shell"><header><strong className="wordmark">audli</strong><span className="language">TRAIN YOUR EARS</span></header>
    {destination === 'Home' && <section className="home-view"><h1>{completed ? 'Nice work today.' : `Good to see you${profile?.name ? ', ' + profile.name : ''}.`}</h1><AudliMascot state={completed ? 'Success' : 'Idle'}/>
      {completed ? <><p>Your ears have done enough. Audli will use today’s session to shape what comes next.</p><p className="next-session">Next session tomorrow</p><button className="primary" onClick={() => setDestination('Review')}>Review today</button><button className="text-button" disabled={busy} onClick={() => void start()}>Start another session</button></> : <><h2>Today’s listening session</h2><p>10 minutes · English · adapted for you</p><button className="primary" disabled={busy || !profile} onClick={() => void start()}>Start today’s session</button><p className="home-support">Your next session will build from what Audli heard last time.</p></>}
    </section>}
    {destination === 'Review' && <section className="shell-view"><h1>What your ears are learning</h1><p>A simple look at recent listening—not a dashboard.</p>{history.length ? [...history].reverse().slice(0,5).map(item => <article className="review-observation" key={item.id}><h2>{new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(item.created_at))}</h2><p>{item.evaluation.feedback}</p><button className="text-button" onClick={() => void reviewTranscript(item.exercise_id)}>Read transcript</button></article>) : <><AudliMascot/><p>Your listening observations will appear after your first session.</p></>}{transcript && <details className="review-transcript" open><summary>{transcript.title}</summary><p>{transcript.script}</p></details>}</section>}
    {destination === 'Plan' && <section className="shell-view"><h1>Your listening plan</h1><p>Audli adjusts this automatically as it learns how you listen.</p><AudliMascot small/><dl><dt>Goal</dt><dd>{profile?.goal ?? 'Build confidence understanding English'}</dd><dt>Current focus</dt><dd>{profile?.completed_attempts ? focusCopy[profile.focus ?? 'details'] ?? 'Build on what you understood.' : 'Find your starting point through listening.'}</dd><dt>Session</dt><dd>10 minutes daily</dd><dt>Starting pace</dt><dd>Clear, slightly slower English</dd></dl></section>}
    {destination === 'Settings' && <section className="shell-view"><h1>Settings</h1><dl><dt>Account</dt><dd>{account.email ?? 'Local development account'}{account.signOut && <button className="text-button" onClick={() => void account.signOut?.()}>Sign out</button>}</dd><dt>Voice & playback</dt><dd>Audli speaks automatically. Replay is available while Audli is speaking.</dd><dt>Accessibility</dt><dd><label className="toggle"><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)}/>Reduce motion</label><p>System reduced-motion preferences always apply.</p></dd><dt>Privacy</dt><dd>Your microphone recording is discarded after transcription. Your listening progress is saved to your account.</dd><dt>Help</dt><dd>Allow microphone access in your browser to speak with Audli.</dd></dl></section>}
    {busy && <p role="status">Loading your listening space…</p>}{error && <div role="alert" className="session-error">{error}<button className="secondary" onClick={retry}>Retry connection</button></div>}
    <BottomNavigation destination={destination} onNavigate={value => void navigate(value)}/><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/>
  </main>;
}
