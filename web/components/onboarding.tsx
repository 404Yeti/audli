'use client';
import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react';
import { authenticatedFetch, LessonLifetime, type LessonOperation } from '../lib/api';
import { captureRecording, recordingUpload } from '../lib/recording';
import { VoiceSession, LoadingState, RecoveryState, PrimaryButton } from './lesson';

type Profile = { name: string; goal: string; target_language: string; interests: string[]; target_situations: string[]; onboarding_status: string };
type State = { profile: Profile; stage: string; revision: number; prompt: string; destination: string; pending: { text: string; uncertainty: string[] } | null };
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function SpokenOnboarding({ onComplete }: { onComplete: () => Promise<void> }) {
  const [state, setState] = useState<State | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState('Loading your conversation…');
  const [error, setError] = useState('');
  const [recording, setRecording] = useState(false);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [voice, setVoice] = useState<{ stage: string; url: string } | null>(null);
  const [voiceError, setVoiceError] = useState('');
  const [voiceRetry, setVoiceRetry] = useState(0);
  const owner = useRef<LessonLifetime | null>(null);
  const capture = useRef<ReturnType<typeof captureRecording> | null>(null);
  const recordingOperation = useRef<LessonOperation | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const attachAudio = useCallback((node: HTMLAudioElement | null) => {
    if (audio.current !== node) audio.current?.pause();
    audio.current = node;
  }, []);
  function apply(data: State) { setState(data); setText(data.pending?.text ?? ''); setBlob(null); }
  async function run(message: string, action: (operation: LessonOperation) => Promise<void>) {
    if (!owner.current?.current) return;
    const operation = owner.current.begin(); setBusy(message); setError('');
    try { await action(operation); }
    catch (e) { if (operation.current) setError(e instanceof Error ? e.message : 'Please retry.'); }
    finally { if (operation.current) setBusy(''); operation.release(); }
  }
  async function load(operation: LessonOperation) {
    const data = await operation.api<State>('/onboarding'); operation.assertCurrent(); apply(data);
    if (data.destination === 'session_ready') await operation.wait(onComplete);
  }
  const initialLoad = useEffectEvent(load);
  useEffect(() => {
    const lifetime = new LessonLifetime(); owner.current = lifetime;
    const operation = lifetime.begin();
    // State is restored only after the server response resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void initialLoad(operation).catch(e => { if (operation.current) setError(e.message); }).finally(() => { if (operation.current) setBusy(''); operation.release(); });
    return () => { lifetime.close(); capture.current?.dispose(); if (timer.current) clearTimeout(timer.current); };
  }, []);
  const requestVoice = useEffectEvent(async (operation: LessonOperation) => {
    if (!state) throw new Error('Prompt unavailable');
    let response = await operation.wait(() => authenticatedFetch('/api/onboarding/audio',
      { ...json({ revision: state.revision }), signal: operation.signal }, operation));
    if (response.status === 409) {
      // One recovery only, using a fresh server checkpoint rather than a guessed revision.
      const checkpoint = await operation.api<State>('/onboarding');
      operation.assertCurrent();
      if (checkpoint.stage !== state.stage || checkpoint.destination === 'session_ready') {
        apply(checkpoint);
        if (checkpoint.destination === 'session_ready') await operation.wait(onComplete);
        operation.cancel(); operation.assertCurrent();
      }
      response = await operation.wait(() => authenticatedFetch('/api/onboarding/audio',
        { ...json({ revision: checkpoint.revision }), signal: operation.signal }, operation));
    }
    if (!response.ok) throw new Error('Voice unavailable');
    return operation.wait(() => response.blob());
  });
  const stage = state?.stage;
  const destination = state?.destination;
  useEffect(() => {
    if (!stage || destination === 'session_ready') return;
    const operation = owner.current!.begin(); let url: string | undefined;
    void requestVoice(operation)
      .then(body => { operation.assertCurrent(); url = URL.createObjectURL(body); setVoice({ stage, url }); setVoiceError(''); })
      .catch(() => { if (operation.current) { setVoice(null); setVoiceError('Audli’s voice is unavailable. Read the prompt and continue, or retry audio.'); } });
    return () => { operation.cancel(); if (url) URL.revokeObjectURL(url); };
  }, [stage, destination, voiceRetry]);
  async function upload(captured: Blob) {
    if (!state) return;
    await run('Audli is listening…', async operation => {
      const form = recordingUpload(captured); form.append('revision', String(state.revision));
      const data = await operation.api<State>('/onboarding/attempts', { method: 'POST', body: form });
      operation.assertCurrent(); apply(data);
    });
  }
  function cancel() {
    audio.current?.pause();
    recordingOperation.current?.cancel(); capture.current?.dispose(); capture.current = null;
    if (timer.current) clearTimeout(timer.current);
    setRecording(false); setBusy('');
  }
  async function record() {
    if (!owner.current?.current || busy || recording) return;
    const operation = owner.current.begin(); recordingOperation.current = operation;
    setError(''); setBusy('Opening your microphone…'); audio.current?.pause();
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('Recording needs HTTPS or localhost and a supported browser.');
      const stream = await operation.wait(() => navigator.mediaDevices.getUserMedia({ audio: true }), input => input.getTracks().forEach(track => track.stop()));
      operation.assertCurrent(() => stream.getTracks().forEach(track => track.stop()));
      const session = captureRecording(stream, captured => {
        if (!operation.current) return;
        if (timer.current) clearTimeout(timer.current);
        setRecording(false); setBlob(captured); operation.release(); void upload(captured);
      }, message => { if (operation.current) { setRecording(false); setError(message); } operation.release(); });
      capture.current = session; setRecording(true); setBusy(''); setBlob(null);
      timer.current = setTimeout(() => { if (operation.current) session.stop(); }, 119000);
    } catch (e) {
      if (operation.current) { setError(e instanceof Error && e.name !== 'NotAllowedError' ? e.message : 'Microphone access was denied. Allow access and retry, or upload a spoken recording.'); setBusy(''); }
      operation.release();
    }
  }
  async function confirm() {
    if (!state) return;
    await run('Saving your listening preferences…', async operation => {
      const data = await operation.api<State>('/onboarding/answer', json({ revision: state.revision, text, confirmed: true }));
      operation.assertCurrent(); apply(data);
    });
  }
  async function complete() {
    if (!state) return;
    await run('Let’s train your ears…', async operation => {
      await operation.api<State>('/onboarding/complete', json({ revision: state.revision }));
      operation.assertCurrent(); await operation.wait(onComplete);
    });
  }
  return <main><header><strong className="wordmark">audli</strong><span className="language">TRAIN YOUR EARS</span></header>
    <section className="card"><VoiceSession state={recording ? 'Recording' : busy ? 'Thinking' : 'Idle'}/>
      <h1>Let’s make this yours.</h1><p>A short conversation about what you want to understand. Speaking fluency isn’t a listening test.</p>
      {busy && <LoadingState>{busy}</LoadingState>}
      {error && <RecoveryState>{error}<button className="secondary" disabled={!!busy || recording} onClick={() => run('Restoring saved progress…', load)}>Reload saved progress</button></RecoveryState>}
      {state && <><p>{state.prompt}</p>{voice?.stage === state.stage && <audio key={voice.url} ref={attachAudio} controls src={voice.url} onCanPlay={() => { if (!owner.current?.current || recording) return; const operation = owner.current.begin(); const element = audio.current; if (element) void operation.wait(() => element.play(), () => element.pause()).catch(() => { if (operation.current) setVoiceError('Tap play to hear Audli.'); }).finally(() => operation.release()); }} aria-label="Hear onboarding prompt"/>}
        {voiceError && <p role="status">{voiceError}<button className="secondary" disabled={!!busy} onClick={() => setVoiceRetry(v => v + 1)}>Retry audio</button></p>}
        {(state.profile.onboarding_status === 'not_started' || (state.profile.onboarding_status === 'profile_saved' && state.stage === 'identity')) ? <PrimaryButton disabled={!!busy} onClick={() => run('Starting our conversation…', async operation => { const data = await operation.api<State>('/onboarding/start', json({ revision: state.revision })); operation.assertCurrent(); apply(data); })}>Let’s talk</PrimaryButton>
        : state.stage === 'review' ? <><p><strong>{state.profile.name}</strong> · English</p><p>{state.profile.goal}</p><p>Listening situations: {state.profile.target_situations.join(', ')}</p><p>Interests: {state.profile.interests.join(', ')}</p><p>We’ll start with clear, slower English. Your listening profile is provisional until we hear evidence from a listening exercise.</p><PrimaryButton disabled={!!busy} onClick={complete}>That’s right. Let’s train your ears.</PrimaryButton><button className="secondary" disabled={!!busy} onClick={() => run('Reopening preferences…', async operation => { const data = await operation.api<State>('/onboarding/revise', json({ revision: state.revision })); operation.assertCurrent(); apply(data); })}>Correct my preferences</button></>
        : <>{recording ? <><PrimaryButton onClick={() => capture.current?.stop()}>Finish answer</PrimaryButton><button className="secondary" onClick={cancel}>Cancel recording</button></> : state.pending ? <><h2>Did I hear you correctly?</h2>{state.pending.uncertainty.map((message, i) => <p key={i}>{message}</p>)}<label htmlFor="onboarding-answer">Your spoken answer</label><textarea id="onboarding-answer" value={text} maxLength={8000} onChange={e => setText(e.target.value)}/><PrimaryButton disabled={!!busy || !text.trim()} onClick={confirm}>That’s what I said</PrimaryButton><button className="secondary" disabled={!!busy} onClick={record}>Record again</button></> : <PrimaryButton disabled={!!busy} onClick={record}>Record answer</PrimaryButton>}
        {blob && !recording && <button className="secondary" disabled={!!busy} onClick={() => upload(blob)}>Retry transcription</button>}
        {!recording && <details><summary>Use a spoken recording</summary><input aria-label="Upload onboarding answer" type="file" accept="audio/webm,audio/mp4,audio/mpeg,audio/wav,audio/ogg" disabled={!!busy} onChange={e => { const file = e.target.files?.[0]; if (file) { setBlob(file); void upload(file); } }}/></details>}</>}
      </>}
      {!state && !busy && <PrimaryButton onClick={() => run('Connecting…', load)}>Retry connection</PrimaryButton>}
    </section><footer>Eyes optional. Ears essential.<span>Your microphone recording is discarded after transcription.</span></footer></main>;
}
