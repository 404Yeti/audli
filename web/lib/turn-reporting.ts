import { authenticatedFetch } from './api';
import { installTurnSink } from './turn-timing';

/** Build-time opt-in (NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1). Fire-and-forget; failures are ignored. */
export function installTurnReporting() {
  if (typeof window === 'undefined' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_REPORTING !== '1') return;
  installTurnSink(stages => {
    void authenticatedFetch('/api/telemetry/turn', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stages }), keepalive: true,
    }).catch(() => undefined);
  });
}
