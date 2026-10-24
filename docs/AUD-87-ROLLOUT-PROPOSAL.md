# AUD-87 — Proposed controlled production rollout (nothing enabled; for approval)

Status on 2026-10-24: `main` at `6f98d61` is deployed on Render with every new flag **off** (#92 benchmark, #95 `AUDLI_DB_OFFLOAD`, #96 `AUDLI_DB_LAZY_PING`); health 200, no errors, P2 effective, P5 off. #93 (ack), #94 (lease pool), #97 (request memo) are held and current with `main`.

## Interaction check on merged `main` (synthetic benchmark, 20 sequential + 32 concurrent turns each)
| Configuration | One learner p50 / p95 (s) | Four learners p50 / p95 (s) | Health p95 (4 learners) | Loop lag mean |
|---|---|---|---|---|
| control (both off) | 18.5 / 20.7 | 51.2 / 66.4 | 13.1 s | 577 ms |
| `AUDLI_DB_OFFLOAD` | 17.4 / 19.4 | 18.3 / 21.8 | 122 ms | 10 ms |
| `AUDLI_DB_LAZY_PING` (5 s window) | 15.7 / 17.6 | 42.2 / 45.6 | 7.1 s | 431 ms |
| **both** | **14.6 / 16.2** | **15.6 / 17.5** | 130 ms | 8.6 ms |

No adverse interaction. For one learner the savings add up (-1.1 s and -2.8 s alone, -3.9 s together). With four learners the offload removes most of the damage by itself (-32.9 s) and lazy ping adds a further -2.7 s on top (-9.0 s alone, since the offload already removes most of what it would have saved). The 5 s lazy-ping window gives the same saving as the earlier 30 s window.

## Proposed order (each step is one environment variable on Render; about a minute to apply or undo)
| Step | Change | Why this order | Check before the next step | Roll back if |
|---|---|---|---|---|
| 1 | `AUDLI_DB_OFFLOAD=true` | removes the event-loop freezes; the safest large effect; code path unchanged with the flag off | in the next `Latency summary` windows: `loop.lag` mean under 30 ms and max under 1 s (was 20 to 33 ms mean and up to 4.5 s); `/api/health` always 200 and fast; no `QueuePool limit` or `Operation failed` in the logs; one owner class completes | any error-level log line from the repository, any 5xx, a class that does not complete |
| 2 | `AUDLI_DB_LAZY_PING=true` (window 5 s by default) | removes about 150 pings per lesson | `db.pool_ping` count falls to a small fraction of `db.checkout` count; zero 503s in the class; turn timing p50 falls | any 503 beyond one isolated event; pings not falling |
| 3 | `AUDLI_ACK_LEASE_EXEMPT=true` after #93 is merged | cue stops queueing behind the assessment | cue request 0.5 to 2 s in the trace, `auth.lease_attempts` above 1 gone, the cue audible during the wait | cue missing or wrong voice |
| 4 | `AUDLI_LEASE_POOL=true` after #94 is merged and the Supabase connection headroom is confirmed (see its doc) | cuts about 0.5 s of lease cost per request | `lease.connect_physical` count near zero, no stale-connection 503s | any lease error |
| 5 | `AUDLI_REQUEST_READ_MEMO=true` after #97 is merged | small, safe saving | statements per assessment fall from 48 to about 45 | any wrong or stale state shown |

Steps are independent; each is verified in production by the existing telemetry (`Latency summary`, `routes`, `loop.lag`, `db.pool_ping`) and, for the browser gap, the AUD-85 trace (`AUDLI_TURN_TRACE`, still available). The synthetic benchmark replaces the need for a class to *evaluate* a change; one class per step is only the acceptance check that production behaves like the benchmark.

## Expected learner-visible effect
Steps 1 and 2 remove about 3.9 s of the server-observed turn in the benchmark (for several learners at once, much more); with steps 3 to 5 about 5.4 s. The ~27 s class figure therefore falls to roughly 21 to 23 s with 1 and 2, roughly 21 s with all five; the database round trips plan (`docs/AUD-88-ROUNDTRIP-PLAN.md`) is the route to the next 3 to 4 s, and the browser-side gap (AUD-85) remains unmeasured in production until a traced class.

## Watch-list during the rollout
`QueuePool limit` (the application pool is 3 connections with a 10 s wait; offloading lets up to 3 sections run at once); `Operation failed`; `lease` retries; health latency; Render CPU and memory (threads add a little); and a deliberate check that a cancelled request (closing the browser tab mid-assessment) leaves the account usable at once.
