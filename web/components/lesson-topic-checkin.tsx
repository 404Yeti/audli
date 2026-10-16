'use client';
import { useEffect, useEffectEvent, useRef, useState, type FormEvent } from 'react';
import { authenticatedFetch, type LessonOperation } from '../lib/api';
import type { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { recordingUpload } from '../lib/recording';
import type { Plan } from '../lib/planner';

type Voice = ReturnType<typeof useVoiceLifecycle>;
export function LessonTopicCheckin({ lessonId, autoVoice, voice, onFinished }: { lessonId: string; autoVoice: boolean; voice: Voice; onFinished: () => void }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [finished, setFinished] = useState(false);
  const active = useRef<LessonOperation | null>(null);
  const submittingText = useRef(false);
  function done() { setFinished(true); onFinished(); }
  async function answer(saved: Plan, typed?: string) {
    const operation = voice.begin(); active.current = operation; setBusy(true); setError('');
    try {
      if (typed === undefined) {
        const response = await operation.wait(() => authenticatedFetch('/api/plan/checkin/audio', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: saved.revision }), signal: operation.signal }, operation));
        if (!response.ok) throw new Error('Use a typed topic or keep your topics. Your lesson is saved.');
        const audio = await operation.wait(() => response.blob());
        await voice.speak(operation, URL.createObjectURL(audio));
        const recording = await voice.listen(operation);
        const form = recordingUpload(recording); form.append('revision', String(saved.revision));
        saved = await operation.api<Plan>('/plan/checkin/attempts', { method: 'POST', body: form });
        operation.assertCurrent(); setPlan(saved);
      }
      const result = await operation.api<Plan>(typed === undefined ? '/plan/checkin/answer' : '/plan/checkin/text', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: saved.revision, ...(typed !== undefined ? { text: typed } : {}) }),
      });
      operation.assertCurrent(); setPlan(result); done();
    } catch (cause) { if (operation.current) setError((cause as Error).message); }
    finally { if (operation.current) setBusy(false); if (active.current === operation) active.current = null; operation.release(); }
  }
  const offer = useEffectEvent(async () => {
    if (!autoVoice) { done(); return; }
    const operation = voice.begin(); active.current = operation;
    try {
      const result = await operation.api<{ offered: boolean; plan: Plan | null }>(`/lessons/${lessonId}/topic-checkin`, { method: 'POST' });
      operation.assertCurrent();
      if (!result.offered || !result.plan) { done(); return; }
      setPlan(result.plan); operation.release(); active.current = null;
      await answer(result.plan);
    } catch { if (operation.current) done(); } // Optional planning never gates a saved lesson.
    finally { if (active.current === operation) active.current = null; operation.release(); }
  });
  useEffect(() => {
    const timer = setTimeout(() => void offer(), 0);
    return () => { clearTimeout(timer); active.current?.cancel(); };
  }, [lessonId]);
  function typed(event: FormEvent) {
    event.preventDefault();
    if (!plan || !reply.trim() || submittingText.current) return;
    if (active.current && busy && !['Listening', 'Speaking'].includes(voice.state)) return;
    voice.stop(); active.current = null;
    submittingText.current = true;
    void answer(plan, reply.trim()).finally(() => { submittingText.current = false; });
  }
  if (!plan || finished) return null;
  return <section className="topic-checkin" aria-label="Listening topic check-in">
    <p>{plan.checkin.prompt}</p>
    <form className="wrapup-reply" onSubmit={typed}><label htmlFor="topic-reply">Your topic preference</label>
      <input id="topic-reply" value={reply} maxLength={200} onChange={event => setReply(event.target.value)} placeholder="Keep them, or name a topic"/>
      <div className="homework-actions"><button className="primary" disabled={!reply.trim()}>Send</button><button className="secondary" type="button" disabled={busy} onClick={() => void answer(plan)}>Speak</button>
        <button className="text-button" type="button" onClick={() => { voice.stop(); active.current = null; void answer(plan, 'keep them'); }}>Keep my topics</button></div>
    </form>
    {error && <div role="alert"><p>{error}</p><button className="text-button" onClick={() => { voice.stop(); active.current = null; done(); }}>Continue lesson wrap-up</button></div>}
  </section>;
}
