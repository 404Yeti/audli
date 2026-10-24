# AUD-82 — Reducing database round trips and connection overhead: implementation proposal

Status: **design and test plan only. No production code, authentication, account lock, region or endpointing change.** The only code added is a measurement script (`scripts/analyze_db_roundtrips.py`, synthetic data, no network).

## 1. Why: what the production class showed
One real class (23:46 UTC, 6 exercise turns, one learner): a turn took ~26 s from speech end to coaching audio; providers (STT, assessment model, ElevenLabs) were 4.6 s. The Singapore to Tokyo database link costs **72 ms per round trip** (`db.pool_ping`, n=592). A write request averaged 13 statements and 7.6 pool checkouts; the three requests on every turn's critical path (`attempts`, `assess`, `coach-audio`) spent about 12 s on the database, account lock, scope read and Auth call.

## 2. Where the round trips come from (measured by driving a full synthetic lesson)
`scripts/analyze_db_roundtrips.py` runs a complete lesson over the real HTTP app (SQLite stand-in, so **counts are real, latencies are not**). Per call, Postgres round trips are modelled as: 1 ping per checkout, 1 `BEGIN` per transaction (psycopg sends it as a separate command; verified in `psycopg/_connection_base.py::_start_query`), 1 per statement, 1 `COMMIT`/`ROLLBACK` per transaction (a read leaves the transaction open, so the return-to-pool rollback is also a round trip).

| Route (per call) | Statements | Checkouts | Repeated identical statements | Round trips now | x 72 ms |
|---|---|---|---|---|---|
| `POST /attempts/{id}/assess` (avg of a follow-up turn and a final turn) | 48 | 17 | 16 | **99** | 7.1 s |
| `POST /exercises/{id}/conversation/ready` (only flips a state flag; ~17 of its 31 statements are the conversation rewrite, ~11 build the response) | **31** | 14 | 8 | **73** | 5.3 s |
| `POST /lessons/{id}/exercise` | 21 | 12 | 7 | 57 | 4.1 s |
| `GET /exercises/{id}/conversation` | 11 | 11 | 5 | 44 | 3.2 s |
| `POST /exercises/{id}/conversation/listened` | 11 | 10 | 6 | 41 | 3.0 s |
| `POST /exercises/{id}/attempts` | 16.5 | 7 | 2 | 37.5 | 2.7 s |
| `POST /exercises/{id}/coach-audio` | 12.3 | 8.3 | 2.3 | 37.3 | 2.7 s |
| `POST /recognition/acknowledgment-audio` | 2.7 | 2.3 | 0 | 9.7 | 0.7 s |
| Whole synthetic lesson | 288 | 152 | 71 | **748** | 54 s |

**Model check against production:** `assess` 99 round trips x 72 ms = 7.1 s, plus the assessment model 3.1 s, plus auth/scope/lock ~1.5 s = 11.7 s; production measured 11.7-12.5 s. `attempts` and `coach-audio` land within ~10% too.

**What the statements show (histogram per route, `--histogram`)**
1. **Every repository call is its own connection and transaction** (17 transactions for one `assess`). A read pays ping + `BEGIN` + `SELECT` + `ROLLBACK` = 4 round trips for one row.
2. **`write_conversation` rewrites the whole conversation on every save**: ownership selects, an upsert of the row, a "prior" `SELECT` plus upsert per follow-up and per turn, the assessment, and **one `INSERT` per evidence unit (7-8)**, even when only `state` changed. `/ready` and the checkpoint save inside `assess` pay this, and `assess` does it twice.
3. **The same immutable `exercises` row is read 3-6 times per request** (`exercise_or_404`, `current_exercise`, `owned_exercise`, `public_conversation`...). 71 of 288 lesson statements (25%) are exact repeats.
4. `POST /conversation/ready` only flips a state flag, but the client waits for it after the feedback audio finishes and before the next beat (`page.tsx`, `GIVING_FEEDBACK` branch). Production measured **6.75 s and 6.79 s** (n=2), matching the model (5.3 s of round trips plus ~1.5 s of auth/scope/lock).

## 3. Event-loop blocking (synchronous database access)
The handlers are `async def` but call the synchronous SQLAlchemy/psycopg repositories directly, as does the lock middleware (`lease_engine.connect()`, `execute`). Each call blocks the single event loop for its full network round trip(s). Evidence:
* In the class window, a 50 ms `asyncio.sleep` in the lock retry loop took **mean 635 ms, max 3.1 s** (n=10); one `/api/health` request (trivial handler) took **1.45 s** while normal is under 1 ms.
* Blocking database work in the 10 minutes of lessons: queries ~100 s + pings ~33 s + lock connects ~21 s + lock queries ~11 s = **~164 s, about 27% of wall time** during which no other request makes progress.
Consequence: concurrent requests from the same learner (acknowledgment audio, telemetry, polling) queue behind whichever request is on the loop, and lock retries are themselves delayed, which prolongs contention.

## 4. Account-lock contention, analysed separately
* Every non-GET request (except `/api/telemetry/turn` and one Review route) takes a Postgres advisory lock on a **new connection** (`NullPool`): 434 ms to connect (`lease.connect_physical`, n=66) + two statements of 109 ms. Held until the response ends, **including the provider call**.
* The client starts `assess`, and if it has not finished after 1.2 s it requests acknowledgment audio (`prepareAcknowledgment`, `voice-lifecycle.ts`). That POST needs the same lock, so it waits for the whole assessment (up to 12 s). Observed: acknowledgment-audio requests **8.5 s mean, 9.2 s max**, `auth.lease` max 8.1 s, up to 7 attempts. The acknowledgment exists to mask latency, so a cold-cache request that waits for the assessment defeats its purpose. The neutral cue is static, cached text-to-speech with no lesson mutation (see the handler comment).
* Each retry reconnects (0.43 s) and re-queries, so a waiter burns ~0.7 s of loop-blocking work per attempt instead of ~72 ms.

## 5. Proposed phases (each small, independently testable, default OFF, no schema change)
Round trips are cumulative on the synthetic lesson; time uses 72 ms per round trip.

| Phase | Change | Flag | Critical-path saving per turn (attempts + assess + coach-audio) | Risk |
|---|---|---|---|---|
| **0** | Measurement only: time commit/rollback, per-route statement/checkout gauges, event-loop lag sampler, and a CI "round-trip budget" test | none (telemetry flag exists) | 0 | Very low |
| **1** | **Skip the pool ping on hot connections**: `pool_pre_ping` off plus a checkout hook that pings only if idle longer than N seconds; a failed ping raises `DisconnectionError` (SQLAlchemy's documented reconnect path) | `AUDLI_DB_HOT_PING_SECONDS` (0 = today) | -32 round trips = **-2.3 s** | Low-medium: stale connections after pooler idle timeout |
| **2** | **Read-only operations in autocommit**: no `BEGIN`, no end-of-transaction round trip. Reads only; locking reads (`FOR SHARE`) and all writes stay transactional | `AUDLI_DB_READ_AUTOCOMMIT` | -55 = **-3.9 s** | Medium: misclassifying a write as a read |
| **3** | **Batch conversation writes**: one multi-row evidence insert; fold the "prior" selects into the upsert (preserve `created_at`, keep the foreign-exercise check) | `AUDLI_DB_BATCH_CONVERSATION_WRITES` | -17 = **-1.2 s** | Medium: ownership checks must be preserved |
| **4** | **Per-request memo of immutable `exercises` rows** keyed by (learner, exercise) in a request-scoped ContextVar | `AUDLI_DB_REQUEST_MEMO` | -9 = **-0.7 s** | Low-medium: stale if the row ever changes |
| **5** | **State-only transitions**: `/conversation/ready` updates only the `conversations` row instead of rewriting every child row. **Corrected 2026-10-24:** the route still performs the reads that build its response (about 11 statements), so only the save is removed | `AUDLI_DB_STATE_ONLY_TRANSITIONS` | none inside a turn; **about -17 round trips (73 to 56), about -1.2 s once per exercise**, on the wait after the feedback audio | Low |
| Total phases 1-4 | | | **~8.1 s modelled** of 12.5 s database time (-65%) | |

**Lock and loop phases (need separate approval; none removes the lock)**
| Phase | Change | Flag | Effect | Risk |
|---|---|---|---|---|
| L1 | Acquire and release the account lock **off the event loop** (`asyncio.to_thread`); same lock, same order | `AUDLI_LEASE_OFF_LOOP` | Removes ~0.65 s of loop blocking per write request (~32 s per lesson) and unblocks retry sleeps | Low |
| L2 | Retry on the **same** lock connection instead of reconnecting each attempt | `AUDLI_LEASE_REUSE_ATTEMPT` | Retry cost 0.7 s to ~0.07 s; contention behaves the same | Low |
| L3 | Serve the **static acknowledgment and retry cues without the account lock** after review against the AUD-68 lease-exemption criteria (the handler must use only short atomic transactions of its own; its cache write is idempotent and learner-scoped) | `AUDLI_LEASE_EXEMPT_STATIC_CUES` | Acknowledgment no longer waits for the assessment (8.5 s to under 1 s when cold) | Medium: a change in lock coverage, reviewed per route |
| L4 | Take the lock from a small **dedicated pool** instead of a new connection each time | `AUDLI_LEASE_POOL` | -0.36 s per write request (**~-1.1 s per turn**) | Medium: pooler session limits; connection held per active request as today |
Handlers' own database calls stay synchronous for now. After phases 1-5 the blocking share drops by roughly two thirds; moving heavy handler sections to threads is a later decision, taken from the loop-lag sampler.

## 6. Estimate, assumptions and overlap (read before trusting the numbers)
* Savings come from **measured operation counts x a measured 72 ms round trip**, applied cumulatively in order, so phases do not double count. The synthetic average for `assess` mixes a follow-up turn (~70 round trips) and a final turn (~128). Per turn: 144.8 round trips for a typical turn (-6.7 s), 202.8 for a final turn (-9.4 s). **Quote 5-8 s per turn (about 20-30% of 26 s)** after derating for the assumptions below.
* SQLite stands in for Postgres: statement and checkout counts follow the same code paths, but Postgres-only branches (`FOR SHARE`, the lock) are not exercised. Calibrated against production counts: production write requests had up to 32 statements and 23 checkouts; the model matches `assess`, `attempts`, `coach-audio` totals within ~10%.
* A statement costs one round trip plus execution; execution time is not reduced. Writes cost more than 72 ms, so savings on writes are a lower bound.
* The lock and Auth costs are unchanged by phases 1-5; L1-L4 and the authentication cache (PR #84) are additive but **overlap** with each other only through the loop-blocking effect, which I have not double counted.
* Not modelled: commit latency on Supabase (fsync), pooler queueing, retries. Production p50/p90 will tell.

## 7. Safety properties to preserve, and how
| Property | Phase risks | Control |
|---|---|---|
| Account isolation | P4 memo, P3 upsert | Memo keyed by learner and exercise in a ContextVar reset per request; ownership selects stay (or become `WHERE user_id =` predicates); cross-account tests for each |
| Transaction safety / rollback | P2, P3, P5 | Writes and locking reads never move to autocommit; each write path keeps one transaction; failure tests assert nothing persisted |
| Concurrency protection | L1-L4 | Lock semantics unchanged (advisory xact lock per learner, same key); contention tests on real Postgres |
| Isolation level | P2 | Postgres and Supabase default to READ COMMITTED, so a multi-statement read transaction already has no snapshot consistency; a deploy check asserts `default_transaction_isolation = 'read committed'` before enabling P2 |
| Lesson semantics | all | **Golden-dump equivalence**: run the full synthetic lesson with flags off and on and compare every table row and every API response (volatile ids/timestamps normalized) |

## 8. Test plan
1. **Round-trip budget test** (CI): the analysis script's counters per route with a ceiling per phase; fails on regression.
2. **Golden equivalence** (CI): one lesson, flags off vs all on, identical rows and responses. Repeat for every phase alone.
3. **Full suite under all flags** in a CI matrix leg (`AUDLI_DB_*=on`), on SQLite and on the PostgreSQL job.
4. **P1:** idle shorter than N skips the ping (counter), longer pings; a killed backend (`pg_terminate_backend`) is recovered once and the request succeeds; no ping storm.
5. **P2:** a read-mode guard that fails any non-`SELECT` in a read connection (runs everywhere, including SQLite); locking reads rejected; no leaked autocommit setting on pool return.
6. **P3:** duplicate/conflicting evidence rows behave as `ON CONFLICT DO NOTHING` did; a follow-up or turn belonging to another exercise still raises; `created_at` preserved.
7. **P4:** memo cleared at request end, not shared across learners/requests, bypassed after any write to the memoized table.
8. **P5:** only `conversations.state` and `data.state` change; children untouched; wrong-state calls rejected as today.
9. **L1-L4:** contention on real Postgres (two learners, one learner with parallel requests), cancellation and timeout paths, the acknowledgment route cannot touch lesson state (AST guard like `test_auth_lease_exemption`), pooled lock connections never hold a stale lock after errors.
10. **Production verification** per phase: `Latency summary` shows fewer statements/checkouts per route and no rise in 4xx/5xx, plus the next test class.

## 9. Rollout and rollback
* One PR per phase, flag default off, merged dark. Enable in production only with approval, one flag at a time, in order **P1, P2, P3, P4, P5, then L1-L4**, each observed for a full test class (and at least 30 turns before claiming a result).
* **Rollback:** unset the environment variable on Render (restart about a minute). No migration, no data change, so rollback is lossless. Each phase also keeps the old code path intact behind the flag.
* Stop conditions: any rise in 5xx, `AccountUnavailable`, lock timeouts, or a golden-dump diff in production-like staging.

## 10. Recommended first step
Merge nothing risky yet. Next PR: **Phase 0 + the round-trip budget test**, then **Phase 5** (lowest risk, but a modest win: about 17 of `ready`'s 73 round trips; my first estimate of 73 to 5 forgot the response-building reads), then **P1** and **P2**, which together are 80% of the measured saving.
