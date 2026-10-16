'use client';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { authenticatedFetch } from '../lib/api';
import { freshHomeworkUrl, verifiedHomeworkUrl, type HomeworkAssignment, type HomeworkView } from '../lib/homework';

async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const deadline = AbortSignal.timeout(25000);
  const boundedSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
  const response = await authenticatedFetch(`/api${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: boundedSignal } : { signal: boundedSignal });
  if (!response.ok) throw new Error('Optional listening is unavailable right now. Your lesson is saved.');
  return response.json() as Promise<T>;
}

function AssignmentCard({ initial, disabled = false }: { initial: HomeworkAssignment; disabled?: boolean }) {
  const { audio, begin, speak, replay } = useVoiceLifecycle();
  const [item, setItem] = useState(initial);
  const card = useRef<HTMLElement>(null);
  const previousStatus = useRef(initial.status);
  useEffect(() => {
    if (previousStatus.current !== item.status && (document.activeElement === document.body || card.current?.contains(document.activeElement))) card.current?.focus();
    previousStatus.current = item.status;
  }, [item.status]);
  const [topic, setTopic] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [checkedUrl, setCheckedUrl] = useState<string | null>(() => freshHomeworkUrl(initial));
  useEffect(() => {
    if (!item.discovery_in_progress) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const saved = await request<HomeworkAssignment>(`/homework/${item.id}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        setItem(saved);
        if (saved.status === 'assigned') setCheckedUrl(freshHomeworkUrl(saved));
        if (saved.discovery_in_progress) timer = setTimeout(() => void poll(), 2000);
      } catch {
        if (!controller.signal.aborted) {
          setError('The connection was interrupted. Reload your saved assignment; your lesson is saved.');
          setItem(value => ({ ...value, discovery_in_progress: false }));
        }
      }
    }
    timer = setTimeout(() => void poll(), 1000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [item.discovery_in_progress, item.id]);
  async function reloadSaved() {
    setBusy(true); setError('');
    try {
      const saved = await request<HomeworkAssignment>(`/homework/${item.id}`);
      setItem(saved); setCheckedUrl(freshHomeworkUrl(saved));
    } catch { setError('The connection was interrupted. Try again later; your lesson is saved.'); }
    finally { setBusy(false); }
  }
  async function action(path: string, body: object) {
    setBusy(true); setError(''); setCheckedUrl(null);
    try { const saved = await request<HomeworkAssignment>(`/homework/${item.id}/${path}`, body); setItem(saved); if (saved.status === 'assigned') setCheckedUrl(freshHomeworkUrl(saved)); }
    catch { setError('The connection was interrupted. Reload your saved assignment; your lesson is saved.'); }
    finally { setBusy(false); }
  }
  async function checkVideo() {
    setBusy(true); setError('');
    try {
      const checked = await request<HomeworkAssignment>(`/homework/${item.id}/validate`, {});
      setItem(checked); setCheckedUrl(freshHomeworkUrl(checked));
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
  const pending = busy || Boolean(item.discovery_in_progress);
  const locked = pending || disabled;
  return <article ref={card} tabIndex={-1} className="homework-card" aria-label="Optional listening assignment" aria-busy={pending}>
    <audio ref={audio} hidden/>
    <p className="homework-label" role="status">Optional listening · {item.status === 'completed' ? 'Completed (self-reported)' : item.status}</p>
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
    {item.content && <><h3>{item.content.title}</h3><p>{item.content.provider === 'youtube' ? 'YouTube' : item.content.provider} · About {Math.max(1, Math.round(item.content.duration_seconds / 60))} min · {item.content.publisher}</p><p>{item.objective}</p></>}
    {item.status === 'assigned' && verifiedHomeworkUrl(item) && <>
      {checkedUrl ? <a href={checkedUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" onClick={event => { if (!freshHomeworkUrl(item)) { event.preventDefault(); setCheckedUrl(null); void checkVideo(); } }}>Watch on YouTube (opens a new tab)</a> : <button className="secondary" disabled={locked} onClick={() => void checkVideo()}>Check video link</button>}
      <p className="homework-note">YouTube content and difficulty may vary. This is optional practice; Audli won’t grade your understanding of this video.</p>
      <div className="homework-actions"><button className="secondary" disabled={locked} onClick={() => void action('status', { status: 'completed' })}>I listened</button>
        <button className="secondary" disabled={locked} onClick={() => void action('status', { status: 'skipped' })}>Skip assignment</button></div>
    </>}
    {item.status === 'unavailable' && <p>{item.message ?? 'This video is unavailable. Continue with your next lesson when ready.'}</p>}
    {item.status === 'skipped' && <p>No homework today. Your next lesson is ready when you are.</p>}
    {pending && <p role="status">{item.status === 'offered' ? 'Finding a short listening assignment…' : 'Saving optional listening…'}</p>}
    {error && <><p role="alert">{error}</p><button className="secondary" disabled={locked} onClick={() => void reloadSaved()}>Reload saved assignment</button></>}
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
