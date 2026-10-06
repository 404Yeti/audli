'use client';

import { useEffect, useEffectEvent, useReducer, useRef, useState } from 'react';
import { AudliLogo, VoiceSession, MicrophoneControl, PrimaryButton, LessonCard, FeedbackCard, LoadingState, RecoveryState, HomeNavigation } from '../components/lesson';
import { shouldAutoRecord, feedbackOutcome, microphonePermission } from '../lib/session';
import { api } from '../lib/api';
import { captureRecording, recordingDiagnostic, recordingPreview, previewSource, recordingUpload, type RecordingPreview } from '../lib/recording';
import { conversationReducer, canRespond, scoreLabel, type ConversationPhase } from '../lib/conversation';

type Difficulty = { speech_rate: number; duration_seconds: number; vocabulary_level: string; information_density: number };
type Profile = { name: string; goal: string; completed_attempts: number; difficulty: Difficulty };
type Exercise = { id: string; audio_url: string; difficulty: Difficulty; completed_attempt_id: string | null };
type Attempt = { id: string; transcription: { text: string; confidence: number | null; uncertainty: string[]; source: string }; confirmed_text?: string | null };
type Evaluation = { overall: number | null; main_idea: number | null; details: number | null; vocabulary: number | null; inference: number | null; coverage?: number; feedback: string; understood: string[]; insufficient_evidence?: string[]; missed?: string[]; misunderstood: string[]; dimensions?: Record<string, string> };
type Adaptation = { decision: string; changed_variable: string | null; old_value: string | number | null; new_value: string | number | null; reason: string; focus: string };
type Result = { evaluation: Evaluation; adaptation: Adaptation };
type Conversation = { state: ConversationPhase; cue_id: string | null; prompt: string | null; active_followup: { id: string; question: string } | null; followups_asked: number; pending_attempt: Attempt | null; result: Result | null };
const labels: Record<string, string> = { main_idea: 'Main idea', details: 'Details', vocabulary: 'Vocabulary understanding', inference: 'Inference' };
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export default function Audli() {
  const [screen, setScreen] = useState<'home' | 'introduction' | 'lesson'>('home');
  const introductionSpeech = useRef<SpeechSynthesisUtterance | null>(null);
  const [introductionError, setIntroductionError] = useState('');
  const usingAudioFile = useRef(false);
  const microphoneGranted = useRef(false);
  const requestingMic = useRef(false);
  const autoRecordedCue = useRef<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [mode, setMode] = useState('');
  const [exercise, setExercise] = useState<Exercise | null>(null);
  const exerciseRef = useRef<Exercise | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [flow, dispatch] = useReducer(conversationReducer, { phase: 'LISTENING', awaiting: 'AWAITING_SUMMARY' });
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [busy, setBusy] = useState('Loading your listening space…');
  const [error, setError] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [preview, setPreview] = useState<RecordingPreview | null>(null);
  const recordingUrl = previewSource(preview, blob);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [text, setText] = useState('');
  const [transcript, setTranscript] = useState<{ title: string; script: string } | null>(null);
  const [coach, setCoach] = useState<{ key: string; url: string } | null>(null);
  const [coachError, setCoachError] = useState('');
  const [coachSpeaking, setCoachSpeaking] = useState(false);
  const [coachRetry, setCoachRetry] = useState(0);
  const capture = useRef<ReturnType<typeof captureRecording> | null>(null);
  const mounted = useRef(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoUploaded = useRef<Blob | null>(null);
  const listeningAudio = useRef<HTMLAudioElement | null>(null);
  const recordingAudio = useRef<HTMLAudioElement | null>(null);
  const coachAudio = useRef<HTMLAudioElement | null>(null);
  const flowRef = useRef(flow.phase); flowRef.current = flow.phase;
  const completedPlayback = useRef(false);
  const recording = flow.phase === 'RECORDING';
  const result = conversation?.result;
  const cueKey = exercise && conversation?.cue_id ? `${exercise.id}:${conversation.cue_id}` : null;
  const coachUrl = coach?.key === cueKey ? coach?.url : undefined;

  function applyConversation(data: Conversation) {
    setConversation(data);
    dispatch({ type: 'server', phase: data.state, hasPending: !!data.pending_attempt });
    setAttempt(data.pending_attempt);
    setText(data.pending_attempt?.confirmed_text ?? data.pending_attempt?.transcription.text ?? '');
  }
  async function load() {
    const [data, current] = await Promise.all([api<{ profile: Profile; provider: string }>('/profile'), api<Exercise | null>('/exercises/current')]);
    if (!mounted.current) return;
    setProfile(data.profile); setMode(data.provider); setName(data.profile.name); setGoal(data.profile.goal);
    exerciseRef.current = current; setExercise(current);
    if (current) applyConversation(await api<Conversation>(`/exercises/${current.id}/conversation`));
  }
  const loadFromEffect = useEffectEvent(load);
  const transcribeFromEffect = useEffectEvent(transcribe);
  useEffect(() => {
    mounted.current = true;
    // Initial state is restored from the external API, after its promises resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadFromEffect().catch(e => setError(e.message)).finally(() => setBusy(''));
    return () => { mounted.current = false; if (timer.current) clearInterval(timer.current); capture.current?.dispose(); capture.current = null; window.speechSynthesis?.cancel(); };
  }, []);
  useEffect(() => {
    // Preview state follows the lifetime of this external object URL.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!blob) { setPreview(null); return; }
    try {
      const owned = recordingPreview(blob);
      setPreview(owned);
      return () => owned.dispose();
    } catch { setPreview(null); setError('Preview could not be created. You can still try transcription.'); }
  }, [blob]);
  useEffect(() => {
    // Reset playback UI when ownership moves to a different server cue.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCoach(null); setCoachError(''); setCoachSpeaking(false);
    if (screen !== 'lesson' || !exercise || !conversation?.cue_id || !cueKey) return;
    let cancelled = false;
    api<{ audio_url: string }>(`/exercises/${exercise.id}/coach-audio`, json('POST', { cue_id: conversation.cue_id }))
      .then(data => { if (!cancelled && data.audio_url?.trim()) setCoach({ key: cueKey, url: data.audio_url }); })
      .catch(() => { if (!cancelled) setCoachError('Audli’s voice is unavailable. You can read the message and continue, or retry audio.'); });
    return () => { cancelled = true; };
  }, [cueKey, coachRetry, screen, exercise, conversation?.cue_id]);
  useEffect(() => {
    if (blob && flow.phase === 'REVIEWING_RECORDING' && autoUploaded.current !== blob) {
      autoUploaded.current = blob;
      void transcribeFromEffect();
    }
  }, [blob, flow.phase]);

  async function run(message: string, action: () => Promise<void>) {
    setError(''); setBusy(message);
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : 'Something went wrong. Please try again.'); }
    finally { setBusy(''); }
  }
  async function next() {
    await run('Preparing your listening clip…', async () => {
      if (!microphoneGranted.current && !usingAudioFile.current) { await microphonePermission(navigator.mediaDevices); microphoneGranted.current = true; }
      if (!exercise) await api('/profile', json('PUT', { name, goal }));
      const nextExercise = await api<Exercise>('/exercises', { method: 'POST' });
      setScreen('introduction'); speakIntroduction(); autoRecordedCue.current = null; exerciseRef.current = nextExercise; setExercise(nextExercise); setConversation(null); dispatch({ type: 'reset' });
      setTranscript(null); setAttempt(null); setBlob(null); setText(''); completedPlayback.current = false;
      applyConversation(await api<Conversation>(`/exercises/${nextExercise.id}/conversation`));
      const data = await api<{ profile: Profile; provider: string }>('/profile'); setProfile(data.profile);
    });
  }
  async function listened() {
    if (!exercise || recording || !!busy || flow.phase !== 'LISTENING') return;
    await run('Audli is getting ready…', async () => {
      applyConversation(await api<Conversation>(`/exercises/${exercise.id}/conversation/listened`, { method: 'POST' }));
    });
  }

  function speakIntroduction() {
    setIntroductionError('');
    if (!window.speechSynthesis || typeof SpeechSynthesisUtterance === 'undefined') { setIntroductionError('Voice playback is unavailable. Read the introduction and continue.'); return; }
    window.speechSynthesis.cancel();
    const speech = new SpeechSynthesisUtterance('Let’s train your ears. Listen to one clear English speaker. Then tell Audli what you understood in your own words.');
    speech.lang = 'en-US';
    introductionSpeech.current = speech;
    speech.onstart = () => setCoachSpeaking(true);
    speech.onend = () => setCoachSpeaking(false);
    speech.onerror = () => { setCoachSpeaking(false); setIntroductionError('Voice playback failed. Read the introduction, or replay it.'); };
    window.speechSynthesis.speak(speech);
  }

  async function begin() {
    await run('Checking your microphone…', async () => {
      await microphonePermission(navigator.mediaDevices); microphoneGranted.current = true; usingAudioFile.current = false;
      if (exercise) { setScreen(flow.phase === 'LISTENING' ? 'introduction' : 'lesson'); if (flow.phase === 'LISTENING') speakIntroduction(); }
    });

  }
  function cancelRecording() {
    capture.current?.dispose(); capture.current = null;
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
    dispatch({ type: 'phase', phase: flow.awaiting }); setElapsed(0);
  }
  function cueEnded() {
    setCoachSpeaking(false);
    if (result) { feedbackEnded(); return; }
    if (shouldAutoRecord(flowRef.current, cueKey, autoRecordedCue.current, !!result)) {
      autoRecordedCue.current = cueKey;
      void startRecording();
    }
  }
  function stop() { capture.current?.stop(); }
  async function startRecording() {
    if (requestingMic.current || busy || !canRespond(flowRef.current) || flowRef.current === 'RECORDING') return;
    requestingMic.current = true;
    await run('Opening your microphone…', async () => {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('Recording needs localhost or HTTPS and a supported browser. You can upload a recording below.');
      window.speechSynthesis?.cancel(); listeningAudio.current?.pause(); recordingAudio.current?.pause(); coachAudio.current?.pause();
      let input: MediaStream;
      try { input = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch { throw new Error('Microphone access was denied or unavailable. Allow access in your browser and retry, or upload a spoken recording.'); }
      microphoneGranted.current = true;
      if (!mounted.current) { input.getTracks().forEach(track => track.stop()); return; }
      capture.current?.dispose();
      const finish = () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };
      const session = captureRecording(input, captured => {
        if (!mounted.current || capture.current !== session) return;
        finish(); setBlob(captured); setAttempt(null); setText(''); dispatch({ type: 'phase', phase: 'REVIEWING_RECORDING' });
      }, message => {
        if (!mounted.current || capture.current !== session) return;
        finish(); dispatch({ type: 'phase', phase: flow.awaiting }); setError(message);
      });
      capture.current = session;
      setBlob(null); setAttempt(null); setText(''); setElapsed(0); dispatch({ type: 'phase', phase: 'RECORDING' });
      const started = Date.now();
      timer.current = setInterval(() => {
        const seconds = Math.floor((Date.now()-started)/1000); setElapsed(seconds);
        if (seconds >= 119) session.stop();
      }, 250);
    });
    requestingMic.current = false;
  }
  async function transcribe() {
    if (!blob || !exercise || recording) return;
    const captured = blob; const exerciseId = exercise.id;
    dispatch({ type: 'phase', phase: 'TRANSCRIBING' });
    await run('Audli is listening to your response…', async () => {
      try {
        const form = recordingUpload(captured);
        recordingDiagnostic('transcription_upload_started', { uploadSize: captured.size, mimeType: captured.type });
        const data = await api<Attempt>(`/exercises/${exerciseId}/attempts`, { method: 'POST', body: form },
          status => recordingDiagnostic('transcription_response', { status }));
        if (!data || typeof data.id !== 'string' || typeof data.transcription?.text !== 'string' || !Array.isArray(data.transcription.uncertainty)) throw new Error('Invalid transcription response. Please retry.');
        if (!data.transcription.text.trim()) setError('We didn’t hear anything. Check your microphone and record again.');
        setAttempt(data); setText(data.transcription.text); dispatch({ type: 'phase', phase: 'REVIEWING_TRANSCRIPT' });
        setBlob(current => current === captured ? null : current);
        recordingDiagnostic('transcription_completed');
      } catch (error) {
        dispatch({ type: 'phase', phase: 'REVIEWING_RECORDING' });
        recordingDiagnostic('transcription_failed', { recordingRetained: true }); throw error;
      }
    });
  }
  async function assess() {
    if (!attempt || !exercise) return;
    dispatch({ type: 'phase', phase: flow.awaiting === 'AWAITING_FOLLOWUP' ? 'ASSESSING_FOLLOWUP' : 'ASSESSING' });
    await run('Audli is thinking about what you heard…', async () => {
      let assessmentSaved = false;
      try {
        recordingDiagnostic('evaluation_started');
        const data = await api<Conversation>(`/attempts/${attempt.id}/assess`, json('POST', {
          text, confirmed: true, followup_id: conversation?.active_followup?.id ?? null }),
          status => recordingDiagnostic('evaluation_response', { status }));
        applyConversation(data); assessmentSaved = true;
        const updated = await api<{ profile: Profile; provider: string }>('/profile'); setProfile(updated.profile);
      } catch (error) { if (!assessmentSaved) dispatch({ type: 'phase', phase: 'REVIEWING_TRANSCRIPT' }); throw error; }
    });
  }
  function feedbackEnded() {
    setCoachSpeaking(false);
    if (!result || !exercise) return;
    const id = exercise.id;
    dispatch({ type: 'phase', phase: 'READY_FOR_NEXT' });
    api<Conversation>(`/exercises/${id}/conversation/ready`, { method: 'POST' }).then(data => {
      if (mounted.current && exerciseRef.current?.id === id) applyConversation(data);
    }).catch(() => { /* Assessment is already durable; continue remains available. */ });
  }

  return <main>
    <header>{screen !== 'home' && <button className="back" disabled={recording || !!busy} onClick={() => { window.speechSynthesis?.cancel(); coachAudio.current?.pause(); listeningAudio.current?.pause(); setScreen('home'); }}>←<span className="sr-only">Back to Home</span></button>}<strong className="wordmark">audli</strong><span className="language">{screen === 'home' ? 'TODAY' : 'LISTENING PRACTICE'}</span></header>
    {screen === 'home' && <><div className="greeting"><div><h1>Hello{name ? ', ' + name : ''}</h1><p>Ready for a little focused listening?</p></div><AudliLogo small/></div>
      <LessonCard duration={exercise?.difficulty.duration_seconds ?? profile?.difficulty.duration_seconds}><PrimaryButton disabled={!!busy || !profile} onClick={exercise ? begin : () => document.getElementById('name')?.focus()}>{exercise ? 'Start training →' : 'Set up your practice →'}</PrimaryButton></LessonCard>
      <aside className="why"><strong>Why this lesson?</strong><p>{profile?.goal ? 'Practice for ' + profile.goal : 'Clear English that adapts to what you understand.'}</p></aside>
      {error.includes('Microphone') && <button className="secondary" disabled={!!busy || !name.trim() || !goal.trim()} onClick={() => { usingAudioFile.current = true; if (exercise) setScreen(flow.phase === 'LISTENING' ? 'introduction' : 'lesson'); else void next(); }}>Continue with an audio file</button>}
    </>}
    {mode === 'demo' && <p className="notice">Demo mode uses synthetic comprehension evidence and manual transcription. Use OpenAI mode for learning assessment.</p>}
    {error && <RecoveryState>{error}{!profile && <button onClick={() => run('Connecting…', load)}>Retry connection</button>}</RecoveryState>}
    {busy && <LoadingState>{busy}</LoadingState>}
    {profile && !exercise && <section className="card"><p className="eyebrow">LET’S FIND YOUR STARTING POINT</p><h2>Make yourself at home.</h2><form onSubmit={e => { e.preventDefault(); next(); }}>
      <label htmlFor="name">What should we call you?</label><input id="name" maxLength={80} required value={name} onChange={e => setName(e.target.value)} placeholder="Your name"/>
      <label htmlFor="goal">What do you want to improve your listening for?</label><textarea id="goal" maxLength={500} required value={goal} onChange={e => setGoal(e.target.value)} placeholder="For example, software engineering meetings"/>
      <p className="hint">We’ll start with clear, slower English.</p><button className="primary" disabled={!!busy || !name.trim() || !goal.trim()}>Let’s listen <span aria-hidden="true">→</span></button>
    </form></section>}
    {exercise && screen !== 'home' && <section className="card">
      <VoiceSession state={recording ? 'Recording' : busy ? 'Thinking' : coachSpeaking ? 'Speaking' : result ? 'Success' : attempt ? 'Review' : screen === 'introduction' ? 'Idle' : 'Listening'}/>
      {screen === 'introduction' && <div className="introduction"><h2>Let’s train your ears.</h2><p>Listen to one clear English speaker. Then tell Audli what you understood in your own words.</p><button className="secondary" onClick={speakIntroduction}>Hear Audli’s introduction</button>{introductionError && <p role="status" className="hint">{introductionError}</p>}<PrimaryButton onClick={() => { window.speechSynthesis?.cancel(); setCoachSpeaking(false); setScreen('lesson'); listeningAudio.current?.play().catch(() => setError('Playback could not start. Tap play to listen.')); }}>Listen to the lesson →</PrimaryButton></div>}
      <div hidden={screen === 'introduction'}>
      <p className="eyebrow" role="status">{coachSpeaking ? 'AUDLI IS SPEAKING' : recording ? 'AUDLI IS LISTENING' : result ? 'LET’S KEEP LISTENING' : conversation?.active_followup ? 'ONE QUICK QUESTION' : 'YOUR LISTENING PRACTICE'}</p>
      {!result && <><h2>{flow.phase === 'LISTENING' ? 'Listen carefully.' : 'Your turn.'}</h2><p>You can replay the clip. The transcript stays hidden while we listen together.</p>
        <div className="audio-panel"><strong>Your English listening clip</strong>{exercise.audio_url?.trim() && <audio ref={listeningAudio} key={exercise.id} controls={!recording} src={exercise.audio_url} preload="none" aria-label="Play listening exercise" onEnded={() => { completedPlayback.current = true; void listened(); }} onPlay={() => { if (flowRef.current === 'RECORDING') { listeningAudio.current?.pause(); return; } coachAudio.current?.pause(); }} onError={() => setError('The listening clip could not play. Retry playback below.')} />}</div>
        <p className="hint">AI-generated voice.</p>{error.includes('clip could not') && <button className="secondary" onClick={() => { listeningAudio.current?.load(); listeningAudio.current?.play().catch(() => setError('Playback could not start. Tap play to listen.')); }}>Retry playback</button>}
      </>}
      {conversation?.prompt && <div className="review"><>{result || conversation.active_followup ? <FeedbackCard terminal={!!result} outcome={result ? feedbackOutcome(result.evaluation.insufficient_evidence, result.evaluation.misunderstood) : 'Gap'}><p>{conversation.prompt}</p></FeedbackCard> : <div className="prompt"><h3>Your turn.</h3><p>{conversation.prompt}</p></div>}</>
        {coachUrl && <audio ref={coachAudio} key={cueKey} controls src={coachUrl} aria-label="Hear Audli’s message"
          onCanPlay={() => { if (!['AWAITING_SUMMARY', 'AWAITING_FOLLOWUP', 'GIVING_FEEDBACK', 'READY_FOR_NEXT'].includes(flowRef.current) || (listeningAudio.current && !listeningAudio.current.paused)) return; coachAudio.current?.play().catch(() => setCoachError('Tap play to hear Audli.')); }}
          onPlay={() => { if (flowRef.current === 'RECORDING') { coachAudio.current?.pause(); return; } listeningAudio.current?.pause(); recordingAudio.current?.pause(); setCoachSpeaking(true); }} onPause={() => setCoachSpeaking(false)}
          onEnded={cueEnded} onError={() => setCoachError('Audio playback failed. The message and your progress are safe; read it or retry audio.')}/>}
        {coachError && <p className="hint">{coachError} <button className="secondary" disabled={!!busy} onClick={() => setCoachRetry(value => value+1)}>Retry audio</button></p>}
      </div>}
      {!result && <>
        {flow.phase === 'LISTENING' && <p className="hint">Play the clip to the end to begin. {completedPlayback.current && <button className="secondary" disabled={!!busy} onClick={listened}>Retry Audli’s prompt</button>}</p>}
        <MicrophoneControl recording={recording} processing={['TRANSCRIBING','ASSESSING','ASSESSING_FOLLOWUP'].includes(flow.phase)} retry={!!error} disabled={!!busy || !canRespond(flow.phase)} elapsed={elapsed} onStart={startRecording} onFinish={stop} onCancel={cancelRecording}/>
        <p className="hint">After Audli’s prompt, recording starts automatically. Finish when you’re done. Grammar doesn’t affect understanding.</p>
        {canRespond(flow.phase) && !recording && !attempt && <details className="upload"><summary>Use an existing spoken recording</summary><label htmlFor="upload">Your response (2–120 seconds, up to 12 MB)</label><input id="upload" type="file" accept="audio/webm,audio/mp4,audio/mpeg,audio/wav,audio/ogg" disabled={!!busy} onChange={e => { const file=e.target.files?.[0]; if (file) { setBlob(file); setAttempt(null); dispatch({ type: 'phase', phase: 'REVIEWING_RECORDING' }); } }}/></details>}
        {blob && !recording && <div className="review"><h3>Your recording</h3>{recordingUrl && <audio ref={recordingAudio} controls src={recordingUrl} aria-label="Review your recording" onPlay={() => { if (flowRef.current === 'RECORDING') { recordingAudio.current?.pause(); return; } coachAudio.current?.pause(); }}/> }<button className="secondary" disabled={!!busy} onClick={transcribe}>Retry transcription</button></div>}
        {attempt && <div className="review"><h3>Did I hear you correctly?</h3>{attempt.transcription.uncertainty.map((u, i) => <p className="notice" key={i}>{u}</p>)}<label htmlFor="summary">Your response</label><textarea id="summary" value={text} maxLength={8000} onChange={e => setText(e.target.value)}/><p className="hint">Correct recognition errors to match what you said.</p><button className="primary" onClick={assess} disabled={!!busy || !text.trim()}>That’s what I said <span aria-hidden="true">→</span></button></div>}
      </>}
      {result && <>
        <button className="primary" onClick={next} disabled={!!busy}>Keep going <span aria-hidden="true">→</span></button>
        <details className="findings"><summary>See details</summary>
          <p>Observed comprehension: {scoreLabel(result.evaluation.overall)}{result.evaluation.coverage != null && ` · evidence coverage ${scoreLabel(result.evaluation.coverage)}`}</p>
          <div className="dimensions">{(['main_idea','details','vocabulary','inference'] as const).map(key => <div key={key}><div><span>{labels[key]}</span><strong>{scoreLabel(result.evaluation[key])}</strong></div>{result.evaluation.dimensions?.[key] && <p className="hint">{result.evaluation.dimensions[key].replaceAll('_', ' ')}</p>}</div>)}</div>
          {(['understood', 'insufficient_evidence', 'misunderstood', 'missed'] as const).map(key => (result.evaluation[key]?.length ?? 0) > 0 && <div key={key}><h3>{key === 'insufficient_evidence' ? 'Still unknown' : key.replaceAll('_', ' ')}</h3><ul>{result.evaluation[key]?.map((item, i) => <li key={i}>{item}</li>)}</ul></div>)}
          <p className="hint">{result.adaptation.reason}</p>
        </details>
        {!transcript ? <button className="secondary" disabled={!!busy} onClick={() => run('Opening transcript…', async () => setTranscript(await api<{ title: string; script: string }>(`/exercises/${exercise.id}/transcript`)))}>Show transcript</button> : <div className="transcript"><h3>{transcript.title}</h3><p>{transcript.script}</p></div>}
      </>}
      </div>
    </section>}
    {screen === 'home' && <HomeNavigation/>}
    <footer hidden={screen === 'home'}>Listen. Understand. Grow.<span>Audli uses your voice for transcription and doesn’t retain the recording.</span></footer>
  </main>;
}
