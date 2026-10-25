import { authenticatedFetch, installRequestTrace } from './api.ts';
import { installTurnSink, activeTurnId, traceKind, recordRequestSpan } from './turn-timing.ts';
import { requeueDeliveries, takeDeliveries } from './upload-delivery.ts';

/** Build-time opt-in (NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1). Fire-and-forget; failures are ignored. */
export function installTurnReporting() {
  if (typeof window === 'undefined' || process.env.NEXT_PUBLIC_AUDLI_LATENCY_REPORTING !== '1') return;
  installRequestTrace({ turnId: activeTurnId, kindOf: traceKind, record: recordRequestSpan });
  installTurnSink((stages, trace) => {
    // AUD-89: per-send upload records ride along; if the report itself fails they go back in the queue for the next one.
    const deliveries = takeDeliveries();
    const payload = { stages, ...(trace ?? {}), ...(deliveries.length ? { deliveries } : {}) };
    void authenticatedFetch('/api/telemetry/turn', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), keepalive: true,
    }).then(response => { if (response.status >= 500 && deliveries.length) requeueDeliveries(deliveries); })   // a rejected (4xx) report is dropped, never retried forever
    .catch(() => { if (deliveries.length) requeueDeliveries(deliveries); });
  });
}
