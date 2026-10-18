'use client';
import { useEffect, useEffectEvent, useRef, useState, type FormEvent } from 'react';
import { authenticatedFetch, type LessonOperation } from '../lib/api';
import type { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { recordingUpload } from '../lib/recording';
import type { ClassroomLayout } from './product-shell';
import type { Plan } from '../lib/planner';

type Voice = ReturnType<typeof useVoiceLifecycle>;
/** Setup-only: rendered by the lifecycle's TOPIC_SETUP phase, before any lesson exists. */
export function LessonTopicCheckin({ initial, autoVoice, voice, onFinished, onLayout }: { initial: Plan; autoVoice: boolean; voice: Voice; onFinished: () => void; onLayout?: (mode: ClassroomLayout) => void }) {
  const [plan, setPlan] = useState<Plan>(initial);
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [finished, setFinished] = useState(false);
  useEffect(() => { onLayout?.(plan && !finished ? 'interaction' : 'focus'); }, [plan, finished, busy, onLayout]);
  const active = useRef<LessonOperation | null>(null);
  const submittingText = useRef(false);
  const completed = useRef(false);
  // Cancel only this prompt's operation: the lifecycle's own setup operation outlives it.
  function interrupt() { active.current?.cancel(); active.current = null; voice.idle(); }
  function done() { if (completed.current) return; completed.current = true; setFinished(true); onFinished(); }
  async function answer(saved: Plan, typed?: string) {
    if (completed.current || active.current) return;
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
    } catch (cause) { if (operation.current) { voice.idle(); setError((cause as Error).message); } }
    finally { if (operation.current) setBusy(false); if (active.current === operation) active.current = null; operation.release(); }
  }
  const begin = useEffectEvent(() => { if (autoVoice) void answer(initial); });
  useEffect(() => {
    const timer = setTimeout(begin, 0);
    return () => { clearTimeout(timer); active.current?.cancel(); };
  }, []);
  function typed(event: FormEvent) {
    event.preventDefault();
    if (!reply.trim() || submittingText.current) return;
    if (active.current && busy && !['Listening', 'Speaking'].includes(voice.state)) return;
    interrupt();
    submittingText.current = true;
    void answer(plan, reply.trim()).finally(() => { submittingText.current = false; });
  }
  function keepTopics() {
    if (completed.current || submittingText.current) return;
    if (active.current && busy && !['Listening', 'Speaking'].includes(voice.state)) return;
    submittingText.current = true;
    interrupt();
    void answer(plan, 'keep them').finally(() => { submittingText.current = false; });
  }
  if (finished) return null;
  return <section className="topic-checkin" aria-label="Listening topic check-in">
    <p className={busy ? 'sr-only' : undefined}>{plan.checkin.prompt}</p>
    <div className="classroom-reply-controls">
      {busy && voice.state === 'Speaking' && <button className="text-button compact-replay" aria-label="Replay current speech" onClick={voice.replay}>↻</button>}
      {!busy && <button className="primary" onClick={() => void answer(plan)}>Speak</button>}
      <button className="text-button" disabled={busy && !['Listening', 'Speaking'].includes(voice.state)} onClick={keepTopics}>Keep my topics</button>
    </div>
    <form className="onboarding-name inline-reply" onSubmit={typed}><label htmlFor="topic-reply">Your topic preference</label><div>
        <input id="topic-reply" value={reply} maxLength={200} onChange={event => setReply(event.target.value)} placeholder="Keep them, or name a topic"/>
        <button className="primary" disabled={!reply.trim() || (busy && !['Listening','Speaking'].includes(voice.state))}>Send</button>
      </div></form>
    {error && <div role="alert"><p>{error}</p><button className="text-button" onClick={() => { interrupt(); done(); }}>Continue to lesson</button></div>}
  </section>;
}
