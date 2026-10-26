'use client';
import { useEffect, useRef } from 'react';
import type { Notice } from '../lib/delivery-notice';

/** AUD-89 P2: the learner-facing delivery states. Progress is a polite status; a failure is an alert that takes focus on its first button.
 * "Send again" resends the saved recording and does not replay the question; "Record again" starts listening and keeps the question as text. */
export function DeliveryNotice({ notice, prompt, onSendAgain, onRecordAgain, onContinue, onReplay, disabled }: {
  notice: Notice; prompt: string; onSendAgain: () => void; onRecordAgain: () => void; onContinue: () => void; onReplay: () => void; disabled: boolean;
}) {
  const first = useRef<HTMLButtonElement | null>(null);
  useEffect(() => { if (!disabled && (notice.kind === 'failed' || notice.kind === 'unsent')) first.current?.focus(); }, [notice.kind, disabled]);
  if (notice.kind === 'failed') return (
    <div role="alert" className="session-error delivery-notice" data-delivery="failed">{notice.text}
      <button ref={first} className="secondary" disabled={disabled} onClick={onSendAgain}>Send again</button>
      <button className="secondary" disabled={disabled} onClick={onRecordAgain}>Record again</button>
      <button className="secondary" disabled={disabled} onClick={onReplay}>Replay question</button>
      {notice.ambiguous && <button className="secondary" disabled={disabled} onClick={onContinue}>Resume saved lesson</button>}
    </div>
  );
  if (notice.kind === 'unsent') return (
    <div role="alert" className="session-error delivery-notice" data-delivery="unsent">{notice.text}
      <button ref={first} className="secondary" onClick={onContinue}>Continue</button>
    </div>
  );
  return <p role="status" className="delivery-notice" data-delivery={notice.kind}>{notice.text}{notice.kind === 'recordAgain' && prompt ? <> <span>{prompt}</span></> : null}</p>;
}
