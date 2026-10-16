'use client';
import { useEffect, useEffectEvent, useState } from 'react';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { authenticatedFetch } from '../lib/api';
import { verifiedHomeworkUrl, type HomeworkAssignment, type HomeworkView } from '../lib/homework';

async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const response = await authenticatedFetch(`/api${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal } : { signal });
  if (!response.ok) throw new Error('Optional listening is unavailable right now. Your lesson is saved.');
  return response.json() as Promise<T>;
}

function AssignmentCard({ initial, disabled = false }: { initial: HomeworkAssignment; disabled?: boolean }) {
  const { audio, begin, speak, replay } = useVoiceLifecycle();
  const [item, setItem] = useState(initial);
  const [topic, setTopic] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [checkedUrl, setCheckedUrl] = useState<string | null>(() => initial.status === 'assigned' && initial.content && Date.parse(initial.content.validated_at) > Date.now() - 3600000 ? verifiedHomeworkUrl(initial) : null);
  async function action(path: string, body: object) {
    setBusy(true); setError(''); setCheckedUrl(null);
    try { const saved = await request<HomeworkAssignment>(`/homework/${item.id}/${path}`, body); setItem(saved); if (saved.status === 'assigned') setCheckedUrl(verifiedHomeworkUrl(saved)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function checkVideo() {
    setBusy(true); setError('');
    try {
      const checked = await request<HomeworkAssignment>(`/homework/${item.id}/validate`, {});
      setItem(checked); setCheckedUrl(checked.status === 'assigned' ? verifiedHomeworkUrl(checked) : null);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function hearInvitation() {
    setBusy(true); setError('');
    const operation = begin();
    try {
      const response = await operation.wait(() => authenticatedFetch(`/api/homework/${item.id}/invitation-audio`, { method: 'POST', signal: operation.signal }, operation));
      if (!response.ok) throw new Error('The invitation audio is unavailable. You can choose using the buttons.');
      const clip = await operation.wait(() => response.blob());
      await speak(operation, URL.createObjectURL(clip));
    } catch (e) { if (operation.current) setError((e as Error).message); }
    finally { if (operation.current) setBusy(false); operation.release(); }
  }
  const locked = busy || disabled;
  return <article className="homework-card" aria-label="Optional listening assignment">
    <audio ref={audio} hidden/>
    <p className="homework-label">Optional listening · {item.status === 'completed' ? 'Completed (self-reported)' : item.status}</p>
    {item.status === 'offered' && <>
      <p>Would you like a short listening assignment about something you’re interested in?</p>
      <button className="secondary" disabled={locked} onClick={() => void hearInvitation()}>Hear invitation</button>
      <button className="secondary" disabled={locked} onClick={() => replay()}>Replay invitation</button>
      <label htmlFor={`homework-topic-${item.id}`}>Different topic (optional)</label>
      <input id={`homework-topic-${item.id}`} value={topic} maxLength={100} disabled={locked} onChange={e => setTopic(e.target.value)} placeholder="Use my interests"/>
      <div className="homework-actions"><button className="primary" disabled={locked} onClick={() => void action('decision', { accept: true, ...(topic.trim() ? { topic: topic.trim() } : {}) })}>Find optional listening</button>
        <button className="secondary" disabled={locked} onClick={() => void action('decision', { accept: false })}>No thanks</button></div>
      <p className="homework-note">Your topic is sent to YouTube to find a video. Homework never changes your learning scores.</p>
    </>}
    {item.content && <><h3>{item.content.title}</h3><p>YouTube · About {Math.max(1, Math.round(item.content.duration_seconds / 60))} min · {item.content.publisher}</p><p>{item.objective}</p></>}
    {item.status === 'assigned' && verifiedHomeworkUrl(item) && <>
      {checkedUrl ? <a href={checkedUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Watch on YouTube (opens a new tab)</a> : <button className="secondary" disabled={locked} onClick={() => void checkVideo()}>Check video link</button>}
      <p className="homework-note">YouTube content and difficulty may vary. This is optional practice; Audli won’t grade your understanding of this video.</p>
      <div className="homework-actions"><button className="secondary" disabled={locked} onClick={() => void action('status', { status: 'completed' })}>I listened</button>
        <button className="secondary" disabled={locked} onClick={() => void action('status', { status: 'skipped' })}>Skip assignment</button></div>
    </>}
    {item.status === 'unavailable' && <p>{item.message ?? 'This video is unavailable. Continue with your next lesson when ready.'}</p>}
    {item.status === 'skipped' && <p>No homework today. Your next lesson is ready when you are.</p>}
    {busy && <p role="status">{item.status === 'offered' ? 'Finding a short listening assignment…' : 'Saving optional listening…'}</p>}
    {error && <p role="alert">{error}</p>}
  </article>;
}

export function ListeningHomework({ lessonId, disabled = false }: { lessonId?: string; disabled?: boolean }) {
  const [items, setItems] = useState<HomeworkAssignment[]>([]);
  const [enabled, setEnabled] = useState(false);
  const load = useEffectEvent(async (signal: AbortSignal) => {
    try {
      const view = await request<HomeworkView>('/homework', undefined, signal);
      if (!view.enabled || signal.aborted) return;
      const assignments = lessonId ? [await request<HomeworkAssignment>(`/lessons/${lessonId}/homework`, {}, signal)] : view.assignments;
      if (!signal.aborted) { setItems(assignments); setEnabled(true); }
    } catch { /* Optional discovery/offer cannot disrupt completion or the plan. */ }
  });
  useEffect(() => {
    const controller = new AbortController();
    // Authenticated external state is loaded once for this mounted view.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(controller.signal);
    return () => controller.abort();
  }, [lessonId]);
  if (!enabled || items.length === 0) return null;
  return <section className="listening-homework" aria-labelledby={lessonId ? 'completion-homework' : 'plan-homework'}>
    <h2 id={lessonId ? 'completion-homework' : 'plan-homework'}>Optional listening</h2>
    {items.map(item => <AssignmentCard key={item.id} initial={item} disabled={disabled}/>)}
  </section>;
}
