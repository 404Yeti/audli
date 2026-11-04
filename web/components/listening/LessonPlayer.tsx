'use client';
import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from 'react';
import styles from './listening.module.css';
import { signedAudioUrl } from '../../lib/listening.ts';
import { SKIP_MS, activeSentence, clampMs, formatTime, nextSpeed, speedLabel, waveformBars, type Cue, type Speaker } from '../../lib/listening-core.ts';

export type PlayerHandle = { playFrom: (ms: number) => void };

const Icon = ({ playing }: { playing: boolean }) => playing
  ? <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
  : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.9l10-6.5a1 1 0 0 0 0-1.8l-10-6.5A1 1 0 0 0 8 5.5z" /></svg>;

/** One audio engine. `compact` is the docked variant used beside the question; the element persists across both. */
export function LessonPlayer({ attemptId, durationMs, cues, speakers, compact, onActive, onPlaying, handle }: {
  attemptId: string; durationMs: number; cues: Cue[]; speakers: Speaker[]; compact: boolean;
  onActive: (index: number | null) => void; onPlaying: (playing: boolean) => void; handle?: Ref<PlayerHandle>;
}) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [ms, setMs] = useState(0);
  const [speed, setSpeed] = useState<number>(1);
  const resume = useRef<{ at: number; play: boolean } | null>(null);
  const refreshed = useRef(false);
  const lastActive = useRef<number | null | undefined>(undefined);
  const bars = useMemo(() => waveformBars(48), []);

  const load = useCallback(async () => {
    try { setSrc((await signedAudioUrl(attemptId)).url); setFailed(false); } catch { setFailed(true); }
  }, [attemptId]);
  useEffect(() => {
    let live = true;
    signedAudioUrl(attemptId).then(info => { if (live) setSrc(info.url); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [attemptId]);

  // Follow playback. requestAnimationFrame only runs while playing; the active line is reported only when it changes.
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => {
      const el = audio.current;
      if (el) {
        const now = el.currentTime * 1000;
        setMs(now);
        const index = activeSentence(cues, now);
        if (index !== lastActive.current) { lastActive.current = index; onActive(index); }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, cues, onActive]);

  const seek = useCallback((to: number) => {
    const el = audio.current;
    if (!el) return;
    const value = clampMs(to, durationMs);
    el.currentTime = value / 1000;
    setMs(value);
    const index = activeSentence(cues, value);
    lastActive.current = index; onActive(index);
  }, [cues, durationMs, onActive]);

  const play = useCallback(() => {
    const el = audio.current;
    if (!el) return;
    el.playbackRate = speed;
    el.play().catch(() => { setPlaying(false); onPlaying(false); });   // a browser may refuse; the button stays usable
  }, [speed, onPlaying]);

  useImperativeHandle(handle, () => ({ playFrom: (start: number) => { seek(start); play(); } }), [seek, play]);

  const toggle = () => { if (playing) audio.current?.pause(); else play(); };
  const cycleSpeed = () => { const next = nextSpeed(speed); setSpeed(next); if (audio.current) audio.current.playbackRate = next; };

  // A signed link expires. If the element errors (e.g. after a long pause), fetch a fresh link once and carry on from the same spot.
  const onError = async () => {
    const el = audio.current;
    if (refreshed.current || !el) { setFailed(true); return; }
    refreshed.current = true;
    resume.current = { at: el.currentTime, play: !el.paused };
    await load();
  };
  const onLoaded = () => {
    const el = audio.current;
    if (!el) return;
    el.playbackRate = speed;            // Safari resets the rate when the source changes
    if (resume.current) {
      el.currentTime = resume.current.at;
      if (resume.current.play) play();
      resume.current = null;
      refreshed.current = false;
    }
  };

  const progress = durationMs ? ms / durationMs : 0;
  const names = speakers.map((s, i) => <span key={s.id} className={`${styles.tag} ${i % 2 ? styles.voiceB : styles.voiceA}`}>{s.name} · {s.role}</span>);
  return <section className={`${styles.player} ${compact ? styles.playerCompact : ''}`} aria-label="Audio player">
    {src ? <audio ref={audio} src={src} preload="metadata" onLoadedMetadata={onLoaded} onError={onError}
      onPlay={() => { setPlaying(true); onPlaying(true); }} onPause={() => { setPlaying(false); onPlaying(false); }}
      onEnded={() => { setPlaying(false); onPlaying(false); }} /> : null}
    {failed ? <p className={styles.playerMsg} role="alert">The audio couldn’t load.
      <button type="button" className={styles.linkBtn} onClick={() => { refreshed.current = false; void load(); }}>Try again</button></p> : null}
    <div className={styles.scrubWrap}>
      <div className={styles.bars} aria-hidden="true">
        {bars.map((h, i) => <span key={i} className={`${styles.bar} ${i / bars.length < progress ? styles.barPlayed : ''}`} style={{ height: `${h}%` }} />)}
      </div>
      <input className={styles.scrub} type="range" min={0} max={durationMs} step={100} value={Math.round(ms)}
        aria-label="Seek" aria-valuetext={`${formatTime(ms)} of ${formatTime(durationMs)}`} disabled={!src}
        onChange={event => seek(Number(event.target.value))} />
    </div>
    <div className={styles.times}><span>{formatTime(ms)}</span><span>{formatTime(durationMs)}</span></div>
    <div className={styles.controls}>
      <button type="button" className={styles.round} onClick={cycleSpeed} aria-label={`Playback speed ${speedLabel(speed)}. Change speed`}>{speedLabel(speed)}</button>
      <button type="button" className={`${styles.round} ${styles.skip}`} onClick={() => seek(ms - SKIP_MS)} disabled={!src} aria-label="Back 10 seconds">↺<small>10</small></button>
      <button type="button" className={styles.play} onClick={toggle} disabled={!src} aria-label={playing ? 'Pause' : 'Play'}><Icon playing={playing} /></button>
      <button type="button" className={`${styles.round} ${styles.skip}`} onClick={() => seek(ms + SKIP_MS)} disabled={!src} aria-label="Forward 10 seconds">↻<small>10</small></button>
      <button type="button" className={styles.round} onClick={() => { seek(0); play(); }} disabled={!src} aria-label="Replay from the start">⟲</button>
    </div>
    {!compact ? <div className={styles.voices}>Voices: {names}</div> : null}
  </section>;
}
