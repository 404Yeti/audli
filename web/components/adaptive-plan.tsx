'use client';
import { useEffect, useEffectEvent, useState } from 'react';
import { authenticatedFetch } from '../lib/api';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { recordingUpload } from '../lib/recording';
import { reliableRecognition } from '../lib/hands-free';
import { planStatus, checkinStatus, type Plan } from '../lib/planner';

const json = (revision: number) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision }) });

export function PlanningInvitation({ open }: { open: () => void }) {
  const [due, setDue] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void authenticatedFetch('/api/plan', { signal: controller.signal }).then(r => r.ok ? r.json() : null)
      .then((plan: Plan | null) => { if (!controller.signal.aborted) setDue(Boolean(plan?.checkin?.available)); }).catch(() => {});
    return () => controller.abort();
  }, []);
  return due ? <aside><p>Three lessons completed. Your optional spoken check-in is ready.</p><button className="secondary" onClick={open}>Review your next lessons</button></aside> : null;
}

export function AdaptivePlan() {
  const { audio, begin, stop, speak, listen, replay } = useVoiceLifecycle();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [minimum, setMinimum] = useState(.65);
  async function load() {
    const operation = begin();
    try {
      const [saved, profile] = await Promise.all([operation.api<Plan>('/plan'), operation.api<{ recognition_min_confidence?: number }>('/profile')]);
      operation.assertCurrent(); setPlan(saved); setMinimum(profile.recognition_min_confidence ?? .65);
    } catch (e) { if (operation.current) setError((e as Error).message); }
    finally { operation.release(); }
  }
  const initialLoad = useEffectEvent(load);
  useEffect(() => {
    // Saved state is loaded from the authenticated API.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void initialLoad();
  }, []);
  async function checkin(skip = false) {
    if (!plan || busy) return;
    setBusy(true); setError('');
    const operation = begin();
    try {
      let saved = plan;
      if (skip) saved = await operation.api<Plan>('/plan/checkin/skip', json(saved.revision));
      else {
        if (!saved.checkin.pending || !reliableRecognition(saved.checkin.pending, minimum)) {
          const response = await operation.wait(() => authenticatedFetch('/api/plan/checkin/audio', { ...json(saved.revision), signal: operation.signal }, operation));
          if (!response.ok) throw new Error('Check-in audio is unavailable. Retry or skip; your lessons are saved.');
          const clip = await operation.wait(() => response.blob());
          await speak(operation, URL.createObjectURL(clip));
          const captured = await listen(operation);
          const form = recordingUpload(captured); form.append('revision', String(saved.revision));
          saved = await operation.api<Plan>('/plan/checkin/attempts', { method: 'POST', body: form });
          operation.assertCurrent(); setPlan(saved);
        }
        saved = await operation.api<Plan>('/plan/checkin/answer', json(saved.revision));
      }
      operation.assertCurrent(); setPlan(saved);
    } catch (e) {
      if (operation.current) {
        setError((e as Error).message);
        try { const saved = await operation.api<Plan>('/plan'); operation.assertCurrent(); setPlan(saved); } catch { /* Keep last checkpoint visible. */ }
      }
    } finally { if (operation.current) setBusy(false); operation.release(); }
  }
  return <section className="shell-view adaptive-plan"><h1>Your listening plan</h1><audio ref={audio} hidden/>
    {!plan && !error && <p role="status">Loading your plan…</p>}
    {error && <p role="alert">{error}<button className="secondary" onClick={() => void load()}>Reload plan</button></p>}
    {plan && <><h2>Current learning focus</h2><p>{plan.focus}</p>
      <p>{plan.completed_lessons} lessons completed · {plan.block_progress} of 3 toward your next check-in</p>
      <p>{planStatus(plan)}</p><h2>Next three lessons</h2>
      <ol>{plan.upcoming.map(item => <li key={item.number}><h3>{item.title}</h3><p>{item.objective}</p>
        <p>Estimated difficulty: {item.difficulty.vocabulary_level} · {item.difficulty.duration_seconds}-second passages · {Math.round(item.difficulty.speech_rate * 100)}% speaking pace</p>
        {item.accent_focus && <p>Optional accent interest: {item.accent_focus}. Audli will ask before using it for a passage.</p>}
      </li>)}</ol>
      <h2>Listening check-in</h2><p role="status">{checkinStatus(plan)}</p>
      {plan.checkin.available && <><p>{plan.checkin.prompt}</p>
        <button className="primary" disabled={busy} onClick={() => void checkin()}>Start spoken check-in</button>
        <button className="secondary" disabled={busy} onClick={() => void checkin(true)}>Skip check-in</button>
        {busy && <><p role="status">Listen to Audli’s question, then share your feedback.</p><button className="secondary" onClick={() => replay()}>Replay prompt</button><button className="secondary" onClick={() => { stop(); setBusy(false); }}>Stop check-in</button></>}
      </>}
      {!!plan.completed.length && <><h2>Completed lessons</h2><ol>{plan.completed.map(item => <li key={item.id}>{item.title} · {new Date(item.completed_at).toLocaleDateString()}</li>)}</ol></>}
    </>}
  </section>;
}
