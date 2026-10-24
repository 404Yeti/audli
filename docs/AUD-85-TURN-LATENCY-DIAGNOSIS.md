# AUD-85 — Why a turn still takes ~27 s after P2 (diagnosis, no behaviour change)

Status: findings from existing production telemetry (class 2, 2026-10-24 04:08–04:18 UTC, P2 on, P5 off) plus a diagnostic-only tracing PR. **Nothing here changes lesson behaviour, authentication, locks, database configuration, endpointing or providers. Tracing is off by default.**

## What the existing logs already prove
One traced-by-hand turn (turn A; times are server log times, request start = end − `request_total`):

| Time (04:10) | Request | Server total | What it did |
|---|---|---|---|
| :25.9 → :31.8 | `POST /exercises/{id}/attempts` (upload) | 5.9 s | 1.7 s before the handler (token verify + lease + scope), 0.5 s audio validation, 1.9 s transcription, ~1 s persistence |
| :32.1 → :41.1 | `POST /attempts/{id}/assess` | 9.0 s | 1.1 s auth/lease, ~1.6 s database, **3.5 s OpenAI**, **~2.8 s database after the provider answered** |
| :34.8 → :42.3 | `POST /recognition/acknowledgment-audio` | **7.5 s** | **~7 s waiting for the account lease held by `/assess`**, then 0.4 s |
| :42.3 → :45.9 | `POST /exercises/{id}/coach-audio` | 3.6 s | 1.0 s auth/lease, 0.5 s ElevenLabs, ~2 s database/response |

1. **Acknowledgment audio competes with assessment (answer to question 7: yes, and it defeats its own purpose).** The browser starts `/assess`, waits 1.2 s, then requests the acknowledgment cue. That cue is a POST, so the middleware makes it take the per-account Postgres advisory lease, which `/assess` holds for its whole 9 s. The cue therefore returns only after the assessment finished (7.5 s), and `coach-audio` is requested only when the cue request returns (1.2 s after the assessment ended). The handler itself reads no learner state (neutral cached cue, own asyncio lock).
2. **The 7.6 s lease contention (question 4):** exactly this pair: `POST /recognition/acknowledgment-audio` waiting on `POST /attempts/{id}/assess` (6–11 retries, `auth.lease_attempts` 6 and 7, `auth.lease_wait_sleep` n=11 in that window). Class 1 had the same structure (the cue route averaged 8.5 s there too); the earlier "new episode" reading was wrong, I compared the wrong windows.
3. **Everything database-related runs synchronously on the event loop (question 5).** Repository calls are plain `def` calls inside `async def` handlers (no `to_thread`), and the lease middleware calls `lease_engine.connect()` and `execute()` directly. Each lease attempt opens a **new** connection to Tokyo (435 ms mean), runs two statements (218 ms) and closes it (72 ms): ~0.7 s during which nothing else in the process runs. A request waiting 7 s for the lease therefore blocks the loop for most of that time and slows the very assessment it is waiting for. Measured: `loop.lag` max 3.0 / 4.5 / 3.4 s in the three windows, and no `/api/health` answer was logged between 04:11:58.8 and 04:12:06.9 (8 s).
4. **Fixed per-request preamble:** every POST pays token verification (0.3 s mean), a new lease connection and lock query (0.7 s mean) and the scope check (0.15 s): ~1.1–1.7 s, four times per turn.
5. **Client versus server gaps (question 3):** the browser's `upload_transcription` averaged 10.3 s against 5.4 s of server time (class 1: 5.5 s against 5.6 s), and `assessment` 18.8 s against 12.3 s for one turn. The server timer starts only when the request reaches the middleware, so the missing 4.7–6.5 s lie before it: in the browser (`await accessToken()` runs inside the timed block, as does the Vercel rewrite hop) or in a loop that was too busy to accept the request. The existing telemetry cannot separate these; the tracing below does.
6. **Providers are not the problem:** transcription 1.1–1.2 s, ElevenLabs 0.4–0.5 s, OpenAI assessment 3.5 s, in both classes.

## What the tracing PR adds (off by default)
* Server: `AUDLI_TURN_TRACE=true` (needs `AUDLI_LATENCY_AGGREGATES=true`). A request carrying a valid random `X-Audli-Turn` (12 lowercase hex, any other value is ignored) logs one `Turn trace` line: route template, start/end wall time, spans (auth verify, lease, scope, handler stages), which synchronous sections ran **on the loop thread** and for how long, database counts, lease attempts and, when it had to wait, **which request held the lease** (route template and that request's turn id).
* Loop stalls ≥ 250 ms log a `Loop stall` line attributing the stall to the overlapping synchronous sections by kind and turn, and the unexplained remainder (CPU or anything not instrumented).
* Browser: `NEXT_PUBLIC_AUDLI_TURN_TRACE=1` (build time). One random id per spoken turn (from end-of-speech to coaching playback), sent as `X-Audli-Turn` on same-origin API calls, and per request of the four kinds (upload, assess, ack, coach) the start relative to end-of-speech, the time awaiting the access token and the time to response headers, reported in the existing `/api/telemetry/turn` call. The header is never sent to a cross-origin API (no CORS change).
* `scripts/analyze_turn_trace.py render-logs.txt` joins both sides per turn: lease waits and holders, loop-blocked time, token waits, and the delay before each request reached the server (relative, because browser and server clocks differ).

Privacy: ids are random per turn and unrelated to any account; lines carry numbers, route templates and ids only (tests assert no UUID, token, learner id or SQL). Bounded: 48 spans and 12 blocks per request, a 512-entry ring, 16 browser spans, one line per request. Overhead measured on the local app: 1.82 ms/request with everything off, 1.91 ms with aggregates, 1.97 ms with tracing and a header (+0.06 ms), against a 72 ms database round trip.

## Ranked optimization plan (by measured critical-path impact; nothing is implemented)
Numbers marked *measured* come from class 2; *estimate* is arithmetic on measured values and must be confirmed with the trace.

| # | Change | Evidence | Gain per turn |
|---|---|---|---|
| 1 | **Take the acknowledgment cue off the account lease** (it reads/writes no learner state) | cue waits 7 s behind `/assess`; coaching request waits for it (1.2 s); its lease polling blocks the loop ~6 s while `/assess` runs | *measured* 1.2 s directly; *estimate* 1–3 s more by unblocking `/assess`; also gives the cue its purpose back (plays during the wait) |
| 2 | **Cheaper lease acquisition** (reuse a held connection instead of connect + 2 statements + close per request; same lock semantics) | lease 0.7 s mean × 4 POSTs, all on the loop | *measured* ~2.8 s of preamble per turn; *estimate* 2 s recoverable |
| 3 | **Find and fix the browser-side gap** (token acquisition, Vercel hop) | 4.7 s average gap on upload for the class-2 learner, 6.5 s before one `/assess` | *measured* gap, cause unknown until traced; potentially the largest item |
| 4 | **Move synchronous database work off the event loop** | 3–4.5 s loop stalls; 8 s with no health answer; ~5 s of database work per `/assess` inline | little for one learner (the turn is sequential), large head-of-line effect with several learners |
| 5 | **Fewer round trips on the critical path**: P1 pool pings (~15 checkouts per `/assess`, 72 ms each), the 2.8 s of database work after the OpenAI answer, auth-verify caching (0.3 s × 4) | measured counts and times | *estimate* 1–3 s each; P1 and auth caching need their own approval |

Not recommended now: Redis or a database architecture change. The measured losses are dominated by per-request fixed costs (lease connection, token verify, pings) and by ordering (cue behind assessment), not by the database's capacity.

## Using the trace
1. Merge and deploy this PR (flag off changes nothing).
2. Set `AUDLI_TURN_TRACE=true` on Render and `NEXT_PUBLIC_AUDLI_TURN_TRACE=1` on Vercel (redeploy the web app), run one class, then export the logs and run `scripts/analyze_turn_trace.py`.
3. Turn both flags off afterwards.

## Rollback
Unset the two flags (the code paths are inert without them) or revert the PR.
