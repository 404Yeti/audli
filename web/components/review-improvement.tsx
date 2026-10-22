'use client';
import { useEffect, useRef, useState } from 'react';
import { LessonLifetime } from '../lib/api';
import type { ImprovementState } from '../lib/review';

const unavailable: Record<string, string> = {
  unreliable_recognition: "Audli wasn't sure it heard this answer correctly, so it can't safely suggest improvements.",
  too_short: 'This answer is too short for language suggestions.',
  too_long: 'This answer is too long for language suggestions.',
};
const rejected = "Audli couldn't improve this answer without risking its meaning, so it isn't showing a suggestion.";

/** Optional, user-triggered language coaching for the saved answer. Language only: it never changes progress. */
export function ReviewImprovement({ exerciseId, initial }: { exerciseId: string; initial: ImprovementState | null | undefined }) {
  const owner = useRef<LessonLifetime | null>(null);
  const [state, setState] = useState<ImprovementState | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const lifetime = new LessonLifetime(); owner.current = lifetime;
    return () => lifetime.close();
  }, []);

  async function request() {
    if (!owner.current?.current || busy) return;
    const operation = owner.current.begin(); setBusy(true); setError('');
    try {
      const data = await operation.api<{ improvement: ImprovementState }>(`/exercises/${encodeURIComponent(exerciseId)}/review/improvement`, { method: 'POST' });
      operation.assertCurrent(); setState(data.improvement);
    } catch (e) { if (operation.current) setError((e as Error).message); }
    finally { if (operation.current) setBusy(false); operation.release(); }
  }

  if (!state) return null;
  const { state: kind } = state;
  return <div className="review-improvement" aria-label="Improve my answer" role="group">
    {kind === 'unavailable' && <p className="review-caption">{unavailable[state.reason ?? ''] ?? 'Language suggestions are not available for this answer.'}</p>}
    {(kind === 'available' || kind === 'pending') && <>
      <p className="review-caption">Want a more natural way to say it? Audli can suggest one. This is about English only and never changes your progress.</p>
      <button className="secondary" disabled={busy} onClick={() => void request()}>{kind === 'pending' ? 'Check again' : 'Show an improved version'}</button>
      {kind === 'pending' && !busy && <p role="status">Audli is still preparing this suggestion.</p>}
    </>}
    {kind === 'rejected' && <p className="review-caption">{rejected}</p>}
    {kind === 'ready' && (state.unchanged
      ? <p role="status">Your answer already reads naturally. Nothing needs changing.</p>
      : <div className="review-improved">
        <h4>Your answer, improved</h4>
        <p className="review-script">{state.improved_text}</p>
        <h4>What changed</h4>
        <p className="review-diff" aria-label="Changes between your answer and the improved answer">{state.diff.map((part, index) => part.op === 'delete'
          ? <del key={index}><span className="sr-only">Removed: </span>{part.text}</del>
          : part.op === 'insert' ? <ins key={index}><span className="sr-only">Added: </span>{part.text}</ins>
          : <span key={index}>{part.text}</span>)}</p>
        <p className="review-caption">Struck-through words were removed. Underlined words were added.</p>
        {state.notes.length > 0 && <><h4>Why</h4>
          <ul className="review-why">{state.notes.map((note, index) => <li key={index}><strong>{note.kind === 'grammar' ? 'Grammar' : 'Vocabulary'}:</strong> “{note.before}” → “{note.after}”. {note.why}</li>)}</ul></>}
        <p className="review-caption">Language coaching only. It doesn&apos;t change whether your answer matched the passage, or your progress.</p>
      </div>)}
    {error && <div role="alert"><p>{error}</p></div>}
  </div>;
}
