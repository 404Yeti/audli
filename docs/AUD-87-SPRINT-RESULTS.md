# AUD-87 — Latency sprint results (synthetic benchmark, nothing deployed)

Method: `docs/AUD-87-BENCHMARK.md`. Real app, real PostgreSQL 17 behind a 72 ms round trip, real authentication middleware and account lease, stubbed providers and Supabase Auth with production-like latencies. 20 sequential turns and 32 concurrent turns (4 learners) per configuration, after warm-up. "Turn" = start of upload to end of coaching audio (the server-observed part of speech-end-to-coaching; browser-side silence detection, token wait and network come on top). Baseline = current `main` with P2 on, as in production.

## Per fix, each alone (flag on, everything else as baseline)
| Configuration | Sequential p50 / p95 (s) | 4 learners p50 / p95 (s) | Health p95 under load | Loop lag mean | Lease time mean |
|---|---|---|---|---|---|
| baseline | 18.7 / 20.9 | 51.1 / 66.2 | 13.0 s | 578 ms | 1807 ms |
| `AUDLI_ACK_LEASE_EXEMPT` | 16.8 / 19.1 | 47.1 / 59.0 | 11.2 s | 390 ms | 387 ms |
| `AUDLI_LEASE_POOL` | 17.2 / 19.5 | 42.9 / 57.7 | 11.9 s | 485 ms | 1699 ms |
| `AUDLI_DB_OFFLOAD` | 17.4 / 19.4 | **18.7 / 22.8** | **120 ms** | **9.7 ms** | 1878 ms |
| `AUDLI_DB_LAZY_PING` | **15.7 / 17.6** | 42.2 / 45.6 | 7.1 s | 430 ms | 1257 ms |
| `AUDLI_REQUEST_READ_MEMO` | 18.0 / 20.0 | 48.2 / 62.1 | 12.1 s | 539 ms | 1702 ms |

## Combined (integration branch `robertleyba/aud-87-all`)
| Configuration | Sequential p50 / p95 (s) | 4 learners p50 / p95 (s) | Cue p50 | Coaching gap p50 | Health p95 (4 learners) | Lease retries |
|---|---|---|---|---|---|---|
| control (all flags off, same run) | 18.5 / 20.8 | 51.3 / 66.4 | 10.0 s | 0.9 s | 12.5 s | 43 |
| offload + ack | 16.6 / 18.8 | 17.7 / 22.4 | 0.6 s | 0.0 s | 6 ms | 0 |
| offload + ack + lease pool | 15.9 / 18.2 | 17.3 / 20.1 | 0.6 s | 0.0 s | 6 ms | 0 |
| **all five** | **13.1 / 14.8** | **13.8 / 17.5** | 0.5 s | 0.0 s | 7 ms | 0 |

All five together, sequential p50 by route: upload 4.2 to 3.2 s, assessment 10.3 to 7.7 s, acknowledgment cue 10.0 to 0.5 s, coaching audio 3.2 to 2.1 s, lease time 1798 to 171 ms. **-5.4 s (-29 %) for one learner and -37.5 s (-73 %) for four concurrent learners.**

## What this says
1. **The dominant defect is the synchronous event loop, not the database itself.** With four learners the unmodified app takes 51 s per turn and does not answer health checks for 13 s; offloading the database sections alone brings that to 18.7 s. For a single learner it is worth little (the turn is sequential), which is why one-learner production classes under-showed it.
2. **The cue no longer waits for the assessment** (10 s to 0.6 s) and coaching starts the moment the assessment ends.
3. **Lazy ping is the largest single-learner saving (-3.0 s)**, the lease pool -1.5 s, request memo -0.7 s. They compound: all five give 13.1 s.
4. **The under-10-s target is not reached.** Of the 13.1 s, stubs account for 5.05 s (transcription 1.1, assessment 3.5, TTS 0.45) and the stubbed Supabase Auth call for about 0.9 s (0.3 s on each of three requests). The remaining ~7 s is roughly 100 sequential database round trips at 72 ms (the assessment alone makes 45 statements on 14 checkouts) plus the lease. In production the browser adds silence detection (~1.8 s), token wait and network, which the benchmark does not include, so expect the learner-visible figure to fall from about 27 s to roughly 20 s from these fixes, not to 10 s.

## What would be needed for 10 s (not done; each needs its own decision)
| Lever | Estimated | Constraint |
|---|---|---|
| Database region (Singapore to Tokyo 72 ms) to same region | up to ~6 s per turn (100 round trips x ~65 ms) | you ruled out changing regions for now; by far the largest single item |
| Fewer statements per turn (batch the assessment persistence and the `public_conversation` reads, P5-style state-only writes elsewhere) | 1 to 2 s | code change per route, flagged |
| In-process TTL cache of the Supabase Auth verification (PR #84 design) | ~0.9 s | needs your approval (auth behaviour) |
| Offload the remaining lesson-phase routes (`heard`, `answer`, `start`, introduction/farewell audio ...) | none for the exercise turn; helps concurrency | follow-up, same pattern |
| Redis | not recommended: `docs/AUD-87-REDIS-EVALUATION.md` | ideal ceiling 12 s per lesson, about what lazy ping already gives |

## Pull requests (all flags default off; nothing merged or deployed)
Benchmark and docs (this PR); one PR per fix: `AUDLI_ACK_LEASE_EXEMPT`, `AUDLI_LEASE_POOL`, `AUDLI_DB_OFFLOAD`, `AUDLI_DB_LAZY_PING`, `AUDLI_REQUEST_READ_MEMO`. Each is independent and rebased on `main`; they touch the same few lines of `app/config.py`, `app/auth.py` and `app/main.py`, so after the first merges the others need a trivial rebase. `robertleyba/aud-87-all` contains the conflict resolutions and was the benchmark target for the combined rows.

Each fix was tested on SQLite and real PostgreSQL (equivalence of responses and every table, isolation, failure cases, rollback of the flag) and the whole existing suite was run with its flag forced on; see each PR.

## Caveats
Single run per configuration on one machine; differences under about 1 s are noise, the large effects are not. Providers are stubs with fixed latency, so provider variance and OpenAI/ElevenLabs behaviour are not exercised. Four learners are not production traffic. Pool size (3) is unchanged and caps database concurrency; raising it is a separate decision. Enabling `AUDLI_LEASE_POOL` keeps up to 36 more database connections: check the Supabase pooler limit first.
