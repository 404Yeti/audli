'use client';
import { useEffect, useRef, useState } from 'react';
import { authenticatedFetch, currentAuthRevision, LessonLifetime, type LessonOperation } from './api';
import { captureRecording } from './recording';
import { turnDetector } from './hands-free';
import type { MascotState } from '../components/audli-mascot';

/** One account-bound lifetime owns playback, turn detection and all continuations. */
export function useVoiceLifecycle() {
  const [state, setState] = useState<MascotState>('Idle');
  const [activity, setActivity] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const owner = useRef<LessonLifetime | null>(null);
  const auth = useRef(currentAuthRevision());
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
    setState('Idle'); setActivity(false);
  }
  async function speak(operation: LessonOperation, url: string) {
    operation.assertCurrent(() => URL.revokeObjectURL(url));
    const element = audio.current;
    if (!element) { URL.revokeObjectURL(url); throw new Error('Voice playback is unavailable.'); }
    try {
      await operation.wait(() => new Promise<void>((resolve, reject) => {
        let settled = false;
        const cleanup = () => { settled = true; element.removeEventListener('ended', ended); element.removeEventListener('error', failed); element.removeEventListener('play', playing); element.removeEventListener('pause', paused); operation.signal.removeEventListener('abort', cancelled); if (playback.current?.failed === failed) playback.current = null; };
        const playing = () => { if (operation.current) setActivity(true); };
        const paused = () => { if (operation.current) setActivity(false); };
        const ended = () => { cleanup(); resolve(); };
        const failed = () => { if (settled) return; cleanup(); reject(new Error('Audli’s voice could not play. Retry audio.')); };
        const cancelled = () => { cleanup(); element.pause(); reject(new DOMException('Playback cancelled.', 'AbortError')); };
        element.addEventListener('ended', ended, { once: true }); element.addEventListener('error', failed, { once: true });
        element.addEventListener('play', playing); element.addEventListener('pause', paused); playback.current = { operation, failed };
        operation.signal.addEventListener('abort', cancelled, { once: true });
        element.src = url;
        void operation.wait(() => element.play(), () => element.pause()).then(() => {
          if (!settled && operation.current) { setState('Speaking'); setActivity(true); }
        }).catch(error => { if (!settled) { cleanup(); reject(error); } });
      }));
    } finally {
      element.pause(); element.removeAttribute('src'); URL.revokeObjectURL(url);
      if (operation.current) setActivity(false);
    }
  }
  async function retrySpeech(operation: LessonOperation) {
    setState('Retry');
    const response = await operation.wait(() => authenticatedFetch('/api/recognition/retry-audio', { method: 'POST', signal: operation.signal }, operation));
    if (!response.ok) throw new Error('Audli’s retry voice is unavailable. Please retry audio.');
    const body = await operation.wait(() => response.blob());
    operation.assertCurrent();
    await speak(operation, URL.createObjectURL(body));
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
        let disposed = false;
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
          capture = captureRecording(input, blob => { cleanup(); resolve(blob); }, message => { cleanup(); reject(new Error(message)); });
          const detect = turnDetector(Date.now());
          setState('Listening'); setActivity(false);
          void context.resume().catch(() => { cleanup(); reject(new Error('Microphone turn detection could not start. Please retry.')); });
          interval = setInterval(() => {
            if (!operation.current) { cancelled(); return; }
            analyser.getFloatTimeDomainData(samples);
            const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
            const turn = detect(Date.now(), rms); setActivity(turn.activity);
            if (turn.end) { if (interval) clearInterval(interval); capture?.stop(); }
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
  return { state, activity, audio, begin, stop, speak, retrySpeech, listen, replay, thinking: () => setState('Thinking') };
}
