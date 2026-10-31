'use client';
import { useEffect, useRef, type RefObject } from 'react';
import { RiveMascot } from './rive-mascot';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';
import type { HomeworkAssignment } from '../lib/homework';
import { ListeningHomework } from './listening-homework';
import type { useVoiceLifecycle } from '../lib/voice-lifecycle';

const focusCopy: Record<string, string> = { main_idea: 'Follow the speaker’s main point.', details: 'Catch the details that change the meaning.', vocabulary: 'Understand useful words in context.', inference: 'Hear what the speaker means beyond the words.' };

/** The one final screen: Pip's celebration and the lesson statistics together. Presentation only: the lesson and the
 *  homework decision are already saved, nothing here advances the lifecycle, and it stays until Return Home. */
export function LessonSuccess({ lesson, scope, reducedMotion, voice, audio, onHome, homework }: {
  homework?: HomeworkAssignment | null;
  lesson: LessonCheckpoint; scope: string; reducedMotion: boolean;
  voice: ReturnType<typeof useVoiceLifecycle>; audio: RefObject<HTMLAudioElement | null>; onHome: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { window.scrollTo(0, 0); heading.current?.focus({ preventScroll: true }); }, []);
  return <main className="lesson-celebration lesson-success" aria-labelledby="celebration-title">
    <header><strong className="wordmark">audli</strong></header>
    <div className="celebration-content">
      <RiveMascot completionId={lesson.id} scope={scope} state="celebrate" reducedMotion={reducedMotion}/>
      <h1 id="celebration-title" ref={heading} tabIndex={-1}>Great job!</h1>
      <p>You’ve finished today’s listening lesson.</p>
      <div className="completion-summary">
        <p>{lesson.exercises_completed} exercise{lesson.exercises_completed === 1 ? '' : 's'} completed · {focusCopy[lesson.focus]}</p>
        {lesson.strength && <p>A demonstrated strength: {lesson.strength}</p>}
        {lesson.improvement_focus && <p>Next listening focus: {lesson.improvement_focus}</p>}
      </div>
      {/* The saved decision (assigned video, declined, unavailable), read-only: it was settled in wrap-up. */}
      <ListeningHomework inline finished lessonId={lesson.id} resolvedAssignment={homework} sharedVoice={voice}/>
      <button className="primary" onClick={onHome}>Return Home</button>
    </div>
    <audio ref={audio} className="session-audio" aria-label="Audli session audio"/>
  </main>;
}
