'use client';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import type { ClassroomLayout } from './product-shell';
import { useAccount } from './auth-gate';
import { useVoiceLifecycle } from '../lib/voice-lifecycle';
import { recordingUpload } from '../lib/recording';
import type { LessonOperation } from '../lib/api';
import { authenticatedFetch } from '../lib/api';
import { freshHomeworkUrl, verifiedHomeworkUrl, type HomeworkAssignment, type HomeworkView } from '../lib/homework';

class HomeworkRequestError extends Error { constructor(message: string, readonly status: number) { super(message); } }

async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const deadline = AbortSignal.timeout(25000);
  const boundedSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
  const response = await authenticatedFetch(`/api${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: boundedSignal } : { signal: boundedSignal });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new HomeworkRequestError(response.status === 422 && typeof detail?.detail === 'string' ? detail.detail : 'Optional listening is unavailable right now. Your lesson is saved.', response.status);
  }
  return response.json() as Promise<T>;
}

type SharedVoice = ReturnType<typeof useVoiceLifecycle>;
function AssignmentCard({ initial, disabled = false, autoVoice = false, sharedVoice, inline = false, onLayout, onFinished, onResolved, finished = false }: { initial: HomeworkAssignment; disabled?: boolean; autoVoice?: boolean; sharedVoice?: SharedVoice; inline?: boolean; onLayout?: (mode: ClassroomLayout) => void; onFinished?: (responded?: boolean) => void; onResolved?: () => void; finished?: boolean }) {
  const localVoice = useVoiceLifecycle();
  const { audio, begin, speak, listen, stop, thinking, state: voiceState } = sharedVoice ?? localVoice;
  const active = useRef<LessonOperation | null>(null);
  const submittingText = useRef(false);
  const confirmationPlayed = useRef(false);
  const awaitingConfirmation = useRef(false);
  const autoStarted = useRef(false);
  const clarificationPlayed = useRef(false);
  const [item, setItem] = useState(initial);
  const card = useRef<HTMLElement>(null);
  const previousStatus = useRef(initial.status);
  useEffect(() => {
    if (previousStatus.current !== item.status && (document.activeElement === document.body || card.current?.contains(document.activeElement))) card.current?.focus();
    previousStatus.current = item.status;
  }, [item.status]);
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [checkedUrl, setCheckedUrl] = useState<string | null>(() => freshHomeworkUrl(initial));
  const confirmRecovered = useEffectEvent(() => {
    if (!awaitingConfirmation.current || confirmationPlayed.current || active.current || disabled) return;
    awaitingConfirmation.current = false; confirmationPlayed.current = true;
    void hearCue('confirmation').finally(() => { onFinished?.(); onResolved?.(); });
  });
  const polled = useEffectEvent((saved: HomeworkAssignment) => {
    if (saved.status === 'assigned') { setCheckedUrl(freshHomeworkUrl(saved)); if (awaitingConfirmation.current) confirmRecovered(); else { onFinished?.(); onResolved?.(); } }
    if (['unavailable','skipped'].includes(saved.status)) { onFinished?.(); onResolved?.(); }
  });
  const pollFailed = useEffectEvent(() => { (sharedVoice ?? localVoice).idle(); if (inline) onFinished?.(true); });
  useEffect(() => {
    if (!item.discovery_in_progress) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const saved = await request<HomeworkAssignment>(`/homework/${item.id}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        setItem(saved);
        polled(saved);
        if (saved.discovery_in_progress) timer = setTimeout(() => void poll(), 2000);
      } catch {
        if (!controller.signal.aborted) {
          setError('The connection was interrupted. Reload your saved assignment; your lesson is saved.');
          setItem(value => ({ ...value, discovery_in_progress: false })); pollFailed();
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
  async function playCue(operation: LessonOperation, cue: 'invitation' | 'confirmation' | 'farewell' | 'clarification') {
    const response = await operation.wait(() => authenticatedFetch(`/api/homework/${item.id}/${cue}-audio`, { method: 'POST', signal: AbortSignal.any([operation.signal, AbortSignal.timeout(20000)]) }, operation));
    if (!response.ok) throw new Error('Audli’s voice is unavailable. Your lesson is saved; type your reply or retry audio.');
    const clip = await operation.wait(() => response.blob());
    await speak(operation, URL.createObjectURL(clip));
  }
  async function runChoice(voice: boolean, body?: object, invitation = false) {
    if (active.current || disabled || item.discovery_in_progress) return;
    const operation = begin(); active.current = operation;
    setBusy(true); setError(''); thinking();
    let assigned = false; let settled = false; let failed = false;
    try {
      let saved: HomeworkAssignment;
      if (voice) {
        if (invitation) await playCue(operation, 'invitation');
        let recognized: HomeworkAssignment | undefined;
        for (let attempt = 0; attempt < 2; attempt++) {
          const blob = await listen(operation);
          const response = await operation.wait(() => authenticatedFetch(`/api/homework/${item.id}/decision/attempts`, {
            method: 'POST', body: recordingUpload(blob), signal: AbortSignal.any([operation.signal, AbortSignal.timeout(30000)]),
          }, operation));
          if (!response.ok) {
            const detail: unknown = await operation.wait(() => response.json().catch(() => null));
            if (response.status === 422 && !clarificationPlayed.current) {
              clarificationPlayed.current = true;
              await playCue(operation, 'clarification');
              continue;
            }
            throw new HomeworkRequestError(detail && typeof detail === 'object' && 'detail' in detail && typeof detail.detail === 'string' ? detail.detail : 'I could not hear your choice. Try again or type your reply. Your lesson is saved.', response.status);
          }
          recognized = await operation.wait(() => response.json() as Promise<HomeworkAssignment>);
          break;
        }
        if (!recognized) throw new Error('Please try your choice again or type it. Your lesson is saved.');
        saved = recognized;
      } else saved = await operation.wait(() => request<HomeworkAssignment>(`/homework/${item.id}/decision${body && 'text' in body ? '/text' : ''}`, body, operation.signal));
      operation.assertCurrent(); settled = !saved.discovery_in_progress && ['assigned','skipped','unavailable'].includes(saved.status); setItem(saved); setCheckedUrl(freshHomeworkUrl(saved)); assigned = saved.status === 'assigned'; awaitingConfirmation.current = Boolean(saved.discovery_in_progress);
      if (saved.status === 'assigned' && !confirmationPlayed.current) {
        confirmationPlayed.current = true;
        await playCue(operation, 'confirmation');
      }

    } catch (e) { if (operation.current) failed = !(e instanceof HomeworkRequestError && e.status === 422); if (operation.current) setError(assigned ? 'Your assignment is saved in Plan. Audli’s voice could not play; use Hear assignment to retry.' : (voice || e instanceof HomeworkRequestError && e.status === 422) && e instanceof Error && !['TimeoutError', 'AbortError'].includes(e.name) ? e.message : 'The connection was interrupted. Reload your saved assignment; your lesson is saved.'); }
    finally { if (operation.current) { setBusy(false); (sharedVoice ?? localVoice).idle(); } if (active.current === operation) active.current = null; operation.release(); if (settled) onResolved?.(); if (operation.current && inline && (settled || failed)) onFinished?.(true); }
  }
  function cancelVoice() {
    // A shared lifetime also owns the lesson's own operations (finishing, setup); cancel only this card's.
    if (sharedVoice) { active.current?.cancel(); sharedVoice.idle(); } else stop();
    active.current = null; awaitingConfirmation.current = false; setBusy(false);
    setError(''); if (inline) onFinished?.(true);
  }
  useEffect(() => () => { active.current?.cancel(); }, []);
  const suspend = useEffectEvent(cancelVoice);
  useEffect(() => { if (disabled && active.current) suspend(); }, [disabled]);
  const startVoice = useEffectEvent(() => {
    if (autoStarted.current || disabled || finished) return;
    if (initial.status !== 'offered' && !initial.discovery_in_progress) { autoStarted.current = true; onFinished?.(false); onResolved?.(); return; }
    if (!autoVoice) return;
    autoStarted.current = true;
    void runChoice(true, undefined, true);
  });
  useEffect(() => {
    const timer = setTimeout(() => startVoice(), 0);
    return () => clearTimeout(timer);
  }, [autoVoice, disabled, finished]);
  async function action(path: string, body: object) {
    if (path === 'decision') return runChoice(false, body);
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
  async function hearCue(cue: 'invitation' | 'confirmation' | 'farewell' | 'clarification') {
    if (active.current || disabled) return;
    const operation = begin(); active.current = operation; setBusy(true); setError('');
    try { await playCue(operation, cue); }
    catch (e) { if (operation.current) setError((e as Error).message); }
    finally { if (operation.current) setBusy(false); if (active.current === operation) active.current = null; operation.release(); }
  }
  const pending = busy || Boolean(item.discovery_in_progress);
  const locked = pending || disabled;
  const layout: ClassroomLayout = item.content ? 'content' : pending ? 'focus' : 'interaction';
  useEffect(() => { if (inline) onLayout?.(layout); }, [inline, layout, onLayout]);
  function submitText(event: React.FormEvent) {
    event.preventDefault();
    if (!reply.trim() || disabled || item.discovery_in_progress || submittingText.current) return;
    if (active.current && !['Listening', 'Speaking'].includes(voiceState)) return;
    // Typed input is an explicit interruption, never a concurrent second turn.
    if (active.current) { stop(); active.current = null; setBusy(false); }
    submittingText.current = true;
    void runChoice(false, { text: reply.trim() }).finally(() => { submittingText.current = false; });
  }
  return <article ref={card} tabIndex={-1} className={'homework-card' + (inline ? ' classroom-homework' : '')} aria-label="Optional listening assignment" aria-busy={pending}>
    {!sharedVoice && <audio ref={audio} hidden/>}
    <p className="homework-label" role="status">Optional listening · {item.status === 'completed' ? 'Completed (self-reported)' : item.status}</p>
    {finished && item.status === 'offered' && <p>You can return to optional listening in Plan.</p>}
    {!finished && item.status === 'offered' && <>
      <p className={pending ? 'sr-only' : 'homework-invitation'}>Would you like a short listening assignment about something you’re interested in?</p>
      <div className="classroom-reply-controls">
        {!pending && <button className="primary" disabled={disabled} onClick={() => void runChoice(true)}>Speak</button>}
        {!pending && <button className="text-button compact-replay" disabled={disabled} onClick={() => void hearCue('invitation')} aria-label="Replay homework invitation">↻</button>}
        {busy && voiceState === 'Speaking' && <button className="text-button compact-replay" aria-label="Replay current speech" onClick={(sharedVoice ?? localVoice).replay}>↻</button>}
        {busy && <button className="text-button" onClick={event => { if (event.detail < 2) cancelVoice(); }}>Skip optional listening</button>}
      </div>
      {!item.discovery_in_progress && !finished && <form onSubmit={submitText} className="onboarding-name inline-reply"><label htmlFor={`homework-reply-${item.id}`}>Your reply</label><div>
          <input id={`homework-reply-${item.id}`} value={reply} maxLength={200} disabled={disabled} onChange={e => setReply(e.target.value)} placeholder="Yes please, no thanks, or a topic"/>
          <button className="primary" disabled={disabled || !reply.trim() || (busy && !['Listening', 'Speaking'].includes(voiceState))}>Send</button>
        </div></form>}
      {!pending && !finished && <button className="text-button" onClick={() => void runChoice(false, { accept: false })}>Skip homework</button>}
      {!pending && !finished && <p className="homework-disclosure">Optional · your topic is sent to YouTube.</p>}
    </>}
    {item.content && <div className="assignment-content">
      <h3>{item.content.title}</h3>
      <p className="assignment-source">{item.content.provider === 'youtube' ? 'YouTube' : item.content.provider} · About {Math.max(1, Math.round(item.content.duration_seconds / 60))} min</p>
      <div className="assignment-actions">
        {item.status === 'assigned' && verifiedHomeworkUrl(item) && (checkedUrl
          ? <a href={checkedUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" onClick={event => { if (!freshHomeworkUrl(item)) { event.preventDefault(); setCheckedUrl(null); void checkVideo(); } }}>Watch on YouTube<span className="sr-only"> (opens a new tab)</span></a>
          : <button className="text-button" disabled={locked} onClick={() => void checkVideo()}>Check video link</button>)}
        {item.status === 'assigned' && <button className="text-button compact-replay" disabled={locked} onClick={() => void hearCue('confirmation')} aria-label="Hear assignment">↻</button>}
        {busy && voiceState === 'Speaking' && <button className="text-button compact-replay" aria-label="Replay current speech" onClick={(sharedVoice ?? localVoice).replay}>↻</button>}
        {busy && <button className="text-button" onClick={event => { if (event.detail < 2) cancelVoice(); }}>Skip optional listening</button>}
      </div>
      {!inline && <div className="homework-details"><p>{item.objective}</p><p>{item.content.publisher}</p><p>Optional practice, without factual grading.</p></div>}
      {!inline && item.status === 'assigned' && <div className="homework-actions"><button className="text-button" disabled={locked} onClick={() => void action('status', { status: 'completed' })}>I listened</button><button className="text-button" disabled={locked} onClick={() => void action('status', { status: 'skipped' })}>Skip assignment</button></div>}
    </div>}
    {item.status === 'unavailable' && <p>{item.message ?? 'This video is unavailable. Continue with your next lesson when ready.'}</p>}
    {item.status === 'skipped' && <p>No homework today. Your next lesson is ready when you are.</p>}
    {!inline && busy && <p role="status">{voiceState === 'Listening' ? 'Listening for your choice…' : voiceState === 'Speaking' ? 'Audli is speaking…' : 'Preparing optional listening…'}</p>}
    {inline && busy && voiceState === 'Listening' && <span className="sr-only">Listening for your choice…</span>}
    {item.discovery_in_progress && <p role="status">Finding a short listening assignment…</p>}
    {error && <div className="homework-recovery"><p role="alert">{error}</p><button className="text-button" disabled={locked} onClick={() => void reloadSaved()}>Reload saved assignment</button></div>}
  </article>;
}

type HomeworkProps = { lessonId?: string; disabled?: boolean; autoVoice?: boolean; sharedVoice?: SharedVoice; inline?: boolean; onLayout?: (mode: ClassroomLayout) => void; onFinished?: (responded?: boolean) => void; onResolved?: () => void; finished?: boolean };

export function ListeningHomework(props: HomeworkProps) {
  const account = useAccount();
  const { onResolved } = props;
  // Homework switched off for this account: there is nothing to decide.
  useEffect(() => { if (!account.homeworkEnabled) onResolved?.(); }, [account.homeworkEnabled]); // eslint-disable-line react-hooks/exhaustive-deps
  // Do not mount effects/audio/state until the existing bootstrap explicitly opts in.
  // Account keys and unmounting also discard saved homework and late continuations.
  if (!account.homeworkEnabled) return null;
  return <EnabledListeningHomework key={account.scope} {...props}/>;
}

function EnabledListeningHomework({ lessonId, disabled = false, autoVoice = false, sharedVoice, inline = false, onLayout, onFinished, onResolved, finished }: HomeworkProps) {
  const [items, setItems] = useState<HomeworkAssignment[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const controller = useRef<AbortController | null>(null);
  async function load(signal: AbortSignal) {
    setLoadError(false);
    try {
      const view = await request<HomeworkView>('/homework', undefined, signal);
      if (signal.aborted) return;
      if (!view.enabled) { if (inline) onFinished?.(); onResolved?.(); return; }
      const assignments = lessonId ? [await request<HomeworkAssignment>(`/lessons/${lessonId}/homework`, {}, signal)] : view.assignments;
      if (!signal.aborted) { setItems(assignments); setEnabled(true); if (assignments.length === 0) onResolved?.(); }
    } catch { if (!signal.aborted) { setLoadError(true); if (inline) onFinished?.(); } }
  }
  const initialLoad = useEffectEvent(load);
  useEffect(() => {
    const pending = new AbortController(); controller.current = pending;
    // Authenticated external state is loaded once for this mounted view.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void initialLoad(pending.signal);
    return () => pending.abort();
  }, [lessonId]);
  if (loadError) return <div className="session-error" role="alert">Your lesson is saved. Optional listening could not load.<button className="text-button" onClick={() => { if (controller.current) void load(controller.current.signal); }}>Retry optional listening</button>{onResolved && <button className="text-button" onClick={onResolved}>Continue without homework</button>}</div>;
  if (!enabled || items.length === 0) return null;
  return <section className="listening-homework" aria-labelledby={lessonId ? 'completion-homework' : 'plan-homework'}>
    <h2 className={inline ? 'sr-only' : undefined} id={lessonId ? 'completion-homework' : 'plan-homework'}>Optional listening</h2>
    {items.map(item => <AssignmentCard key={item.id} initial={item} disabled={disabled} autoVoice={autoVoice} sharedVoice={sharedVoice} inline={inline} onLayout={onLayout} onFinished={onFinished} onResolved={onResolved} finished={finished}/>)}
  </section>;
}
