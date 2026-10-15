'use client';

import { AudliMascot } from './audli-mascot';

/** Startup only: decorative Pip is inline SVG, so its space never depends on a download. */
export function AudliLoadingScreen({ message = 'Getting Audli ready…', error, onRetry }: {
  message?: string;
  error?: string;
  onRetry?: () => void;
}) {
  return <main className="audli-loading-screen" aria-label="Audli startup">
    <strong className="wordmark">audli</strong>
    <div className="startup-presence" data-failed={Boolean(error)}><AudliMascot/></div>
    {error ? <div className="startup-recovery"><h1>Let’s try that again.</h1><p role="alert">{error}</p><button className="secondary" onClick={onRetry}>Retry connection</button></div>
      : <p className="startup-status" role="status" aria-live="polite" aria-atomic="true">{message}</p>}
  </main>;
}
