'use client';
import { HomeworkReview } from '../components/homework-review';
import { LessonTopicCheckin } from '../components/lesson-topic-checkin';
import { INITIAL_PHASE, lessonPhase, after, type PhaseEvent } from '../lib/lesson-phase';
import type { Plan } from '../lib/planner';
import { ListeningHomework } from '../components/listening-homework';
import { AdaptivePlan, PlanningInvitation } from '../components/adaptive-plan';
import { ReviewHub } from '../components/review-hub';
import { OnboardingReset } from '../components/onboarding-reset';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { AuthGate, useAccount } from '../components/auth-gate';
import { AccountSettings } from '../components/account-settings';
import { TutorVoiceSettings } from '../components/tutor-voice-settings';
import { AccentSettings, type AccentPreferences } from '../components/accent-settings';
import { SpokenOnboarding } from '../components/onboarding';
import { AudliLoadingScreen } from '../components/audli-loading-screen';
import { startup } from '../lib/startup';
import { LessonSuccess } from '../components/lesson-success';
import type { HomeworkAssignment } from '../lib/homework';
import { prefetchCelebration } from '../lib/mascot-celebration';
import { AudliMascot } from '../components/audli-mascot';
import { BottomNavigation, TutorVoiceNotice, SessionShell, type Destination } from '../components/product-shell';
import { authenticatedFetch, ApiError, cancelPendingOperations, isLimitError, LessonLifetime, type LessonOperation } from '../lib/api';
import { HomeLesson } from '../components/home-lesson';
import { useEligibility } from '../lib/use-eligibility';
import { homeView } from '../lib/eligibility';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { reliableRecognition, type Recognition } from '../lib/hands-free';
import { InterruptedRecording } from '../lib/recording';
import { prepareNewRecording, deliverCheckin, deliverExercise } from '../lib/recording-delivery';
import { DeliveryFailed, boundedTask, type DeliveryState } from '../lib/upload-sender';
import { noticeFor, NOTICE_TEXT, type Notice } from '../lib/delivery-notice';
import { clearUnsent, markUnsent, readUnsent, type UnsentKind } from '../lib/unsent-marker';
import { DeliveryNotice } from '../components/delivery-notice';
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
  const eligibility = useEligibility(account.scope);
  // A calm, non-error explanation when a daily-limit refusal arrives but the eligibility endpoint is unavailable.
  const [limitMessage, setLimitMessage] = useState('');
  const voice = useVoiceLifecycle();
  const [initialized, setInitialized] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [destination, setDestination] = useState<Destination>('Home');
  const [active, setActive] = useState(false);
  const [homeworkVoice, setHomeworkVoice] = useState(false);
  // One lifecycle for every screen; the ref mirrors it for async continuations that must not read stale renders.
  const [lifecycle, setLifecycle] = useState(INITIAL_PHASE);
  const phase = useRef(INITIAL_PHASE);
  function send(event: PhaseEvent) {
    const next = lessonPhase(phase.current, event);
    if (next !== phase.current) { phase.current = next; setLifecycle(next); }
    return next;
  }
  useEffect(() => { document.documentElement.dataset.lifecyclePhase = lifecycle.phase; return () => { delete document.documentElement.dataset.lifecyclePhase; }; }, [lifecycle.phase]);
  const [topicPlan, setTopicPlan] = useState<Plan | null>(null);
  const topicDone = useRef<(() => void) | null>(null);
  const farewellDue = useRef(false);
  const offeredTopic = useRef<string | null>(null);
  const [reviewLesson, setReviewLesson] = useState<string | null>(null);
  const reviewDone = useRef<(() => void) | null>(null);
  const [classroomLayout, setClassroomLayout] = useState<'focus' | 'interaction' | 'content'>('focus');
  const [resolvedHomework, setResolvedHomework] = useState<HomeworkAssignment | null | undefined>(undefined);
  const [completion, setCompletion] = useState<LessonCheckpoint | null>(null);
  const completionHeading = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => { if (completion) completionHeading.current?.focus(); }, [completion, lifecycle.phase]);
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
  // AUD-89 P2: delivery states for the learner. A failed delivery keeps the recording (retained / personalCapture) and offers Send again /
  // Record again; recovery uses the held checkpoint directly without restarting question playback.
  const [notice, setNotice] = useState<Notice | null>(null);

  function reportDelivery(kind: UnsentKind, id: string) {
    return (state: DeliveryState) => {
      if (state.kind === 'sending' || state.kind === 'processing') markUnsent(account.scope, kind, id);
      else if (state.kind === 'delivered' || state.kind === 'moved') clearUnsent(account.scope);
      setNotice(noticeFor(state));
    };
  }
  const storageKey = 'audli-session:' + account.scope;
  const intent = conversationIntent(account.scope, 'lesson');
  async function refresh(operation: LessonOperation) {
    const [data, exercise] = await operation.wait(() => Promise.all([
      operation.api<ProfileResponse>('/profile'), operation.api<Exercise | null>('/exercises/current'),
    ]));
    operation.assertCurrent(); setProfile(data.profile); current.current = exercise; minimum.current = data.recognition_min_confidence ?? .65;
  }
  /** Background profile/exercise refresh: never gates a screen, failures are ignored (Return Home refreshes again). */
  function refreshQuietly() {
    void Promise.all([authenticatedFetch('/api/profile').then(r => r.ok ? r.json() as Promise<ProfileResponse> : null), authenticatedFetch('/api/exercises/current').then(r => r.ok ? r.json() as Promise<Exercise | null> : null)])
      .then(([data, exercise]) => { if (!data) return; setProfile(data.profile); current.current = exercise; minimum.current = data.recognition_min_confidence ?? .65; })
      .catch(() => {});
  }
  async function restore(operation: LessonOperation) {
    await refresh(operation);
    const checkpoint = await operation.api<LessonCheckpoint | null>('/lessons/current');
    operation.assertCurrent(); lesson.current = checkpoint; setBusy(false);
    if (checkpoint?.status === 'active' && !intent.ended()) {
      // An upload was in flight when the page went away and the recording is gone: say so first, and wait for the learner before replaying anything.
      if (readUnsent(account.scope)) { setActive(true); setNotice({ kind: 'unsent', text: NOTICE_TEXT.unsent }); return; }
      void start(false); return;
    }
    try {
      const completedId = sessionStorage.getItem(storageKey);
      if (!checkpoint && completedId) {
        const saved = await operation.api<LessonCheckpoint>(`/lessons/${completedId}`);
        operation.assertCurrent();
        // A restored completion resumes at the homework query or the final card, never earlier; its celebration is not replayed.
        if (saved.status === 'completed' && send({ type: 'restored', lessonId: saved.id, homeworkResolved: sessionStorage.getItem(storageKey + ':homework') === saved.id }).lessonId === saved.id) setCompletion(saved);
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
  async function captureAnswer(operation: LessonOperation, exercise: Exercise, replacesAttemptId?: string): Promise<Attempt> {
    let blob = retained.current?.exercise === exercise.id ? retained.current.blob : null;
    if (!blob) {
      try { blob = await voice.listen(operation); }
      catch (error) { if (operation.current && error instanceof InterruptedRecording) retained.current = { exercise: exercise.id, blob: error.blob }; throw error; }
      operation.assertCurrent(); retained.current = { exercise: exercise.id, blob };
    }
    voice.thinking();
    const attempt = await measureTurn('upload_transcription', () => deliverExercise(operation, exercise.id, blob!, () => { retained.current = null; clearUnsent(account.scope); }, { onState: reportDelivery('exercise', exercise.id), replacesAttemptId }));
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
        if (!attempt) {
          await playCue(operation, exercise, state);
          attempt = await captureAnswer(operation, exercise);
        }
        while (operation.current) {
          // A correction explicitly confirmed before AUD-17 is already a server checkpoint.
          const confirmed = attempt.confirmed_text?.trim();
          const corrected = confirmed && confirmed !== attempt.transcription.text.trim() ? confirmed : null;
          if (!corrected && !reliableRecognition(attempt.transcription, minimum.current)) {
            if (++retries > 2) throw new Error('Your microphone is hard to hear. Check it, then retry this turn.');
            setPrompt("I didn't quite catch that. Could you say it again?");
            await voice.retrySpeech(operation); attempt = await captureAnswer(operation, exercise, attempt.id); continue;
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
            await voice.retrySpeech(operation); attempt = await captureAnswer(operation, exercise, attempt.id);
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
      const before = phase.current;
      send({ type: 'checkpoint', lessonId: value.id, phase: value.phase });
      setStartedAt(Date.now() - value.elapsed_seconds * 1000);
      setPrompt(value.prompt ?? '');
      if (value.phase === 'COMPLETED') {
        // Wrap-up shows at once; the profile refresh is no longer a blocking step in front of it.
        try { sessionStorage.setItem(storageKey, value.id); } catch { /* Optional completion presentation memory. */ }
        // Duplicate or delayed completion responses find the lifecycle already past FEEDBACK and change nothing.
        if (before.lessonId !== value.id || !after(before, 'HOMEWORK')) { setHomeworkVoice(true); farewellDue.current = true; }
        setCompletion(value); setActive(false); refreshQuietly(); return;
      }
      if (value.phase === 'TRANSITION' && account.homeworkEnabled) {
        await operation.wait(() => new Promise<void>(resolve => { reviewDone.current = resolve; setReviewLesson(value.id); }));
        operation.assertCurrent(); setReviewLesson(null);
      }
      if (value.status === 'expired') { limitEnded(value.id); return; }
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
        let serverPending = value.pending; // what the server holds before another recording
        if (value.pending) personalCapture.current = null;
        if (!value.pending) await voice.lessonSpeech(operation, value.id, value.revision, 'audio', value.phase === 'WELCOME');
        while (operation.current) {
          if (!value.pending) {
            if (!personalCapture.current) {
              try { personalCapture.current = await voice.listen(operation); }
              catch (error) { if (operation.current && error instanceof InterruptedRecording) personalCapture.current = error.blob; throw error; }
            }
            const sent = await measureTurn('upload_transcription', () => deliverCheckin(operation, value, personalCapture.current!, () => { personalCapture.current = null; clearUnsent(account.scope); }, serverPending, { onState: reportDelivery('checkin', value.id) }));
            operation.assertCurrent(); personalCapture.current = null; value = sent.value; lesson.current = value; serverPending = value.pending;
            if (sent.via === 'moved') break;   // the server moved on without this recording: continue from its checkpoint
          }
          // Only the server knows which closed question is active. Older servers
          // omit the verdict and retain the strict recognition behavior.
          if (!value.pending || !(value.pending_recognition === 'accepted'
            || (value.pending_recognition === undefined && reliableRecognition(value.pending, minimum.current)))) {
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
  /** Optional preferences are asked here, before any lesson exists, and never again once one has begun. */
  async function topicSetup(operation: LessonOperation) {
    if (phase.current.phase !== 'HOME') return;
    let plan: Plan;
    try { plan = await operation.api<Plan>('/plan'); } catch { operation.assertCurrent(); return; } // Optional planning never gates a lesson.
    if (!plan.checkin.available) return;
    // Offered once per three-lesson milestone. Answering or "Keep my topics" resolves it on the server;
    // an interrupted or dismissed one is deferred (still available in Plan), never re-asked at every Start.
    const milestone = String(plan.checkin.after_lesson), deferredKey = 'audli-topic-offered:' + account.scope;
    let deferred = offeredTopic.current === milestone;
    try { deferred ||= localStorage.getItem(deferredKey) === milestone; } catch { /* In-memory record still applies. */ }
    if (deferred) return;
    offeredTopic.current = milestone;
    try { localStorage.setItem(deferredKey, milestone); } catch { /* Optional browser memory. */ }
    send({ type: 'topic_setup' }); setTopicPlan(plan);
    await operation.wait(() => new Promise<void>(resolve => { topicDone.current = resolve; }));
    topicDone.current = null; setTopicPlan(null); send({ type: 'topic_resolved' });
    operation.assertCurrent();
  }
  /** The daily limit ended this lesson (or refused its start): a calm Home, never a generic error screen. Saved work stays. */
  function limitEnded(lessonId: string | null, message = '') {
    voice.stop(); voice.idle(); running.current = false; retained.current = null; personalCapture.current = null;
    leaveSetup(); reviewDone.current?.(); reviewDone.current = null; setReviewLesson(null); send({ type: 'home' });
    intent.end(); lesson.current = null; setActive(false); setBusy(false); setError(''); setPrompt(''); setTopic(null); setDestination('Home');
    setLimitMessage(message || (lessonId ? 'Today’s lesson time has ended. We saved your work.' : ''));
    void eligibility.refresh(true).then(() => eligibility.announce()); refreshQuietly();
  }
  /** A refusal mid-lesson (429 lesson_time_expired) is resolved by the server's own settled state: the closing sequence
   *  continues from completed work, or the lesson is shown as expired. The browser never decides which. */
  async function runWithLimitRecovery(operation: LessonOperation, first: LessonCheckpoint, intentionalRestart: boolean) {
    let value = first, restart = intentionalRestart;
    for (let attempt = 0; ; attempt++) {
      try { return await structuredLesson(operation, value, restart); }
      catch (error) {
        const id = lesson.current?.id;
        if (!isLimitError(error) || error.limit.code !== 'lesson_time_expired' || !id || attempt >= 3 || !operation.current) throw error;
        const latest = await operation.api<LessonCheckpoint>(`/lessons/${id}`); operation.assertCurrent();
        if (latest.status === 'expired') { limitEnded(latest.id); return; }
        value = latest.status === 'paused' ? await operation.api<LessonCheckpoint>(`/lessons/${latest.id}/resume`, { method: 'POST' }) : latest;
        restart = true;
      }
    }
  }
  /** Home's only door to the classroom: an enforcing server must confirm eligibility again right now. */
  async function startFromHome() {
    setLimitMessage('');
    // Always ask (joins the request already in flight): Home may not have heard from the server yet.
    const confirmed = homeView(await eligibility.refresh(eligibility.view.kind !== 'legacy'), performance.now());
    if (confirmed.kind === 'cooldown' || confirmed.kind === 'checking') return;
    void start();
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
      if (preflight) await topicSetup(operation);
      let value = await operation.api<LessonCheckpoint>('/lessons/start', { method: 'POST' });
      const intentionalRestart = intent.ended() || value.status === 'paused';
      if (intent.ended() && value.status === 'active') value = await operation.api<LessonCheckpoint>(`/lessons/${value.id}/end`, { method: 'POST' });
      if (value.status === 'paused') value = await operation.api<LessonCheckpoint>(`/lessons/${value.id}/resume`, { method: 'POST' });
      operation.assertCurrent(); lesson.current = value; intent.start();
      setActive(true); if (value.phase !== 'COMPLETED') { setCompletion(null); setResolvedHomework(undefined); } setBusy(false);
      eligibility.announce();
      await runWithLimitRecovery(operation, value, intentionalRestart);
    } catch (error) {
      if (operation.current && isLimitError(error)) limitEnded(lesson.current?.id ?? null, error.message);
      else if (operation.current && error instanceof DeliveryFailed) { voice.idle(); setBusy(false); }   // the notice already offers Send again / Record again
      else if (operation.current) { voice.idle(); setError((error as Error).message); setBusy(false); if (!microphoneReady) setActive(false); }
    }
    finally { if (operation.current) running.current = false; operation.release(); }
  }
  async function recoverDelivery(action: 'send' | 'record' | 'replay') {
    if (running.current || !lesson.current) return;
    running.current = true; setBusy(true); setError('');
    const operation = voice.begin();
    try {
      if (action === 'replay') { await voice.replayQuestion(operation); return; }
      let value = lesson.current;
      if (action === 'record') {
        // Discard only after the learner explicitly chooses Record again; read before replacing any evidence.
        let saved = await boundedTask(signal => operation.apiWith<LessonCheckpoint>(`/lessons/${value.id}`, undefined, signal), 10000, operation.signal);
        if (saved.id !== value.id || saved.status !== 'active' || saved.phase !== value.phase
          || saved.exercise?.id !== value.exercise?.id) throw new DeliveryFailed('ambiguous', 0);
        if (await prepareNewRecording(operation, personalCapture.current ?? retained.current?.blob ?? null, saved.exercise?.id, saved)) {
          const fresh = await boundedTask(signal => operation.apiWith<LessonCheckpoint>(`/lessons/${saved.id}`, undefined, signal), 10000, operation.signal);
          if (fresh.id !== saved.id || fresh.status !== 'active' || fresh.phase !== saved.phase || fresh.exercise?.id !== saved.exercise?.id) throw new DeliveryFailed('ambiguous', 0);
          saved = fresh;
        }
        retained.current = null; personalCapture.current = null; clearUnsent(account.scope);
        setNotice({ kind: 'recordAgain', text: NOTICE_TEXT.recordAgain });
        setPrompt(saved.prompt ?? prompt);
        if (saved.phase === 'WELCOME' || saved.phase === 'REVIEW') {
          try { personalCapture.current = await voice.listen(operation); }
          catch (cause) {
            if (cause instanceof InterruptedRecording) { personalCapture.current = cause.blob; throw new DeliveryFailed('background', 0); }
            throw cause;
          }
          const sent = await deliverCheckin(operation, saved, personalCapture.current, () => { personalCapture.current = null; clearUnsent(account.scope); },
            saved.pending, { onState: reportDelivery('checkin', saved.id) });
          operation.assertCurrent(); personalCapture.current = null; value = sent.value;
        } else if (saved.phase === 'EXERCISE' && saved.exercise) {
          await captureAnswer(operation, saved.exercise); value = saved;
        } else throw new DeliveryFailed('ambiguous', 0);
      } else if (personalCapture.current && (value.phase === 'WELCOME' || value.phase === 'REVIEW')) {
        const sent = await deliverCheckin(operation, value, personalCapture.current, () => { personalCapture.current = null; clearUnsent(account.scope); },
          value.pending, { onState: reportDelivery('checkin', value.id) });
        operation.assertCurrent(); personalCapture.current = null; value = sent.value;
      } else if (retained.current && value.exercise?.id === retained.current.exercise) {
        await captureAnswer(operation, value.exercise);
      } else {
        throw new DeliveryFailed('ambiguous', 0);
      }
      operation.assertCurrent(); lesson.current = value; setNotice(null); clearUnsent(account.scope);
      await runWithLimitRecovery(operation, value, false);
    } catch (cause) {
      if (operation.current) {
        if (cause instanceof DeliveryFailed) setNotice(noticeFor({ kind: 'failed', reason: cause.reason, sends: cause.sends }));
        else setError((cause as Error).message);
      }
    } finally {
      if (operation.current) { voice.idle(); setBusy(false); running.current = false; }
      operation.release();
    }
  }
  function sendAgain() { void recoverDelivery('send'); }
  function recordAgain() { void recoverDelivery('record'); }
  function replayQuestion() { void recoverDelivery('replay'); }
  function continueAfterReload() {
    if (running.current) return;
    retained.current = null; personalCapture.current = null; clearUnsent(account.scope); setNotice(null); void start(false);
  }
  function leaveSetup() { topicDone.current?.(); topicDone.current = null; setTopicPlan(null); }
  function end() {
    // Ask now, before End's own request: Home must show the open lesson, and nothing new may be requested after End settles.
    void eligibility.refresh(true);
    reviewDone.current?.(); reviewDone.current = null; setReviewLesson(null); leaveSetup(); send({ type: 'home' });
    voice.stop(); running.current = false; retained.current = null; personalCapture.current = null; setTopic(null); setNotice(null); clearUnsent(account.scope);
    intent.end(); setActive(false); setBusy(false); setError(''); setPrompt(''); setDestination('Home');
    const value = lesson.current;
    if (value) {
      const operation = voice.begin();
      ending.current = operation.api<LessonCheckpoint>(`/lessons/${value.id}/end`, { method: 'POST' })
        .then(saved => { operation.assertCurrent(); lesson.current = saved; eligibility.announce(); })
        .catch(error => { if (operation.current) setError((error as Error).message); })
        .finally(() => { operation.release(); ending.current = null; });
    }
  }
  function cancelForOnboarding() {
    cancelPendingOperations();
    document.querySelectorAll('audio').forEach(player => player.pause());
    voice.stop(); running.current = false; retained.current = null; personalCapture.current = null; setNotice(null); clearUnsent(account.scope);
    intent.end(); lesson.current = null; current.current = null; ending.current = null; leaveSetup(); send({ type: 'home' });
    setActive(false); setBusy(false); setCompletion(null); setResolvedHomework(undefined); setTranscript(null); setEligibleExercise(null);
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
  /** Resolved = the new homework decision is persisted or explicitly skipped. Only then may the celebration start. */
  function homeworkResolved(id: string, assignment?: HomeworkAssignment) {
    if (phase.current.lessonId !== id || phase.current.phase !== 'HOMEWORK') return;
    setResolvedHomework(assignment ?? null);
    try { sessionStorage.setItem(storageKey + ':homework', id); } catch { /* Presentation memory only. */ }
    send({ type: 'homework_resolved', lessonId: id });
  }
  /** Return Home is the only way out of Success. */
  function returnHome() {
    voice.stop(); setHomeworkVoice(false); send({ type: 'home' }); setCompletion(null); setResolvedHomework(undefined); lesson.current = null; farewellDue.current = false; setNotice(null); clearUnsent(account.scope);
    void eligibility.refresh(true).then(() => eligibility.announce());
    try { sessionStorage.removeItem(storageKey); } catch { /* Optional presentation memory. */ }
    refreshQuietly();
  }
  /** Optional spoken farewell on the Success screen; never gates it, and never replays on restore. */
  async function farewell(id: string) {
    const operation = voice.begin();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      voice.thinking();
      const response = await operation.wait(() => fetchClosing(operation, id));
      if (!response.ok) throw new Error('Closing audio unavailable');
      const clip = await operation.wait(() => response.blob());
      await Promise.race([
        voice.speak(operation, URL.createObjectURL(clip)),
        new Promise<void>((_, reject) => { timer = setTimeout(() => reject(new Error('Closing interrupted')), 20000); }),
      ]);
    } catch { /* The saved lesson and final card always win over optional audio. */ }
    finally { clearTimeout(timer); if (operation.current) { operation.cancel(); voice.idle(); } operation.release(); }
  }
  const startFarewell = useEffectEvent((id: string) => { void farewell(id); });
  useEffect(() => { if (completion && lifecycle.phase === 'SUCCESS' && farewellDue.current && account.homeworkEnabled) { farewellDue.current = false; startFarewell(completion.id); } }, [completion, lifecycle.phase, account.homeworkEnabled]);
  // Warm the celebration while the learner is still deciding about homework.
  useEffect(() => { if (completion) prefetchCelebration(reducedMotion); }, [completion, reducedMotion]);
  useEffect(() => () => { reviewDone.current?.(); }, []);
  async function navigate(value: Destination) {
    setDestination(value); setTranscript(null); setError('');
  }
  if (!initialized && !active) return <AudliLoadingScreen message="Getting your listening space ready…" error={error} onRetry={() => void retry()}/>;
  if (active) return <><SessionShell windowNote={eligibility.snapshot?.value.enforced === true} backupVoice={voice.backupVoice} state={voice.state} activity={voice.activity} layout={reviewLesson ? 'interaction' : lifecycle.phase === 'TOPIC_SETUP' ? classroomLayout : undefined} topic={topic} startedAt={startedAt ?? undefined} onEnd={end} onReplay={voice.state === 'Speaking' ? voice.replay : undefined}>
    {lifecycle.phase === 'TOPIC_SETUP' && topicPlan && <LessonTopicCheckin initial={topicPlan} autoVoice voice={voice} onLayout={setClassroomLayout} onFinished={() => topicDone.current?.()}/>}
    {reviewLesson && <HomeworkReview key={reviewLesson} lessonId={reviewLesson} voice={voice} onFinished={() => { reviewDone.current?.(); reviewDone.current = null; }}/>}
    {voice.playbackBlocked && <div className="delivery-notice"><p role="status">Audli’s voice is ready. Tap below to listen.</p><button className="primary" onClick={voice.resumePlayback}>Tap to play Audli</button></div>}
    <p className="sr-only">{prompt}</p>{notice && <DeliveryNotice notice={notice} prompt={prompt} onSendAgain={sendAgain} onRecordAgain={recordAgain} onContinue={continueAfterReload} onReplay={replayQuestion} disabled={busy}/>}{error && <div role="alert" className="session-error">{error}<p>{prompt}</p><button className="secondary" onClick={retry}>Retry conversation</button>{(retained.current || personalCapture.current) && <button className="secondary" onClick={() => { retained.current = null; personalCapture.current = null; void retry(); }}>Record again</button>}</div>}
    {eligibleExercise && <details className="session-transcript"><summary>Transcript</summary>{transcript ? <p>{transcript.script}</p> : <button className="text-button" onClick={() => void reviewTranscript(eligibleExercise)}>Read transcript</button>}</details>}
  </SessionShell><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/></>;
  if (completion && lifecycle.phase === 'SUCCESS' && lifecycle.lessonId === completion.id) return <LessonSuccess lesson={completion} scope={account.scope} reducedMotion={reducedMotion} voice={voice} audio={voice.audio} onHome={returnHome} homework={resolvedHomework}/>;
  if (completion) return <><SessionShell completed label={voice.state === 'Speaking' ? 'Audli is speaking' : voice.state === 'Listening' ? 'Listening to you' : 'Wrapping up your lesson…'} showPresence={voice.state === 'Speaking' || voice.state === 'Listening' || voice.state === 'Thinking'} layout={classroomLayout} backupVoice={voice.backupVoice} state={voice.state === 'Idle' ? 'Success' : voice.state} activity={voice.activity} onEnd={returnHome}>
    <h1 className="wrapup-heading" ref={completionHeading} tabIndex={-1}>Lesson completed</h1>
    <p className="sr-only">{completion.encouragement}</p>
    <ListeningHomework inline lessonId={completion.id} autoVoice={homeworkVoice} sharedVoice={voice} onLayout={setClassroomLayout} onResolved={assignment => homeworkResolved(completion.id, assignment)}/>
  </SessionShell><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/></>;

  return <main className="product-shell"><header><strong className="wordmark">audli</strong><span className="language">TRAIN YOUR EARS</span></header><TutorVoiceNotice used={voice.backupVoice}/>
    {destination === 'Home' && <section className="home-view"><h1>{`Good to see you${profile?.name ? ', ' + profile.name : ''}.`}</h1><AudliMascot/>
      <PlanningInvitation open={() => setDestination('Plan')}/>{limitMessage && <p className="home-note" role="status">{limitMessage}</p>}
      <HomeLesson view={eligibility.view} nowMs={eligibility.nowMs} busy={busy} ready={!!profile} onStart={() => void startFromHome()} onReview={() => void navigate('Review')} onPlan={() => setDestination('Plan')}/>
    </section>}
    {destination === 'Review' && <ReviewHub key={account.scope}/>}
    {destination === 'Plan' && <AdaptivePlan/>}
    {destination === 'Settings' && <section className="shell-view"><h1>Settings</h1><dl><dt>Account</dt><dd>{account.email ?? 'Local development account'}{account.signOut && <button className="text-button" onClick={() => void account.signOut?.()}>Sign out</button>}</dd><AccountSettings disabled={busy}/><dd><OnboardingReset onConfirm={cancelForOnboarding} onRestart={onRestart}/></dd><dt>Voice & playback</dt><dd><TutorVoiceSettings/>Audli speaks automatically. Replay is available while Audli is speaking.</dd><dt>Accent difficulties</dt><dd><AccentSettings preferences={profile?.accent_preferences} onSaved={value => setProfile(current => current ? { ...current, accent_preferences: value } : current)}/></dd><dt>Accessibility</dt><dd><label className="toggle"><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)}/>Reduce motion</label><p>System reduced-motion preferences always apply.</p></dd><dt>Privacy</dt><dd>Your microphone recording is discarded after transcription. Your listening progress is saved to your account.</dd><dt>Help</dt><dd>Allow microphone access in your browser to speak with Audli.</dd></dl></section>}
    {busy && <p role="status">Loading your listening space…</p>}{error && <div role="alert" className="session-error">{error}<button className="secondary" onClick={retry}>Retry connection</button></div>}
    <BottomNavigation destination={destination} onNavigate={value => void navigate(value)}/><audio ref={voice.audio} className="session-audio" aria-label="Audli session audio"/>
  </main>;
}
