'use client';
import { useEffect, useRef, useState } from 'react';
import { authenticatedFetch, currentAuthRevision, LessonLifetime, type LessonOperation } from './api';
import { captureRecording } from './recording';
import { turnDetector } from './hands-free';
import { measureTurn, turnTiming, speechLatency, measuredAudioBody } from './turn-timing';
import { PreparedAudio } from './prepared-audio';
import type { LessonCheckpoint } from './lesson-lifecycle';
import type { MascotState } from '../components/audli-mascot';
type AcknowledgmentCue = 'assessment' | 'followup' | 'reflection';
const acknowledgmentText = { assessment: 'Okay, I heard you.', followup: 'Okay, I’ve heard that.', reflection: 'Let me keep that in mind.' };
type CoachingCheckpoint = { lesson_id: string; lesson_revision: number };

/** One account-bound lifetime owns playback, turn detection and all continuations. */
export function useVoiceLifecycle() {
  const [state, setState] = useState<MascotState>('Idle');
  const [activity, setActivity] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const owner = useRef<LessonLifetime | null>(null);
  const auth = useRef(currentAuthRevision());
  const latency = useRef(speechLatency());
  const acknowledgment = useRef<Partial<Record<AcknowledgmentCue, Blob>>>({});
  const acknowledgedTurns = useRef(new Set<string>());
  const coaching = useRef(new WeakMap<LessonOperation, Map<string, { audio: PreparedAudio; background: boolean }>>());
  const disposeRecording = useRef<(() => void) | null>(null);
  const playback = useRef<{ operation: LessonOperation; failed: () => void } | null>(null);
  useEffect(() => {
    const lifetime = new LessonLifetime(); owner.current = lifetime;
    // End may replace the lifetime; unmount must close its latest resources too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => { lifetime.close(); owner.current?.close(); disposeRecording.current?.(); audio.current?.pause(); };
  }, []);
  function begin() {
    if (auth.current !== currentAuthRevision() || !owner.current) throw new DOMException('Account changed.', 'AbortError');
    return owner.current.begin();
  }
  function stop() {
    owner.current?.close(); disposeRecording.current?.(); audio.current?.pause();
    if (auth.current === currentAuthRevision()) owner.current = new LessonLifetime();
    latency.current.reset(); setState('Idle'); setActivity(false);
  }
  async function speak(operation: LessonOperation, url: string, kind: 'meaningful' | 'acknowledgment' | 'retry' = 'meaningful') {
    operation.assertCurrent(() => URL.revokeObjectURL(url));
    const element = audio.current;
    if (!element) { URL.revokeObjectURL(url); throw new Error('Voice playback is unavailable.'); }
    try {
      await operation.wait(() => new Promise<void>((resolve, reject) => {
        let settled = false;
        let playbackMeasured = false;
        let playbackStartedAt: number | null = null;
        const cleanup = () => { settled = true; element.removeEventListener('ended', ended); element.removeEventListener('error', failed); element.removeEventListener('playing', playing); element.removeEventListener('pause', paused); operation.signal.removeEventListener('abort', cancelled); if (playback.current?.failed === failed) playback.current = null; };
        const playing = () => {
          if (!operation.current) return;
          setActivity(true);
          if (playbackMeasured) return;
          playbackMeasured = true;
          playbackStartedAt = performance.now();
          const sample = latency.current.playback(kind);
          if (sample) turnTiming(kind === 'meaningful' ? 'turn_to_playback' : kind === 'retry' ? 'retry_playback' : 'acknowledgment_playback', performance.now() - sample.elapsedMs);
        };
        const paused = () => { if (operation.current) setActivity(false); };
        const ended = () => { if (kind === 'acknowledgment' && playbackStartedAt != null) turnTiming('acknowledgment_duration', playbackStartedAt); cleanup(); resolve(); };
        const failed = () => { if (settled) return; cleanup(); reject(new Error('Audli’s voice could not play. Retry audio.')); };
        const cancelled = () => { cleanup(); element.pause(); reject(new DOMException('Playback cancelled.', 'AbortError')); };
        element.addEventListener('ended', ended, { once: true }); element.addEventListener('error', failed, { once: true });
        element.addEventListener('playing', playing); element.addEventListener('pause', paused); playback.current = { operation, failed };
        operation.signal.addEventListener('abort', cancelled, { once: true });
        element.src = url;
        void operation.wait(() => measureTurn('playback_start', () => element.play()), () => element.pause()).then(() => {
          if (!settled && operation.current) {
            setState('Speaking'); setActivity(true);
          }
        }).catch(error => { if (!settled) { cleanup(); reject(error); } });
      }));
    } finally {
      element.pause(); element.removeAttribute('src'); URL.revokeObjectURL(url);
      if (operation.current) setActivity(false);
    }
  }
  function coachingPreparation(operation: LessonOperation, exerciseId: string, cueId: string | null, checkpoint?: CoachingCheckpoint, background = false) {
    operation.assertCurrent();
    let entries = coaching.current.get(operation);
    if (!entries) { entries = new Map(); coaching.current.set(operation, entries); }
    const key = JSON.stringify([exerciseId, cueId, checkpoint]);
    const existing = entries.get(key);
    if (existing) { existing.background ||= background; return existing; }
    const entry = { background, audio: new PreparedAudio(operation, () => measureTurn('tts_readiness', async () => {
      const started = performance.now();
      const response = await operation.wait(() => authenticatedFetch(`/api/exercises/${encodeURIComponent(exerciseId)}/coach-audio`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'audio/*' },
        body: JSON.stringify({ cue_id: cueId, ...checkpoint }), signal: operation.signal,
      }, operation));
      if (!response.ok) {
        const data: unknown = await operation.wait(() => response.json().catch(() => null));
        operation.assertCurrent();
        throw new Error(data && typeof data === 'object' && 'detail' in data && typeof data.detail === 'string'
          ? data.detail : 'Audli’s voice is unavailable. Please retry audio.');
      }
      const body = await operation.wait(() => measuredAudioBody(response));
      operation.assertCurrent(); turnTiming('coaching_readiness', started); return body;
    })) };
    entries.set(key, entry);
    return entry;
  }
  function prepareCoachSpeech(operation: LessonOperation, exerciseId: string, cueId: string | null, checkpoint?: CoachingCheckpoint) {
    coachingPreparation(operation, exerciseId, cueId, checkpoint, true);
  }
  async function coachSpeech(operation: LessonOperation, exerciseId: string, cueId: string | null, checkpoint?: CoachingCheckpoint) {
    const started = performance.now();
    const entry = coachingPreparation(operation, exerciseId, cueId, checkpoint);
    await entry.audio.play(async body => {
      if (entry.background) turnTiming('coaching_wait_after_ack', started);
      await speak(operation, URL.createObjectURL(body));
    }, async () => {
      if (!entry.background || !checkpoint) return;
      // Ready audio may have waited through acknowledgment/replay or another tab's End.
      const latest = await operation.api<LessonCheckpoint>(`/lessons/${encodeURIComponent(checkpoint.lesson_id)}`);
      if (latest.status !== 'active' || latest.phase !== 'EXERCISE' || latest.exercise?.id !== exerciseId || latest.revision !== checkpoint.lesson_revision)
        throw new Error('Your lesson changed. Retry conversation to restore your saved progress.');
    });
  }
  async function lessonSpeech(operation: LessonOperation, lessonId: string, revision: number, action = 'audio') {
    const body = await measureTurn('tts_readiness', async () => {
      const response = await operation.wait(() => authenticatedFetch(`/api/lessons/${encodeURIComponent(lessonId)}/${action}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision }), signal: operation.signal,
      }, operation));
      if (!response.ok) throw new Error('Audli’s lesson voice is unavailable. Retry the conversation; your progress is saved.');
      return operation.wait(() => measuredAudioBody(response));
    });
    operation.assertCurrent();
    await speak(operation, URL.createObjectURL(body));
  }
  async function prepareAcknowledgment(operation: LessonOperation, cue: AcknowledgmentCue = 'assessment') {
    if (acknowledgment.current[cue]) return;
    try {
      const response = await operation.wait(() => authenticatedFetch('/api/recognition/acknowledgment-audio', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cue }), signal: operation.signal }, operation));
      if (response.ok) acknowledgment.current[cue] = await operation.wait(() => response.blob());
    } catch { operation.assertCurrent(); } // Optional cue never blocks authoritative assessment.
  }
  async function acknowledged<T>(operation: LessonOperation, key: string, work: () => Promise<T>, cue: AcknowledgmentCue, onCue: (text: string) => void, onSaved?: (value: T) => void): Promise<T> {
    let settled = false;
    let acknowledgmentPlaying = false;
    const task = (async () => {
      try {
        const value = await work(); settled = true; operation.assertCurrent();
        if (acknowledgmentPlaying) onSaved?.(value);
        return { ok: true as const, value };
      } catch (error) { settled = true; return { ok: false as const, error }; }
    })();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await operation.wait(() => Promise.race([task, new Promise<void>(resolve => { timer = setTimeout(resolve, 1200); })]));
    } finally { if (timer) clearTimeout(timer); }
    let alreadyPlayed = acknowledgedTurns.current.has(key);
    try { alreadyPlayed ||= sessionStorage.getItem('audli-cue:' + key) === 'played'; } catch { /* Optional cue memory. */ }
    if (!settled && !alreadyPlayed) {
      // Optional cue readiness must never hold up completed assessment/coaching.
      await operation.wait(() => Promise.race([task, prepareAcknowledgment(operation, cue)]));
      const blob = acknowledgment.current[cue];
      if (!settled && blob) {
        operation.assertCurrent(); acknowledgedTurns.current.add(key);
        try { sessionStorage.setItem('audli-cue:' + key, 'played'); } catch { /* Optional cue memory. */ }
        onCue(acknowledgmentText[cue]);
        acknowledgmentPlaying = true;
        try { await speak(operation, URL.createObjectURL(blob), 'acknowledgment'); }
        catch { operation.assertCurrent(); }
        finally { acknowledgmentPlaying = false; }
        if (operation.current) setState('Thinking');
      }
    }
    // Attach the rejection handler immediately so a fast failure cannot go unhandled.
    const result = await task; operation.assertCurrent();
    if (!result.ok) throw result.error;
    return result.value;
  }
  async function retrySpeech(operation: LessonOperation) {
    setState('Retry');
    const response = await operation.wait(() => authenticatedFetch('/api/recognition/retry-audio', { method: 'POST', signal: operation.signal }, operation));
    if (!response.ok) throw new Error('Audli’s retry voice is unavailable. Please retry audio.');
    const body = await operation.wait(() => response.blob());
    operation.assertCurrent();
    await speak(operation, URL.createObjectURL(body), 'retry');
  }
  async function listen(operation: LessonOperation): Promise<Blob> {
    operation.assertCurrent(); audio.current?.pause();
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('Recording requires HTTPS and a supported browser.');
    const input = await operation.wait(() => navigator.mediaDevices.getUserMedia({ audio: true }), stream => stream.getTracks().forEach(track => track.stop()));
    operation.assertCurrent(() => input.getTracks().forEach(track => track.stop()));
    try {
      return await operation.wait(() => new Promise<Blob>((resolve, reject) => {
        let context: AudioContext | undefined, source: MediaStreamAudioSourceNode | undefined;
        let interval: ReturnType<typeof setInterval> | undefined;
        let capture: ReturnType<typeof captureRecording> | undefined;
        let disposed = false, stoppedAt: number | null = null, lastVoiceAt = performance.now();
        const cleanup = () => {
          if (disposed) return; disposed = true;
          if (interval) clearInterval(interval);
          source?.disconnect(); void context?.close().catch(() => {});
          capture?.dispose(); input.getTracks().forEach(track => track.stop());
          operation.signal.removeEventListener('abort', cancelled); disposeRecording.current = null;
        };
        const cancelled = () => { cleanup(); reject(new DOMException('Recording cancelled.', 'AbortError')); };
        disposeRecording.current = cancelled; operation.signal.addEventListener('abort', cancelled, { once: true });
        try {
          if (typeof AudioContext === 'undefined') throw new Error('Automatic turn detection is unavailable in this browser. Please use a supported browser.');
          context = new AudioContext();
          const analyser = context.createAnalyser(); analyser.fftSize = 1024;
          source = context.createMediaStreamSource(input); source.connect(analyser);
          const samples = new Float32Array(analyser.fftSize);
          capture = captureRecording(input, blob => { if (stoppedAt != null) turnTiming('recording_finalization', stoppedAt); cleanup(); resolve(blob); }, message => { cleanup(); reject(new Error(message)); });
          const detect = turnDetector(Date.now());
          setState('Listening'); setActivity(false);
          void context.resume().catch(() => { cleanup(); reject(new Error('Microphone turn detection could not start. Please retry.')); });
          interval = setInterval(() => {
            if (!operation.current) { cancelled(); return; }
            analyser.getFloatTimeDomainData(samples);
            const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
            const turn = detect(Date.now(), rms); setActivity(turn.activity);
            if (turn.activity) lastVoiceAt = performance.now();
            if (turn.end) {
              latency.current.begin(lastVoiceAt); turnTiming('silence_detection', lastVoiceAt); stoppedAt = performance.now();
              setState('Thinking'); setActivity(false);
              if (interval) clearInterval(interval); capture?.stop();
            }
          }, 50);
        } catch (error) { cleanup(); reject(error); }
      }));
    } catch (error) { input.getTracks().forEach(track => track.stop()); throw error; }
    finally { if (operation.current) { setState('Thinking'); setActivity(false); } }
  }
  function replay() {
    const element = audio.current, current = playback.current;
    if (!current?.operation.current || !element?.src) return;
    element.currentTime = 0;
    if (element.paused) void current.operation.wait(() => element.play(), () => element.pause()).catch(() => { if (current.operation.current) current.failed(); });
  }
  return { state, activity, audio, begin, stop, speak, prepareCoachSpeech, coachSpeech, lessonSpeech, prepareAcknowledgment, acknowledged, retrySpeech, listen, replay, thinking: () => setState('Thinking') };
}
