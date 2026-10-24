import { authenticatedFetch, installRequestTrace } from './api';
import { installTurnSink, activeTurnId, traceKind, recordRequestSpan } from './turn-timing';

/** Build-time opt-in (NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1). Fire-and-forget; failures are ignored. */
export function installTurnReporting() {
  if (typeof window === 'undefined' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_REPORTING !== '1') return;
  installRequestTrace({ turnId: activeTurnId, kindOf: traceKind, record: recordRequestSpan });
  installTurnSink((stages, trace) => {
    void authenticatedFetch('/api/telemetry/turn', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(trace ? { stages, ...trace } : { stages }), keepalive: true,
    }).catch(() => undefined);
  });
}
