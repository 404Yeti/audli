'use client';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { authenticatedFetch, type LessonOperation } from '../lib/api';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { reliableRecognition, type Recognition } from '../lib/hands-free';
import { recordingUpload } from '../lib/recording';
import { SessionShell } from './product-shell';
import { useAccount } from './auth-gate';
import { conversationIntent } from '../lib/conversation-intent';
import { measureTurn, turnTiming, measuredAudioBody } from '../lib/turn-timing';
import { AudliMascot } from './audli-mascot';

type Profile = { name: string; goal: string; target_language: string; interests: string[]; target_situations: string[]; onboarding_status: string };
type Checkpoint = { profile: Profile; stage: string; revision: number; prompt: string; destination: string; pending: Recognition | null };
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function SpokenOnboarding({ onComplete }: { onComplete: (operation: LessonOperation) => Promise<void> }) {
  const voice = useVoiceLifecycle();
  const intent = conversationIntent(useAccount().scope, 'onboarding');
  const [checkpoint, setCheckpoint] = useState<Checkpoint | null>(null);
  const [active, setActive] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const running = useRef(false);
  const minimum = useRef(.65);
  const retained = useRef<Blob | null>(null);
  async function restore(operation: LessonOperation) {
    const data = await operation.api<Checkpoint>('/onboarding');
    const settings = await operation.api<{ recognition_min_confidence?: number }>('/profile');
    operation.assertCurrent(); minimum.current = settings.recognition_min_confidence ?? .65;
    setCheckpoint(data); setBusy(false);
    if (data.destination === 'session_ready') await onComplete(operation);
    else if (!intent.ended() && (data.profile.onboarding_status === 'in_progress' || data.stage === 'review')) void start();
  }
  const initialRestore = useEffectEvent(restore);
  useEffect(() => {
    const operation = voice.begin();
    // Restored state is applied after the authenticated request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void initialRestore(operation).catch(error => { if (operation.current) { setError(error.message); setBusy(false); } }).finally(() => operation.release());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function promptAudio(operation: LessonOperation, data: Checkpoint): Promise<Checkpoint> {
    voice.thinking();
    const started = performance.now();
    let response = await operation.wait(() => authenticatedFetch('/api/onboarding/audio', { ...json({ revision: data.revision }), signal: operation.signal }, operation));
    if (response.status === 409) {
      // One retry, same account-bound operation, latest authoritative checkpoint.
      const latest = await operation.api<Checkpoint>('/onboarding'); operation.assertCurrent(); setCheckpoint(latest);
      if (latest.destination === 'session_ready' || latest.stage !== data.stage || latest.pending) return latest;
      data = latest;
      response = await operation.wait(() => authenticatedFetch('/api/onboarding/audio', { ...json({ revision: latest.revision }), signal: operation.signal }, operation));
    }
    if (!response.ok) throw new Error('Audli’s voice is unavailable. Retry the conversation; your saved progress is safe.');
    const body = await operation.wait(() => measuredAudioBody(response)); operation.assertCurrent();
    turnTiming('tts_readiness', started);
    await voice.speak(operation, URL.createObjectURL(body)); return data;
  }
  async function captureAnswer(operation: LessonOperation, data: Checkpoint): Promise<Checkpoint> {
    if (!retained.current) retained.current = await voice.listen(operation);
    operation.assertCurrent(); voice.thinking();
    const form = recordingUpload(retained.current); form.append('revision', String(data.revision));
    const latest = await measureTurn('upload_transcription', () => operation.api<Checkpoint>('/onboarding/attempts', { method: 'POST', body: form }));
    operation.assertCurrent(); retained.current = null; setCheckpoint(latest); return latest;
  }
  async function start() {
    if (running.current) return;
    const operation = voice.begin(); running.current = true; setActive(true); setError(''); setBusy(false);
    try {
      let data = await operation.api<Checkpoint>('/onboarding');
      if (data.destination !== 'session_ready' && (data.profile.onboarding_status === 'not_started'
        || (data.profile.onboarding_status === 'profile_saved' && data.stage === 'identity'))) {
        data = await operation.api<Checkpoint>('/onboarding/start', json({ revision: data.revision }));
      }
      operation.assertCurrent(); intent.start(); setCheckpoint(data);
      let retries = 0;
      while (operation.current) {
        if (data.destination === 'session_ready') { await onComplete(operation); return; }
        if (data.pending) {
          // Pending recognition resumes without duplicating completed profile stages.
          retained.current = null;
          if (!reliableRecognition(data.pending, minimum.current)) {
            if (++retries > 2) throw new Error('Your microphone is hard to hear. Check it, then retry this turn.');
            await voice.retrySpeech(operation); data = await captureAnswer(operation, data); continue;
          }
          voice.thinking();
          data = await measureTurn('profile_extraction', () => operation.api<Checkpoint>('/onboarding/answer', json({ revision: data.revision, text: data.pending!.text, confirmed: true, hands_free: true })));
          operation.assertCurrent(); setCheckpoint(data); retries = 0; continue;
        }
        const beforeStage = data.stage;
        data = await promptAudio(operation, data); operation.assertCurrent(); setCheckpoint(data);
        if (data.destination === 'session_ready' || data.pending || data.stage !== beforeStage) continue;
        if (data.stage === 'review') {
          data = await operation.api<Checkpoint>('/onboarding/complete', json({ revision: data.revision }));
          operation.assertCurrent(); setCheckpoint(data); await onComplete(operation); return;
        }
        data = await captureAnswer(operation, data);
      }
    } catch (error) { if (operation.current) { setError((error as Error).message); setBusy(false); } }
    finally { if (operation.current) running.current = false; operation.release(); }
  }
  function end() { voice.stop(); intent.end(); retained.current = null; running.current = false; setActive(false); setError(''); }
  if (active) return <><SessionShell state={voice.state} activity={voice.activity} onEnd={end} onReplay={voice.state === 'Speaking' ? voice.replay : undefined}>
    <p className="sr-only">{checkpoint?.prompt}</p>{error && <div role="alert" className="session-error">{error}<p>{checkpoint?.prompt}</p><button className="secondary" onClick={() => void start()}>Retry conversation</button></div>}
  </SessionShell><audio ref={voice.audio} className="session-audio" aria-label="Audli onboarding audio"/></>;
  return <main className="onboarding-ready"><header><strong className="wordmark">audli</strong></header><AudliMascot/><h1>Let’s make this yours.</h1><p>A short conversation about what you want to understand.</p><button className="primary" disabled={busy} onClick={() => void start()}>{checkpoint?.profile.onboarding_status === 'in_progress' ? 'Resume our conversation' : 'Let’s talk'}</button>{error && <div role="alert">{error}<button className="secondary" onClick={() => void start()}>Retry conversation</button></div>}<audio ref={voice.audio} className="session-audio" aria-label="Audli onboarding audio"/><footer>Eyes optional. Ears essential.</footer></main>;
}
