'use client';
import { HomeworkReview } from '../components/homework-review';
import { LessonTopicCheckin } from '../components/lesson-topic-checkin';
import { ListeningHomework } from '../components/listening-homework';
import { AdaptivePlan, PlanningInvitation } from '../components/adaptive-plan';
import { ReviewHub } from '../components/review-hub';
import { OnboardingReset } from '../components/onboarding-reset';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { AuthGate, useAccount } from '../components/auth-gate';
import { TutorVoiceSettings } from '../components/tutor-voice-settings';
import { AccentSettings, type AccentPreferences } from '../components/accent-settings';
import { SpokenOnboarding } from '../components/onboarding';
import { AudliLoadingScreen } from '../components/audli-loading-screen';
import { startup } from '../lib/startup';
import { LessonCelebration } from '../components/lesson-celebration';
import { AudliMascot } from '../components/audli-mascot';
import { BottomNavigation, TutorVoiceNotice, SessionShell, type Destination } from '../components/product-shell';
import { authenticatedFetch, ApiError, cancelPendingOperations, LessonLifetime, type LessonOperation } from '../lib/api';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { reliableRecognition, type Recognition } from '../lib/hands-free';
import { recordingUpload, sendRecording, InterruptedRecording } from '../lib/recording';
import { conversationIntent } from '../lib/conversation-intent';
import { measureTurn } from '../lib/turn-timing';
import { microphonePermission } from '../lib/session';
import type { ConversationPhase } from '../lib/conversation';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';

type Profile = { accent_preferences?: AccentPreferences; name: string; goal: string; focus?: string; interests?: string[]; target_situations?: string[]; completed_attempts: number };
type ProfileResponse = { destination: string; profile: Profile; provider: string; recognition_min_confidence?: number };
type Exercise = { id: string; audio_url: string; completed_attempt_id: string | null };
type Attempt = { id: string; transcription: Recognition; confirmed_text?: string | null };
type Conversation = { state: ConversationPhase; cue_id: string | null; prompt: string | null; active_followup: { id: string } | null; pending_attempt: Attempt | null; result: unknown; topic?: string | null };
const fetchClosing = (operation: LessonOperation, id: string) => authenticatedFetch(`/api/lessons/${id}/farewell-audio`, { method: 'POST', signal: AbortSignal.any([operation.signal, AbortSignal.timeout(20000)]) }, operation);
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const focusCopy: Record<string, string> = { main_idea: 'Follow the speaker’s main point.', details: 'Catch the details that change the meaning.', vocabulary: 'Understand useful words in context.', inference: 'Hear what the speaker means beyond the words.' };
export default function AudliPage() { return <AuthGate><ApplicationFlow/></AuthGate>; }

function ApplicationFlow() {
  const account = useAccount();
  const [reducedMotion, setReducedMotion] = useState(false);
  const motionKey = 'audli-reduced-motion:' + account.scope.split(':')[0];
  useEffect(() => {
    if (reducedMotion) document.documentElement.dataset.reducedMotion = 'true';
    else delete document.documentElement.dataset.reducedMotion;
    return () => { delete document.documentElement.dataset.reducedMotion; };
  }, [reducedMotion]);
  function updateMotion(value: boolean) {
    setReducedMotion(value);
    try { localStorage.setItem(motionKey, String(value)); } catch { /* Optional browser preference. */ }
  }
  const [destination, setDestination] = useState<string | null>(null);
  const [error, setError] = useState('');
  const owner = useRef<LessonLifetime | null>(null);
  async function load(operation: LessonOperation) {
    const data = await operation.api<ProfileResponse>('/profile'); operation.assertCurrent();
    if (!['onboarding','session_ready'].includes(data.destination)) throw new Error('Your listening space could not be restored.');
    try { setReducedMotion(localStorage.getItem(motionKey) === 'true'); } catch { /* Optional browser preference. */ }
    setDestination(data.destination); setError('');
  }
  const restore = useEffectEvent(load);
  useEffect(() => {
    const lifetime = new LessonLifetime(); owner.current = lifetime;
    const operation = lifetime.begin();
    // State updates occur only after authenticated profile resolution.
    void startup(() => restore(operation)).catch(error => { if (operation.current) setError(error.message); }).finally(() => operation.cancel());
    return () => lifetime.close();
  }, []);
  async function retry() {
    if (!owner.current?.current) return;
    const operation = owner.current.begin(); setError('');
    try { await startup(() => load(operation)); } catch (error) { if (operation.current) setError((error as Error).message); } finally { operation.cancel(); }
  }
  if (!destination) return <AudliLoadingScreen message="Getting your listening space ready…" error={error} onRetry={() => void retry()}/>;
  if (destination === 'onboarding') return <SpokenOnboarding onComplete={load}/>;
  return <Product onRestart={load} reducedMotion={reducedMotion} setReducedMotion={updateMotion}/>;
}

function Product({ onRestart, reducedMotion, setReducedMotion }: { onRestart: (operation: LessonOperation) => Promise<void>; reducedMotion: boolean; setReducedMotion: (value: boolean) => void }) {
  const account = useAccount();
  const voice = useVoiceLifecycle();
  const [initialized, setInitialized] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [destination, setDestination] = useState<Destination>('Home');
  const [active, setActive] = useState(false);
  const [homeworkVoice, setHomeworkVoice] = useState(false);
  const [wrapupReady, setWrapupReady] = useState(false);
  const presentedCompletion = useRef<string | null>(null);
  const completedTopic = useRef<string | null>(null);
  const [wrapupFinished, setWrapupFinished] = useState(false);
  const [closing, setClosing] = useState(false);
  const closingStarted = useRef(false);
  const [reviewLesson, setReviewLesson] = useState<string | null>(null);
  const reviewDone = useRef<(() => void) | null>(null);
  const [classroomLayout, setClassroomLayout] = useState<'focus' | 'interaction' | 'content'>('focus');
  const [completion, setCompletion] = useState<LessonCheckpoint | null>(null);
  const [celebrationLesson, setCelebrationLesson] = useState<string | null>(null);
  const completionHeading = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => { if (completion) completionHeading.current?.focus(); }, [completion, wrapupFinished, celebrationLesson, wrapupReady]);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [prompt, setPrompt] = useState('');
  const [topic, setTopic] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<{ title: string; script: string } | null>(null);
  const [eligibleExercise, setEligibleExercise] = useState<string | null>(null);
  const current = useRef<Exercise | null>(null);
  const lesson = useRef<LessonCheckpoint | null>(null);
  const ending = useRef<Promise<void> | null>(null);
  const minimum = useRef(.65);
  const running = useRef(false);
  const retained = useRef<{ exercise: string; blob: Blob } | null>(null);
  const personalCapture = useRef<Blob | null>(null);
  const storageKey = 'audli-session:' + account.scope;
  const intent = conversationIntent(account.scope, 'lesson');
  async function refresh(operation: LessonOperation) {
    const [data, exercise] = await operation.wait(() => Promise.all([
      operation.api<ProfileResponse>('/profile'), operation.api<Exercise | null>('/exercises/current'),
    ]));
    operation.assertCurrent(); setProfile(data.profile); current.current = exercise; minimum.current = data.recognition_min_confidence ?? .65;
  }
  async function restore(operation: LessonOperation) {
    await refresh(operation);
    const checkpoint = await operation.api<LessonCheckpoint | null>('/lessons/current');
    operation.assertCurrent(); lesson.current = checkpoint; setBusy(false);
    if (checkpoint?.status === 'active' && !intent.ended()) { void start(false); return; }
    try {
      const completedId = sessionStorage.getItem(storageKey);
      if (!checkpoint && completedId) {
        const saved = await operation.api<LessonCheckpoint>(`/lessons/${completedId}`);
        operation.assertCurrent(); if (saved.status === 'completed') {
          presentedCompletion.current = saved.id;
          completedTopic.current = saved.id; // Restoring never automatically reopens optional preferences.
          setWrapupReady(true);
          setWrapupFinished(sessionStorage.getItem(storageKey + ':finished') === saved.id);
          setCompletion(saved);
        }
      }
    } catch { operation.assertCurrent(); }
  }
  const initialRestore = useEffectEvent(restore);
  useEffect(() => {
    const operation = voice.begin();
    // State updates occur only after the external checkpoint resolves.
    void startup(() => initialRestore(operation)).then(() => { operation.assertCurrent(); setInitialized(true); }).catch(error => { if (operation.current) { setError(error.message); setBusy(false); } }).finally(() => operation.cancel());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function playCue(operation: LessonOperation, exercise: Exercise, state: Conversation) {
    voice.thinking();
    setPrompt(state.prompt ?? '');
    await voice.coachSpeech(operation, exercise.id, state.cue_id, coachingCheckpoint(exercise));
  }
  function coachingCheckpoint(exercise: Exercise) {
    const checkpoint = lesson.current;
    return checkpoint?.phase === 'EXERCISE' && checkpoint.exercise?.id === exercise.id
      ? { lesson_id: checkpoint.id, lesson_revision: checkpoint.revision } : undefined;
  }
  async function captureAnswer(operation: LessonOperation, exercise: Exercise): Promise<Attempt> {
    let blob = retained.current?.exercise === exercise.id ? retained.current.blob : null;
    if (!blob) {
      try { blob = await voice.listen(operation); }
      catch (error) { if (operation.current && error instanceof InterruptedRecording) retained.current = { exercise: exercise.id, blob: error.blob }; throw error; }
      operation.assertCurrent(); retained.current = { exercise: exercise.id, blob };
    }
    voice.thinking();
    const attempt = await measureTurn('upload_transcription', () => sendRecording(() => operation.api<Attempt>(`/exercises/${exercise.id}/attempts`, { method: 'POST', body: recordingUpload(blob!) }), () => { retained.current = null; }));
    operation.assertCurrent(); retained.current = null; return attempt;
  }
  async function exerciseConversation(operation: LessonOperation, exercise: Exercise, intentionalRestart = false) {
    operation.assertCurrent(); current.current = exercise;
    let state = await operation.api<Conversation>(`/exercises/${exercise.id}/conversation`);
    if (intentionalRestart && exercise.completed_attempt_id) {
      await operation.api<Conversation>(`/exercises/${exercise.id}/conversation/ready`, { method: 'POST' });
      return;
    }
    // Explicit End starts a fresh presentation of an unfinished clip, never a
    // fragment of old coaching. Evidence/pending recognition remain authoritative.
    if (intentionalRestart && state.state !== 'LISTENING' && !exercise.completed_attempt_id) {
      await voice.speak(operation, await operation.audio(exercise.audio_url));
      state = await operation.api<Conversation>(`/exercises/${exercise.id}/conversation`);
    }
    let retries = 0;
    while (operation.current) {
      setEligibleExercise(state.result ? exercise.id : null);
      setTopic(state.topic ?? null);
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
          voice.thinking(); setPrompt('');
          try {
            state = await voice.acknowledged(operation, `${account.scope}:attempt:${attempt!.id}`, () => measureTurn('assessment', () => operation.api<Conversation>(`/attempts/${attempt!.id}/assess`, json({ text: corrected ?? attempt!.transcription.text, confirmed: true, hands_free: !corrected, followup_id: state.active_followup?.id ?? null }))), state.active_followup ? 'followup' : 'assessment', setPrompt, saved => {
              if (saved.cue_id && (saved.state === 'GIVING_FEEDBACK' || saved.state === 'AWAITING_FOLLOWUP'))
                voice.prepareCoachSpeech(operation, exercise.id, saved.cue_id, coachingCheckpoint(exercise));
            });
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
        return;
      } else throw new Error('Your conversation could not be restored. Please retry.');
    }
  }
  async function lessonAction(operation: LessonOperation, value: LessonCheckpoint, action: string) {
    try { return await operation.api<LessonCheckpoint>(`/lessons/${value.id}/${action}`, json({ revision: value.revision })); }
    catch (error) {
      if (!(error instanceof ApiError) || error.status !== 409) throw error;
      const latest = await operation.api<LessonCheckpoint>(`/lessons/${value.id}`);
      if (latest.status === 'paused') throw new Error('Your lesson was ended. Return Home and resume when you’re ready.');
      if (latest.revision === value.revision) throw error;
      return latest;
    }
  }
  async function structuredLesson(operation: LessonOperation, initial: LessonCheckpoint, intentionalRestart: boolean) {
    let value = initial;
    while (operation.current) {
      operation.assertCurrent(); lesson.current = value;
      setStartedAt(Date.now() - value.elapsed_seconds * 1000);
      setPrompt(value.prompt ?? '');
      if (value.phase === 'COMPLETED') {
        await refresh(operation); operation.assertCurrent();
        try { sessionStorage.setItem(storageKey, value.id); } catch { /* Optional completion presentation memory. */ }
        if (presentedCompletion.current === value.id) { setWrapupReady(completedTopic.current === value.id); setCompletion(value); setActive(false); return; }
        presentedCompletion.current = value.id; completedTopic.current = null;
        closingStarted.current = false; setClosing(false); setWrapupFinished(false); setWrapupReady(false); setHomeworkVoice(true); setCelebrationLesson(value.id); setCompletion(value); setActive(false); return;
      }
      if (value.phase === 'TRANSITION' && account.homeworkEnabled) {
        await operation.wait(() => new Promise<void>(resolve => { reviewDone.current = resolve; setReviewLesson(value.id); }));
        operation.assertCurrent(); setReviewLesson(null);
      }
      if (value.status !== 'active') throw new Error('Resume your lesson to continue.');
      if (value.phase === 'EXERCISE') {
        if (!value.exercise) { setTopic(null); value = await lessonAction(operation, value, 'exercise'); continue; }
        if (value.introduction_pending) {
          setTopic(value.exercise.topic ?? null);
          voice.thinking();
          setPrompt(value.introduction_prompt ?? 'Let’s listen to the next recording.');
          await voice.lessonSpeech(operation, value.id, value.revision, 'introduction-audio');
          value = await lessonAction(operation, value, 'introduction-heard');
          continue;
        }
        current.current = value.exercise; setTranscript(null);
        await exerciseConversation(operation, value.exercise, intentionalRestart);
        intentionalRestart = false;
        value = await lessonAction(operation, value, 'advance'); continue;
      }
      setEligibleExercise(null);
      if (value.phase === 'WELCOME' || value.phase === 'REVIEW') {
        let retries = 0;
        if (value.pending) personalCapture.current = null;
        if (!value.pending) await voice.lessonSpeech(operation, value.id, value.revision);
        while (operation.current) {
          if (!value.pending) {
            if (!personalCapture.current) {
              try { personalCapture.current = await voice.listen(operation); }
              catch (error) { if (operation.current && error instanceof InterruptedRecording) personalCapture.current = error.blob; throw error; }
            }
            const form = recordingUpload(personalCapture.current); form.append('revision', String(value.revision));
            value = await measureTurn('upload_transcription', () => sendRecording(() => operation.api<LessonCheckpoint>(`/lessons/${value.id}/attempts`, { method: 'POST', body: form }), () => { personalCapture.current = null; }));
            operation.assertCurrent(); personalCapture.current = null; lesson.current = value;
          }
          if (!value.pending || !reliableRecognition(value.pending, minimum.current)) {
            if (++retries > 2) throw new Error('Your microphone is hard to hear. Check it, then retry this turn.');
            await voice.retrySpeech(operation); value = { ...value, pending: null }; continue;
          }
          setPrompt('');
          value = value.phase === 'WELCOME' ? await measureTurn('checkin_response', () => lessonAction(operation, value, 'answer')) :
            await voice.acknowledged(operation, `${account.scope}:reflection:${value.id}:${value.revision}`, () => measureTurn('profile_extraction', () => lessonAction(operation, value, 'answer')), 'reflection', setPrompt);
          break;
        }
      } else {
        try { await voice.lessonSpeech(operation, value.id, value.revision); }
        catch (cause) { operation.assertCurrent(); if (value.phase !== 'CLOSING') throw cause; }
        value = await lessonAction(operation, value, 'heard');
      }
    }
  }
  async function start(preflight = true) {
    if (running.current) return;
    const operation = voice.begin(); running.current = true; setError(''); setBusy(true);
    setActive(true); setEligibleExercise(null); setTranscript(null); voice.thinking();
    let microphoneReady = !preflight;
    try {
      if (ending.current) await operation.wait(() => ending.current!);
      if (preflight) await operation.wait(() => microphonePermission(navigator.mediaDevices));
      microphoneReady = true; operation.assertCurrent();
      let value = await operation.api<LessonCheckpoint>('/lessons/start', { method: 'POST' });
      const intentionalRestart = intent.ended() || value.status === 'paused';
      if (intent.ended() && value.status === 'active') value = await operation.api<LessonCheckpoint>(`/lessons/${value.id}/end`, { method: 'POST' });
      if (value.status === 'paused') value = await operation.api<LessonCheckpoint>(`/lessons/${value.id}/resume`, { method: 'POST' });
      operation.assertCurrent(); lesson.current = value; intent.start();
      setActive(true); if (value.phase !== 'COMPLETED') setCompletion(null); setBusy(false);
      await structuredLesson(operation, value, intentionalRestart);
    } catch (error) { if (operation.current) { voice.idle(); setError((error as Error).message); setBusy(false); if (!microphoneReady) setActive(false); } }
    finally { if (operation.current) running.current = false; operation.release(); }
  }
  function end() {
    reviewDone.current?.(); reviewDone.current = null; setReviewLesson(null);
    voice.stop(); running.current = false; retained.current = null; personalCapture.current = null; setTopic(null);
    intent.end(); setActive(false); setBusy(false); setError(''); setPrompt(''); setDestination('Home');
    const value = lesson.current;
    if (value) {
      const operation = voice.begin();
      ending.current = operation.api<LessonCheckpoint>(`/lessons/${value.id}/end`, { method: 'POST' })
        .then(saved => { operation.assertCurrent(); lesson.current = saved; })
        .catch(error => { if (operation.current) setError((error as Error).message); })
        .finally(() => { operation.release(); ending.current = null; });
    }
  }
  function cancelForOnboarding() {
    cancelPendingOperations();
    document.querySelectorAll('audio').forEach(player => player.pause());
    voice.stop(); running.current = false; retained.current = null; personalCapture.current = null;
    intent.end(); lesson.current = null; current.current = null; ending.current = null;
    setActive(false); setBusy(false); setCompletion(null); setTranscript(null); setEligibleExercise(null);
    setTopic(null); setPrompt(''); setError('');
  }
  async function reviewTranscript(exerciseId: string) {
    const operation = voice.begin(); setError('');
    try { const data = await operation.api<{ title: string; script: string }>(`/exercises/${exerciseId}/transcript`); operation.assertCurrent(); setTranscript(data); }
    catch (error) { if (operation.current) setError((error as Error).message); } finally { operation.release(); }
  }
  async function retry() {
    if (!initialized) {
      const operation = voice.begin(); setError('');
      try { await startup(() => restore(operation)); operation.assertCurrent(); setInitialized(true); }
      catch (error) { if (operation.current) setError((error as Error).message); }
      finally { operation.cancel(); }
      return;
    }
    if (active) { await start(false); }
    else { const operation = voice.begin(); try { await refresh(operation); setError(''); } catch (error) { if (operation.current) setError((error as Error).message); } finally { operation.release(); } }
  }
  function finishTopic(id: string) {
    if (presentedCompletion.current !== id || completedTopic.current === id) return;
    completedTopic.current = id;
    setWrapupReady(true);
  }
  async function finishWrapup(audible = homeworkVoice) {
    if (!completion || closingStarted.current) return;
    closingStarted.current = true; setClosing(true);
    const operation = voice.begin();
    const completionId = completion.id;
    let timer: ReturnType<typeof setTimeout> | null = null;
    try {
      if (audible && account.homeworkEnabled) {
        voice.thinking();
        const response = await operation.wait(() => fetchClosing(operation, completion.id));
        if (!response.ok) throw new Error('Closing audio unavailable');
        const clip = await operation.wait(() => response.blob());
        await Promise.race([
          voice.speak(operation, URL.createObjectURL(clip)),
          new Promise<void>((_, reject) => { timer = setTimeout(() => reject(new Error('Closing interrupted')), 20000); closingTimer.current = timer; }),
        ]);
      }
    } catch { /* Saved completion always wins over optional closing audio. */ }
    finally {
      if (timer) clearTimeout(timer);
      if (closingTimer.current === timer) closingTimer.current = null;
      if (operation.current && presentedCompletion.current === completionId) { operation.cancel(); voice.idle(); setClosing(false); setWrapupFinished(true); setHomeworkVoice(false); try { sessionStorage.setItem(storageKey + ':finished', completion.id); } catch { /* Presentation memory only. */ } }
      operation.release();
    }
  }
  const closingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (closingTimer.current) clearTimeout(closingTimer.current); reviewDone.current?.(); }, []);
  const finishOptional = useEffectEvent(() => { void finishWrapup(); });
  useEffect(() => { if (completion && wrapupReady && !account.homeworkEnabled && !wrapupFinished) finishOptional(); }, [completion, wrapupReady, account.homeworkEnabled, wrapupFinished]);
  async function navigate(value: Destination) {
    setDestination(value); setTranscript(null); setError('');
  }
  if (!initialized && !active) return <AudliLoadingScreen message="Getting your listening space ready…" error={error} onRetry={() => void retry()}/>;
  if (active) return <><SessionShell backupVoice={voice.backupVoice} state={voice.state} activity={voice.activity} layout={reviewLesson ? 'interaction' : undefined} topic={topic} startedAt={startedAt ?? undefined} onEnd={end} onReplay={voice.state === 'Speaking' ? voice.replay : undefined}>
    {reviewLesson && <HomeworkReview key={reviewLesson} lessonId={reviewLesson} voice={voice} onFinished={() => { reviewDone.current?.(); reviewDone.current = null; }}/>}
    <p className="sr-only">{prompt}</p>{error && <div role="alert" className="session-error">{error}<p>{prompt}</p><button className="secondary" onClick={retry}>Retry conversation</button>{(retained.current || personalCapture.current) && <button className="secondary" onClick={() => { retained.current = null; personalCapture.current = null; void retry(); }}>Record again</button>}</div>}
    {eligibleExercise && <details className="session-transcript"><summary>Transcript</summary>{transcript ? <p>{transcript.script}</p> : <button className="text-button" onClick={() => void reviewTranscript(eligibleExercise)}>Read transcript</button>}</details>}
  </SessionShell><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/></>;
  if (completion && celebrationLesson === completion.id) return <LessonCelebration completionId={completion.id} scope={account.scope} reducedMotion={reducedMotion} onComplete={() => setCelebrationLesson(current => current === completion.id ? null : current)}/>;
  if (completion) return <><SessionShell completed label={voice.state === 'Speaking' ? 'Audli is speaking' : voice.state === 'Listening' ? 'Listening to you' : 'Your lesson is saved.'} showPresence={voice.state === 'Speaking' || voice.state === 'Listening' || voice.state === 'Thinking'} layout={classroomLayout} backupVoice={voice.backupVoice} state={voice.state === 'Idle' ? 'Success' : voice.state} activity={voice.activity} onEnd={() => {
    voice.stop(); if (closingTimer.current) clearTimeout(closingTimer.current); setClosing(false); setHomeworkVoice(false); setWrapupReady(false); setCompletion(null); lesson.current = null;
    try { sessionStorage.removeItem(storageKey); } catch { /* Optional presentation memory. */ }
  }}>
    <h1 className="wrapup-heading" ref={completionHeading} tabIndex={-1}>Lesson completed</h1>
    <p className="sr-only">{completion.encouragement}</p>
    <div className="completion-summary"><p>{completion.exercises_completed} exercise{completion.exercises_completed === 1 ? '' : 's'} completed · {focusCopy[completion.focus]}</p>{completion.strength && <p>A demonstrated strength: {completion.strength}</p>}{completion.improvement_focus && <p>Next listening focus: {completion.improvement_focus}</p>}</div>
    {!wrapupReady && !wrapupFinished && <LessonTopicCheckin key={completion.id} lessonId={completion.id} autoVoice={homeworkVoice} voice={voice} onLayout={setClassroomLayout} onFinished={() => finishTopic(completion.id)}/>}
    {wrapupReady && <ListeningHomework inline disabled={closing} finished={wrapupFinished} lessonId={completion.id} autoVoice={homeworkVoice} sharedVoice={voice} onLayout={setClassroomLayout}/>}
    {wrapupReady && account.homeworkEnabled && !wrapupFinished && <button className="primary" disabled={closing} onClick={() => { voice.stop(); void finishWrapup(true); }}>{closing ? 'Finishing…' : 'Finish lesson'}</button>}
  </SessionShell><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/></>;

  return <main className="product-shell"><header><strong className="wordmark">audli</strong><span className="language">TRAIN YOUR EARS</span></header><TutorVoiceNotice used={voice.backupVoice}/>
    {destination === 'Home' && <section className="home-view"><h1>{`Good to see you${profile?.name ? ', ' + profile.name : ''}.`}</h1><AudliMascot/>
      <PlanningInvitation open={() => setDestination('Plan')}/><h2>Today’s listening lesson</h2><p>10 minutes · English · adapted for you</p><button className="primary" disabled={busy || !profile} onClick={() => void start()}>Start today’s session</button><p className="home-support">Your next lesson will build from what Audli heard last time.</p>
    </section>}
    {destination === 'Review' && <ReviewHub key={account.scope}/>}
    {destination === 'Plan' && <AdaptivePlan/>}
    {destination === 'Settings' && <section className="shell-view"><h1>Settings</h1><dl><dt>Account</dt><dd>{account.email ?? 'Local development account'}{account.signOut && <button className="text-button" onClick={() => void account.signOut?.()}>Sign out</button>}</dd><dd><OnboardingReset onConfirm={cancelForOnboarding} onRestart={onRestart}/></dd><dt>Voice & playback</dt><dd><TutorVoiceSettings/>Audli speaks automatically. Replay is available while Audli is speaking.</dd><dt>Accent difficulties</dt><dd><AccentSettings preferences={profile?.accent_preferences} onSaved={value => setProfile(current => current ? { ...current, accent_preferences: value } : current)}/></dd><dt>Accessibility</dt><dd><label className="toggle"><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)}/>Reduce motion</label><p>System reduced-motion preferences always apply.</p></dd><dt>Privacy</dt><dd>Your microphone recording is discarded after transcription. Your listening progress is saved to your account.</dd><dt>Help</dt><dd>Allow microphone access in your browser to speak with Audli.</dd></dl></section>}
    {busy && <p role="status">Loading your listening space…</p>}{error && <div role="alert" className="session-error">{error}<button className="secondary" onClick={retry}>Retry connection</button></div>}
    <BottomNavigation destination={destination} onNavigate={value => void navigate(value)}/><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/>
  </main>;
}
