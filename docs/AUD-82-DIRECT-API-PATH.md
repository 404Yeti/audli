# AUD-82 — Optional direct browser-to-API path (feature-flagged, OFF)

## Problem
Every `/api/*` call goes browser → Vercel (hkg1 edge) → Render Singapore through a Next.js rewrite. Same `/api/health` request on a reused connection from Vietnam, 20 samples each (one network, tiny payload, **indicative only**):

| Path | p50 | p90 | max |
|---|---|---|---|
| Direct to Render | 80 ms | 90 ms | 94 ms |
| Via Vercel rewrite | 166 ms | 558 ms | 1,065 ms |

A turn makes several sequential calls, so the tail adds up. This has not been measured in the real browser yet.

## What the flags do (both default off)
* **Server:** `AUDLI_CORS_ENABLED=true` adds Starlette `CORSMiddleware`, outermost, for the exact origins in `AUDLI_ALLOWED_ORIGINS` (startup refuses the flag with no origins). Methods GET/POST/PUT/PATCH/DELETE; request headers `Authorization`, `Content-Type`; exposed `Retry-After`, `X-Audli-Tutor-Fallback`; **no credentials**; preflight cached 600 s.
* **Browser (build time):** `NEXT_PUBLIC_AUDLI_API_ORIGIN=https://audli-api.onrender.com` makes `authenticatedFetch` call that origin. Only an exact https origin is honoured (no path, credentials, wildcard or http); otherwise paths stay relative. Rolling back means unsetting it and rebuilding; the same-origin proxy keeps working throughout.

## Security properties kept (tests: `tests/test_cors.py`, `tests/frontend.test.mjs`)
* **Authentication/authorization:** unchanged. Bearer token on every request, server-verified Supabase identity, account lease, ownership checks. `credentials: 'omit'` stays, so no cookies exist to ride along.
* **CSRF:** not applicable to bearer-token calls without cookies; the existing `LocalRequestGuard` still rejects POST/PUT/PATCH from any non-allowed `Origin` with 403 and still bounds body size (413), including for the allowed origin.
* **CORS:** exact-match origins only (look-alike hosts, http, `null` all refused); no `Allow-Credentials`; unauthenticated preflights are answered without touching any account state; 401/403/413 are still readable by the allowed origin so the app's error handling and session-expiry handling work.
* **Rate/lesson limits:** the lesson-limit and eligibility logic is server-side and unchanged. There is no per-IP rate limiter in the app today; the Render origin is already publicly reachable, so the direct path adds no new network exposure.
* **Caching/redirects:** `cache: 'no-store'`, `redirect: 'error'` unchanged.

## Costs and risks (why this may not pay off at p50)
1. **Preflight round trip.** The `Authorization` header makes every cross-origin call "non-simple". Browsers cache preflight per exact URL, and most turn URLs contain a fresh exercise/attempt ID, so most requests would pay an extra ~80 ms `OPTIONS`. Expect the p50 to be roughly unchanged and the **tail (p90+) to improve**; the real browser must confirm it.
2. **Safari:** preflight cache is shorter (about 5 min) and stricter on credentials; needs iPhone testing.
3. **Origin exposure:** browsers will see and depend on `audli-api.onrender.com`. A custom domain such as `api.audli.ai` would be cleaner, but a sub-domain is still cross-origin, so the preflight cost does not go away.
4. Telemetry route (`/api/telemetry/turn`) and Supabase calls are unaffected.

## Rollout plan (not performed)
1. Set `AUDLI_CORS_ENABLED=true` on Render (harmless while no browser uses it) with `AUDLI_ALLOWED_ORIGINS` containing `https://app.audli.ai` only.
2. Build a **Vercel preview** with the origin variable and compare, with the same telemetry, against production for the same actions; test on iPhone Safari.
3. Enable in Production only after owner approval and a comparison showing a tail improvement worth the preflight cost.

## Alternatives if preflight cost cancels the gain
* Cut requests per turn (merge assess and coach-audio on the server).
* Keep the proxy but look at why it adds 90 ms at p50 / 470 ms at p90 (hkg1 to Singapore routing, TLS reuse, rewrite overhead).
