'use client';
import { useEffect, useEffectEvent, useRef, useState, type FormEvent } from 'react';
import { authenticatedFetch, type LessonOperation } from '../lib/api';
import type { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { recordingUpload } from '../lib/recording';

type Review = { revision: number; phase: string; prompt: string };
type Voice = ReturnType<typeof useVoiceLifecycle>;
export function HomeworkReview({ lessonId, voice, onFinished }: { lessonId: string; voice: Voice; onFinished: () => void }) {
  const [review, setReview] = useState<Review | null>(null);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const active = useRef<LessonOperation | null>(null);
  const submitting = useRef(false);
  const done = useRef(false);
  function finish() { if (!done.current) { done.current = true; onFinished(); } }
  const path = `/lessons/${lessonId}/homework-review`;
  async function request<T>(operation: LessonOperation, suffix = '', body?: object | FormData): Promise<T> {
    const response = await operation.wait(() => authenticatedFetch(`/api${path}${suffix}`, {
      method: 'POST', signal: AbortSignal.any([operation.signal, AbortSignal.timeout(30000)]),
      ...(body instanceof FormData ? { body } : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }),
    }, operation));
    if (!response.ok) throw new Error('Optional review is unavailable.');
    return operation.wait(() => response.json() as Promise<T>);
  }
  async function run(initial?: Review, typed?: string, skip = false) {
    const operation = voice.begin(); active.current = operation; setError(''); voice.thinking();
    try {
      let saved = initial ?? await request<Review | null>(operation);
      if (!saved) { finish(); return; }
      if (typed !== undefined || skip) saved = await request<Review>(operation, skip ? '/skip' : '/text', { revision: saved.revision, ...(skip ? {} : { text: typed }) });
      submitting.current = false;
      while (operation.current && saved) {
        setReview(saved); setText('');
        if (saved.phase === 'done') { finish(); return; }
        const response = await operation.wait(() => authenticatedFetch(`/api${path}/audio`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: saved!.revision }), signal: AbortSignal.any([operation.signal, AbortSignal.timeout(20000)]),
        }, operation));
        if (!response.ok) throw new Error('We can continue today’s lesson.');
        const clip = await operation.wait(() => response.blob());
        await voice.speak(operation, URL.createObjectURL(clip));
        const recording = await voice.listen(operation);
        const form = recordingUpload(recording); form.append('revision', String(saved.revision));
        saved = await request<Review>(operation, '/attempts', form);
      }
    } catch { if (operation.current) { setError('We can leave this optional review for now.'); voice.idle(); finish(); } }
    finally { if (active.current === operation) { active.current = null; submitting.current = false; } operation.release(); }
  }
  const start = useEffectEvent(() => void run());
  useEffect(() => {
    const timer = setTimeout(start, 0);
    return () => { clearTimeout(timer); active.current?.cancel(); };
  }, [lessonId]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!review || !text.trim() || submitting.current || !['Speaking','Listening','Idle'].includes(voice.state)) return;
    active.current?.cancel(); active.current = null;
    submitting.current = true;
    void run(review, text.trim());
  }
  function skip() {
    active.current?.cancel(); active.current = null;
    if (review) void run(review, undefined, true); else finish();
  }
  if (!review || review.phase === 'done') return null;
  return <section className="homework-review" aria-label="Previous homework">
    <p>{review.prompt}</p>
    <form className="onboarding-name inline-reply" onSubmit={submit}>
      <label htmlFor="homework-review-reply">Your reply</label><div>
        <input id="homework-review-reply" value={text} maxLength={2000} onChange={event => setText(event.target.value)} placeholder="Or type your reply"/>
        <button className="primary" disabled={!text.trim() || !['Speaking','Listening','Idle'].includes(voice.state)}>Send</button>
      </div>
    </form>
    <button className="text-button" onClick={skip}>Skip review</button>
    {error && <p role="alert">{error}</p>}
  </section>;
}
