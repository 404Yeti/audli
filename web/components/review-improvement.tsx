'use client';
import { useEffect, useRef, useState } from 'react';
import { LessonLifetime } from '../lib/api';
import type { ImprovementState } from '../lib/review';

const unavailable: Record<string, string> = {
  unreliable_recognition: "Audli wasn't sure it heard this answer correctly, so it can't safely suggest improvements.",
  too_short: 'This answer is too short for language suggestions.',
  too_long: 'This answer is too long for language suggestions.',
};
// Rejection codes that mean the suggestion could not be put in the required form (no judgment about meaning).
const formatting = new Set(['unexplained_changes', 'note_not_grounded', 'inconsistent_notes']);
const rejectedMeaning = "Audli couldn't improve this answer without risking its meaning, so it isn't showing a suggestion.";
const rejectedFormat = "Audli couldn't put a suggestion together for this answer this time.";
const rejectedFinal = "Audli couldn't improve this answer, so it isn't showing a suggestion. You've already tried twice.";

/** Accurate disclosure (see docs/AUD-77-LEARNER-DISCLOSURE.md): shown before the first request. Never claims zero retention. */
const disclosure = 'To do this, Audli sends the text of your answer (not your voice) to its AI provider, OpenAI. OpenAI may keep it for up to 30 days to check for misuse, and by default does not use it to train its models.';

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

  async function request(retry = false) {
    if (!owner.current?.current || busy) return;
    const operation = owner.current.begin(); setBusy(true); setError('');
    try {
      const data = await operation.api<{ improvement: ImprovementState }>(`/exercises/${encodeURIComponent(exerciseId)}/review/improvement`,
        retry ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ retry: true }) } : { method: 'POST' });
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
      <details className="review-disclosure"><summary>How your answer is handled</summary>
        <p className="review-caption">{disclosure}</p>
        <p className="review-caption">Audli can&apos;t remove OpenAI&apos;s copy sooner. Only your answer text is sent: never the passage, your voice or your account details. Audli saves the suggestion with your lesson.</p></details>
      <button className="secondary" disabled={busy} onClick={() => void request()}>{kind === 'pending' ? 'Check again' : 'Show an improved version'}</button>
      {kind === 'pending' && !busy && <p role="status">Audli is still preparing this suggestion.</p>}
    </>}
    {kind === 'rejected' && <>
      <p className="review-caption" role="status">{!state.retry_available ? rejectedFinal : formatting.has(state.reason ?? '') ? rejectedFormat : rejectedMeaning}</p>
      {state.retry_available && <>
        <p className="review-caption">You can try once more. That uses one of your daily language suggestions.</p>
        <button className="secondary" disabled={busy} onClick={() => void request(true)}>Try once more</button></>}
    </>}
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
