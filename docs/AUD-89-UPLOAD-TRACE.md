# AUD-89 P0 — Recording upload correlation (diagnostic only)

**Question:** when a learner's recording is slow or never arrives, is the delay in the phone's upload, in a proxy in front of the API (Vercel `/api` rewrite, Cloudflare), or inside the API?

**Why now:** in the 2026-10-25 production lesson (iPhone Safari) 2 of 8 recordings never reached the API (the browser's `fetch` failed after about 12.1–12.2 s; no server access line, trace or attempt row) and 3 others arrived 3.4–8.3 s after the browser sent them. The server could not see either case: the body guard returned silently when a client disconnected mid-upload, and the existing trace cannot see time spent before a proxy hands the request over.

No behaviour changes. Everything below is off unless the existing diagnostics are on.

## What is recorded

### Browser (build flag `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING=1`, the same one that already enables turn reporting)
One record per **send** of a recording, queued in memory (mirrored to `sessionStorage`, at most 16) and sent with the next turn report in `deliveries` (at most 8 per report). If the report itself fails with a server error the records go back in the queue; a rejected (4xx) report is dropped, never retried forever.

| Field | Meaning |
|---|---|
| `id` | random 16-hex id **per recording** (not derived from any account); the same recording sent again keeps it, and is sent as `X-Audli-Upload-Id` (same-origin only, so no CORS change) |
| `n` | which send of that recording (1, 2, …) |
| `kind` | `exercise`, `lesson`, `plan`, `onboarding`, `homework` |
| `outcome`, `status` | `ok`, `network`, `timeout`, `aborted`, `http`; HTTP status, 0 when nothing came back |
| `ms` | send → response headers or failure |
| `bytes`, `mime`, `rec_ms` | size and type class of the audio, and length of the recording itself |
| `since_prev_ms` | how long the page had been quiet since its previous API request finished |
| `online`, `net`, `rtt`, `downlink_kbps` | `navigator.onLine` and `navigator.connection` hints where the browser provides them |
| `hidden`, `hidden_ms`, `pagehide` | page hidden during the send (count, total time), page hide event |
| `errname`, `err` | error name (letters only) and message reduced to plain words: any word containing a digit, slash, colon, at-sign or other symbol is dropped whole |

### Server (`AUDLI_TURN_TRACE=true` with `AUDLI_LATENCY_AGGREGATES=true`, already on in production)
One `Upload trace` line per `POST …/attempts` (a pure ASGI pass-through placed outermost, so it sees the request exactly as the proxy delivered it):

| Field | Meaning |
|---|---|
| `upload`, `turn` | the browser's ids when well-formed, else null |
| `declared`, `received` | `Content-Length` and body bytes that actually arrived |
| `first_ms`, `body_ms` | request first seen → first body byte, → last body byte |
| `disconnect_ms` | request first seen → client went away (null if it did not) |
| `status`, `outcome`, `total_ms` | `completed`, `disconnect_before_body`, `disconnect_before_response`, `cancelled`, `error`, `no_response` |
| `cf_ray`, `vercel_id` | opaque request ids of Cloudflare and Vercel, for looking the request up on their side |

`Latency summary` also gains `client.upload_send_<outcome>` histograms, so a rise in failed sends shows in the normal 5-minute windows.

## Reading it: phone vs proxy vs API
Join on the upload id (`deliveries[].id` in the `kind:"client"` line ↔ `upload` in the `Upload trace` line).

| Observation | Reading |
|---|---|
| Client `outcome: network/timeout`, **no** `Upload trace` line | the request never reached this process: lost on the phone's network, at Vercel or at Cloudflare. `since_prev_ms` (stale connection?), `net`/`rtt`, `hidden*`, `bytes` narrow it down; `cf_ray`/`vercel_id` are absent, so use Vercel Observability around the client's timestamp |
| `disconnect_before_body`, `received` < `declared` | the connection was cut mid-upload (upstream gave up or the phone left) |
| Client `ms` ≫ server `total_ms`, `body_ms` ≈ 0 | a proxy upstream of this process buffered the body, then handed it over at once; the time was spent on the phone→proxy leg. Compare with `bytes`: delay that grows with size means a slow uplink |
| `body_ms` large | the body streamed into this process slowly: slow upstream leg (proxy streaming, or the phone) |
| `total_ms − body_ms` large | time inside the API (auth, lease, STT, database): not a delivery problem |
| `hidden > 0` or `pagehide` | Safari backgrounded the page during the send |

## Privacy
Numbers, enums, route templates and random ids only. Never logged: audio, transcripts, authentication tokens, IP addresses, user agents, concrete paths with ids, or account identifiers. The server validates every browser field with a strict bounded schema (unknown fields, long or symbol-bearing strings and out-of-range numbers are rejected with 422 and logged nowhere).

## Cost
`scripts/bench_upload_trace.py` drives the middleware with a 1 MB body in 64 KB chunks: about +0.3 µs per request with tracing off and about +33 µs with it on (one JSON log line), with about 10 KB of extra memory that does not grow with the body, against uploads that take seconds. The wrapper never reads ahead or buffers (`test_the_body_is_never_read_ahead_or_buffered`).

## Rollback
Remove `NEXT_PUBLIC_AUDLI_LATENCY_REPORTING` (Vercel, needs a rebuild) to stop the browser records and the header; unset `AUDLI_TURN_TRACE` (Render) to stop the server lines. Neither affects any learner-visible behaviour.
