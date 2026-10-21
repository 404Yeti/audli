'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { authenticatedFetch } from './api';
import { homeView, parseEligibility, serverNow, shouldRevalidate, snapshotOf, tickInterval, type HomeView, type Snapshot } from './eligibility';

/** GET /api/eligibility is the single source of truth for Home. It is revalidated on mount, when the countdown ends,
 *  on focus, visibility, pageshow (back/forward cache) and reconnect, and when another tab announces a change.
 *  A failed or unrecognized answer never invents state: an absent endpoint means "legacy Home"; a transient
 *  failure keeps the last server answer, and the server still enforces every request either way. */
export function useEligibility(scope: string) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  // Monotonic reading used for display; advanced by the ticker and on every server answer (render stays pure).
  const [mono, setMono] = useState(() => performance.now());
  const inflight = useRef<Promise<Snapshot | null> | null>(null);
  const last = useRef<number | null>(null);
  const latest = useRef<Snapshot | null>(null);
  const channel = useRef<BroadcastChannel | null>(null);
  const alive = useRef(true);

  const refresh = useCallback((force = false): Promise<Snapshot | null> => {
    if (inflight.current) return inflight.current;
    if (!shouldRevalidate(last.current, performance.now(), force)) return Promise.resolve(latest.current);
    // Bounded: a hung endpoint can delay a revalidation by seconds, never block the app. Not aborted on unmount: React
    // StrictMode remounts immediately and the shared in-flight request must still deliver its answer.
    const signal = AbortSignal.timeout(4000);
    inflight.current = (async () => {
      try {
        const response = await authenticatedFetch('/api/eligibility', { signal }, undefined, false);
        if (!alive.current) return latest.current;
        if (response.status === 404) { latest.current = null; setSnapshot(null); return null; }
        if (!response.ok) return latest.current;
        const value = parseEligibility(await response.json().catch(() => null));
        const next = value ? snapshotOf(value, performance.now()) : null;
        if (!alive.current) return latest.current;
        latest.current = next; setSnapshot(next); setMono(performance.now());
        return next;
      } catch { return latest.current; }
      finally { last.current = performance.now(); inflight.current = null; }
    })();
    return inflight.current;
  }, []);

  /** Tell the other tabs of this account that the allowance changed (start, end, completion). */
  const announce = useCallback(() => { try { channel.current?.postMessage({ at: Date.now() }); } catch { /* Optional. */ } }, []);

  useEffect(() => {
    alive.current = true;
    const onVisible = () => { if (document.visibilityState === 'visible') void refresh(); };
    const onFocus = () => void refresh();
    const onOnline = () => void refresh(true);
    const onPageShow = (event: PageTransitionEvent) => void refresh(event.persisted);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onFocus); window.addEventListener('online', onOnline); window.addEventListener('pageshow', onPageShow);
    try {
      channel.current = new BroadcastChannel('audli-eligibility:' + scope);
      channel.current.onmessage = () => void refresh(true);
    } catch { channel.current = null; }
    void refresh(true);
    return () => {
      alive.current = false;
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onFocus); window.removeEventListener('online', onOnline); window.removeEventListener('pageshow', onPageShow);
      channel.current?.close(); channel.current = null;
    };
  }, [scope, refresh]);

  const view: HomeView = homeView(snapshot, mono);
  const cadence = tickInterval(view);
  const kind = view.kind;
  useEffect(() => {
    if (cadence === null) return;
    const timer = setInterval(() => {
      setMono(performance.now());
      // The countdown reaching zero only asks the server; it never opens anything by itself.
      if (kind === 'checking') void refresh();
      else if (homeView(latest.current, performance.now()).kind === 'checking') void refresh(true);
    }, cadence);
    return () => clearInterval(timer);
  }, [cadence, kind, refresh]);

  return { snapshot, view, nowMs: snapshot ? serverNow(snapshot, mono) : null, refresh, announce };
}
