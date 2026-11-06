'use client';
import { useEffect, useRef } from 'react';
import styles from './listening.module.css';
import { useLearnerSettings } from '../../lib/use-settings.ts';
import { formatTime, type Cue, type Sentence, type Speaker } from '../../lib/listening-core.ts';

/** Sentence-level transcript. The active line follows playback; tapping a line replays from it. */
export function TranscriptViewer({ sentences, speakers, cues, activeIndex, playing, onReplay }: {
  sentences: Sentence[]; speakers: Speaker[]; cues: Cue[]; activeIndex: number | null; playing: boolean; onReplay: (startMs: number) => void;
}) {
  const { settings } = useLearnerSettings();
  const active = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!playing || activeIndex === null) return;
    const reduced = typeof window !== 'undefined' && (settings.reduceMotion || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
    active.current?.scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
  }, [activeIndex, playing, settings.reduceMotion]);
  const order = speakers.map(s => s.id);
  return <div className={`${settings.textSize === 'large' ? styles.tsLarge : ''} ${settings.highContrast ? styles.tsContrast : ''}`} data-text-size={settings.textSize} data-high-contrast={settings.highContrast}>
    <p className={styles.transcriptNote}>Tap any line to replay it</p>
    <ol className={styles.transcript} aria-label="Transcript">
      {sentences.map(sentence => {
        const cue = cues[sentence.index];
        const speaker = speakers.find(s => s.id === sentence.speaker);
        const isActive = sentence.index === activeIndex;
        const played = activeIndex !== null && sentence.index < activeIndex;
        return <li key={sentence.index}>
          <button type="button" ref={isActive ? active : undefined} aria-current={isActive ? 'true' : undefined}
            className={`${styles.line} ${isActive ? styles.lineActive : ''} ${played ? styles.linePlayed : ''}`}
            onClick={() => cue && onReplay(cue.start_ms)}>
            <span className={styles.who}>
              <span className={`${styles.dot} ${order.indexOf(sentence.speaker) % 2 ? styles.dotB : ''}`} aria-hidden="true" />
              {speaker ? `${speaker.name} · ${speaker.role}` : sentence.speaker}{cue ? ` · ${formatTime(cue.start_ms)}` : ''}
            </span>
            <span className={styles.said}>{sentence.text}</span>
          </button>
        </li>;
      })}
    </ol>
  </div>;
}
