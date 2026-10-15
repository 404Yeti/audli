'use client';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { authenticatedFetch, type LessonOperation } from '../lib/api';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { reliableRecognition, type Recognition } from '../lib/hands-free';
import { recordingUpload, sendRecording, InterruptedRecording } from '../lib/recording';
import { SessionShell } from './product-shell';
import { useAccount } from './auth-gate';
import { conversationIntent } from '../lib/conversation-intent';
import { measureTurn, turnTiming, measuredAudioBody } from '../lib/turn-timing';
import { AudliMascot } from './audli-mascot';

type Profile = { name: string; goal: string; target_language: string; interests: string[]; target_situations: string[]; onboarding_status: string };
type Checkpoint = { goal_entry_available?: boolean; goal_question_revision?: number; name_entry_available?: boolean; name_question_revision?: number | null; profile: Profile; stage: string; welcome?: 'introduction' | 'status' | null; revision: number; prompt: string; destination: string; pending: Recognition | null; clarification_paused?: boolean };
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function SpokenOnboarding({ onComplete }: { onComplete: (operation: LessonOperation) => Promise<void> }) {
  const voice = useVoiceLifecycle();
  const intent = conversationIntent(useAccount().scope, 'onboarding');
  const [checkpoint, setCheckpoint] = useState<Checkpoint | null>(null);
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [savingGoal, setSavingGoal] = useState(false);
  const goalSubmission = useRef(false);
  const [savingName, setSavingName] = useState(false);
  const nameSubmission = useRef(false);
  const [active, setActive] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const running = useRef(false);
  const minimum = useRef(.65);
  const retained = useRef<{ audio: Blob; revision: number } | null>(null);
  async function restore(operation: LessonOperation) {
    const data = await operation.api<Checkpoint>('/onboarding');
    const settings = await operation.api<{ recognition_min_confidence?: number }>('/profile');
    operation.assertCurrent(); minimum.current = settings.recognition_min_confidence ?? .65;
    setCheckpoint(data); setBusy(false);
    if (data.destination === 'session_ready') await onComplete(operation);
    else if (!data.clarification_paused && !intent.ended() && (data.profile.onboarding_status === 'in_progress' || data.stage === 'review')) void start();
  }
  const initialRestore = useEffectEvent(restore);
  useEffect(() => {
    const operation = voice.begin();
    // Restored state is applied after the authenticated request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void initialRestore(operation).catch(error => { if (operation.current) { setError(error.message); setBusy(false); } }).finally(() => operation.release());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function promptAudio(operation: LessonOperation, data: Checkpoint): Promise<{ data: Checkpoint; played: boolean }> {
    voice.thinking();
    const started = performance.now();
    let response = await operation.wait(() => authenticatedFetch('/api/onboarding/audio', { ...json({ revision: data.revision }), signal: operation.signal }, operation));
    if (response.status === 409) {
      // One retry, same account-bound operation, latest authoritative checkpoint.
      const latest = await operation.api<Checkpoint>('/onboarding'); operation.assertCurrent(); setCheckpoint(latest);
      if (latest.destination === 'session_ready' || latest.stage !== data.stage || latest.welcome !== data.welcome || latest.pending) return { data: latest, played: false };
      data = latest;
      response = await operation.wait(() => authenticatedFetch('/api/onboarding/audio', { ...json({ revision: latest.revision }), signal: operation.signal }, operation));
    }
    if (!response.ok) throw new Error('Audli’s voice is unavailable. Retry the conversation; your saved progress is safe.');
    const body = await operation.wait(() => measuredAudioBody(response)); operation.assertCurrent();
    turnTiming('tts_readiness', started);
    const latest = await operation.api<Checkpoint>('/onboarding'); operation.assertCurrent();
    if (latest.revision !== data.revision) return { data: latest, played: false };
    await voice.speak(operation, URL.createObjectURL(body));
    voice.thinking();
    // Another tab can finish a step while this one is playing. Restore before
    // acknowledging speech or opening a microphone for an obsolete question.
    return { data: await operation.api<Checkpoint>('/onboarding'), played: true };
  }
  async function captureAnswer(operation: LessonOperation, data: Checkpoint): Promise<Checkpoint> {
    const current = await operation.api<Checkpoint>('/onboarding');
    if (current.revision !== data.revision) { retained.current = null; setCheckpoint(current); return current; }
    if (retained.current?.revision !== data.revision) retained.current = null;
    if (!retained.current) {
      try { retained.current = { audio: await voice.listen(operation), revision: data.revision }; }
      catch (error) { if (operation.current && error instanceof InterruptedRecording) retained.current = { audio: error.blob, revision: data.revision }; throw error; }
    }
    operation.assertCurrent(); voice.thinking();
    const afterCapture = await operation.api<Checkpoint>('/onboarding');
    if (afterCapture.revision !== data.revision) { retained.current = null; setCheckpoint(afterCapture); return afterCapture; }
    const form = recordingUpload(retained.current.audio); form.append('revision', String(data.revision));
    const latest = await measureTurn('upload_transcription', () => sendRecording(() => operation.api<Checkpoint>('/onboarding/attempts', { method: 'POST', body: form }), () => { retained.current = null; }));
    operation.assertCurrent(); retained.current = null; setCheckpoint(latest); return latest;
  }
  async function start(userInitiated = false) {
    if (running.current) return;
    const operation = voice.begin(); running.current = true; setActive(true); setError(''); setBusy(false);
    try {
      let data = await operation.api<Checkpoint>('/onboarding');
      if (data.clarification_paused && userInitiated) {
        data = await operation.api<Checkpoint>('/onboarding/retry-clarification', json({ revision: data.revision }));
      }
      if (data.destination !== 'session_ready' && (data.profile.onboarding_status === 'not_started'
        || (data.profile.onboarding_status === 'profile_saved' && data.stage === 'identity'))) {
        data = await operation.api<Checkpoint>('/onboarding/start', json({ revision: data.revision }));
      }
      operation.assertCurrent(); intent.start(); setCheckpoint(data);
      if (userInitiated && data.name_entry_available && data.pending && !reliableRecognition(data.pending, minimum.current)) {
        await voice.retrySpeech(operation); data = await captureAnswer(operation, data);
      }
      let retries = 0;
      while (operation.current) {
        if (data.clarification_paused) throw new Error('Let’s pause here. Check your microphone, then retry to answer the remaining question. Your preferences are saved.');
        if (data.destination === 'session_ready') { await onComplete(operation); return; }
        if (data.pending) {
          // Pending recognition resumes without duplicating completed profile stages.
          retained.current = null;
          if (!reliableRecognition(data.pending, minimum.current)) {
            if (data.name_entry_available) throw new Error('I didn’t quite catch your name. You can type it below, or retry by voice.');
            if (++retries > 2) {
              if (data.stage === 'needs') {
                data = await operation.api<Checkpoint>('/onboarding/goal', json({ question_revision: data.goal_question_revision ?? data.revision, skip: true }));
                operation.assertCurrent(); setCheckpoint(data); retries = 0; continue;
              }
              throw new Error('Your microphone is hard to hear. Check it, then retry this turn.');
            }
            await voice.retrySpeech(operation); data = await captureAnswer(operation, data); continue;
          }
          voice.thinking();
          data = await measureTurn('profile_extraction', () => operation.api<Checkpoint>('/onboarding/answer', json({ revision: data.revision, text: data.pending!.text, confirmed: true, hands_free: true })));
          operation.assertCurrent(); setCheckpoint(data); retries = 0; continue;
        }
        const beforeStage = data.stage;
        const beforeWelcome = data.welcome;
        const beforePrompt = data.prompt;
        const speech = await promptAudio(operation, data); data = speech.data; operation.assertCurrent(); setCheckpoint(data);
        if (!speech.played || data.destination === 'session_ready' || data.pending || data.stage !== beforeStage || data.welcome !== beforeWelcome || data.prompt !== beforePrompt) continue;
        if (data.welcome) {
          data = await operation.api<Checkpoint>('/onboarding/heard', json({ revision: data.revision }));
          operation.assertCurrent(); setCheckpoint(data); continue;
        }
        if (data.stage === 'review') {
          data = await operation.api<Checkpoint>('/onboarding/complete', json({ revision: data.revision }));
          operation.assertCurrent(); setCheckpoint(data); await onComplete(operation); return;
        }
        data = await captureAnswer(operation, data);
      }
    } catch (error) { if (operation.current) { setError((error as Error).message); setBusy(false); } }
    finally { if (operation.current) running.current = false; operation.release(); }
  }
  async function saveName() {
    if (!checkpoint || nameSubmission.current) return;
    nameSubmission.current = true; setSavingName(true);
    // Cancel recording/playback and all stale UI continuations immediately.
    voice.stop(); retained.current = null; running.current = false;
    const operation = voice.begin();
    setError('');
    try {
      const data = await operation.api<Checkpoint>('/onboarding/name', json({
        question_revision: checkpoint.name_question_revision ?? 0, name,
      }));
      operation.assertCurrent(); setCheckpoint(data); setName('');
      nameSubmission.current = false; setSavingName(false);
      void start();
    } catch (error) {
      if (operation.current) setError((error as Error).message);
    } finally {
      if (operation.current) { nameSubmission.current = false; setSavingName(false); }
      operation.release();
    }
  }
  function editGoal(value: string) {
    setGoal(value);
    // Choosing text freezes this question while the learner composes an answer.
    // Any already-committed voice result can still be replaced using its token.
    if (running.current) {
      voice.stop(); retained.current = null; running.current = false;
    }
  }
  async function saveGoal(skip = false) {
    if (!checkpoint || goalSubmission.current) return;
    goalSubmission.current = true; setSavingGoal(true);
    voice.stop(); retained.current = null; running.current = false;
    const operation = voice.begin(); setError('');
    try {
      const data = await operation.api<Checkpoint>('/onboarding/goal', json({
        question_revision: checkpoint.goal_question_revision ?? checkpoint.revision, text: goal, skip,
      }));
      operation.assertCurrent(); setCheckpoint(data); setGoal('');
      goalSubmission.current = false; setSavingGoal(false); void start();
    } catch (error) {
      if (operation.current) {
        setError((error as Error).message);
        // A response can be lost after commit. Restore the authoritative stage.
        try { const data = await operation.api<Checkpoint>('/onboarding'); operation.assertCurrent(); setCheckpoint(data); }
        catch { /* The saved question remains available for retry. */ }
      }
    } finally {
      if (operation.current) { goalSubmission.current = false; setSavingGoal(false); }
      operation.release();
    }
  }
  const goalEntry = checkpoint?.goal_entry_available && <form className="onboarding-name" onSubmit={event => { event.preventDefault(); void saveGoal(); }}>
    <label htmlFor="listening-goal">Your goal (optional)</label>
    <p id="goal-help">Tell Audli by voice, type a goal, or skip for now. We’ll practice understanding spoken English.</p>
    <div><input id="listening-goal" name="listening-goal" aria-describedby="goal-help" maxLength={500} value={goal} onChange={event => editGoal(event.target.value)} disabled={savingGoal} />
    <button className="primary" type="submit" disabled={savingGoal || !goal.trim()}>{savingGoal ? 'Saving…' : 'Save goal and continue'}</button>
    <button className="secondary" type="button" disabled={savingGoal} onClick={() => void saveGoal(true)}>Skip for now</button></div>
  </form>;
  const nameEntry = checkpoint?.name_entry_available && <form className="onboarding-name" onSubmit={event => { event.preventDefault(); void saveName(); }}>
    <label htmlFor="preferred-name">What should I call you?</label>
    <p id="name-help">You can say your name or type it here.</p>
    <div><input id="preferred-name" name="preferred-name" autoComplete="given-name" aria-describedby="name-help" maxLength={80} value={name} onChange={event => setName(event.target.value)} disabled={savingName} />
    <button className="primary" type="submit" disabled={savingName || !name.trim()}>{savingName ? 'Saving…' : 'Save and continue'}</button></div>
  </form>;
  function end() { goalSubmission.current = false; setSavingGoal(false); nameSubmission.current = false; setSavingName(false); voice.stop(); intent.end(); retained.current = null; running.current = false; setActive(false); setError(''); }
  if (active) return <><SessionShell state={voice.state} activity={voice.activity} onEnd={end} onReplay={voice.state === 'Speaking' ? voice.replay : undefined}>
    <p className="sr-only">{checkpoint?.prompt}</p>{nameEntry}{goalEntry}{error && <div role="alert" className="session-error">{error}<p>{checkpoint?.prompt}</p><button className="secondary" onClick={() => void start(true)}>Retry conversation</button>{retained.current && <button className="secondary" onClick={() => { retained.current = null; void start(true); }}>Record again</button>}</div>}
  </SessionShell><audio ref={voice.audio} className="session-audio" aria-label="Audli onboarding audio"/></>;
  return <main className="onboarding-ready"><header><strong className="wordmark">audli</strong></header><AudliMascot/><h1>Let’s make this yours.</h1><p>{checkpoint?.clarification_paused ? 'Your preferences are saved. Check your microphone, then resume to answer the remaining question.' : 'A short conversation about what you want to understand.'}</p><button className="primary" disabled={busy} onClick={() => void start(true)}>{checkpoint?.profile.onboarding_status === 'in_progress' ? 'Resume our conversation' : 'Let’s talk'}</button>{error && <div role="alert">{error}<button className="secondary" onClick={() => void start(true)}>Retry conversation</button>{retained.current && <button className="secondary" onClick={() => { retained.current = null; void start(true); }}>Record again</button>}</div>}<audio ref={voice.audio} className="session-audio" aria-label="Audli onboarding audio"/>{nameEntry}{goalEntry}<footer>Eyes optional. Ears essential.</footer></main>;
}
