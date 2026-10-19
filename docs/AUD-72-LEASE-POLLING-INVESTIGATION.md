# AUD-72 — per-account lease polling and the FastAPI event loop (technical investigation)

**Status: investigation only. Nothing in the application was changed.** Linear: AUD-72 (Backlog). Related: AUD-68 (found it), AUD-70 (per-learner locks).
Evidence: `scripts/experiments/lease_polling_approaches.py` → `docs/AUD-72-LEASE-APPROACHES.json`; `scripts/experiments/db_statements_per_request.py` →
`docs/AUD-72-DB-STATEMENTS.json`. Both ran against a **disposable local PostgreSQL 17 on loopback (no TLS, ~0 ms network)**, so every CPU and blocking number below is a **lower bound** for
production, which reaches Supabase's **Session pooler** over the network with `sslmode=require` (`docs/PERSISTENCE.md`). The production database region / round-trip time is **unknown** (see §8).

## 1. Summary and recommendation
* **Problem (measured):** waiting for the per-account lease does a *synchronous* connect + query on the event loop every 50 ms per waiter. With 20 waiters the loop is delayed by a median **266 ms**.
* **Larger context (measured):** the whole app issues synchronous DB statements on the loop: **118 statements per exercise turn** (45 in `/assess`). At an app↔DB round trip of 20 ms that is ≈2.4 s of loop blocking per turn; at 80 ms ≈9.4 s. The lease wait is the sharpest instance of a systemic pattern.
* **Recommended smallest safe fix:** replace the polling with **one native-async psycopg connection per request and a single server-side wait** — `pg_advisory_xact_lock` bounded by a transaction-local `lock_timeout` of 5 s (§5). It scored best on every measured axis (loop lag ≈0, CPU 5–10× lower, accurate 5.0 s timeout, FIFO hand-off in ~2 ms, clean cancellation) with **no new dependency**, ~40 lines in `app/auth.py`, and identical exclusion semantics. Estimated **M (1–2 days incl. tests and pooler validation)**.
* **Do not** use the "run each polling attempt in a thread" variants as the end state: they fix the lag but **raise CPU use** (up to 2.5 cores at 20 waiters vs 0.43 today), which a 0.15 vCPU instance cannot afford, and they are fragile under cancellation.
* **Step 0 (XS, before or with the fix):** time the lease acquisition and count DB statements per request in production through the AUD-68 telemetry, so the real RTT replaces the assumptions here.

## 2. How the lease works today (`app/auth.py`, `LearnerAuthentication.__call__`)
1. Verify identity with Supabase (HTTP, async). 2. For PostgreSQL and any non-GET/HEAD/OPTIONS request: loop **up to 100 times**: `lease_engine.connect()` (new `NullPool` connection, **sync**) →
`set_config('idle_in_transaction_session_timeout','0',true)` → `SELECT pg_try_advisory_xact_lock(key)`; if acquired, **keep that connection (an open transaction) for the entire request**; else close it and `await asyncio.sleep(.05)`.
3. After 100 failures: `HTTPException(409, 'Your account is saving another step. Wait and retry.')`. 4. On any exit, `lease.close()` (rolls back, releasing the lock).
`key` = first 8 bytes of `sha256('audli-account:' + verified_learner_id)`. Exclusion is therefore *per verified account, across processes*; it is independent of the repository's revision compare-and-swap (`save_lesson(expected_revision)`), which stays untouched by any option below.

Nominal timeout is "100 × 50 ms = 5 s", but **100 attempts × (50 ms + attempt cost)** is the real bound: **6.7 s** locally (attempt ≈18 ms) and, because the attempt cost is a connect + 2 round trips (several RTTs with TLS and SCRAM), it grows with network latency — at a 200 ms RTT the effective wait could be minutes while the loop is mostly blocked. A fixed 5 s would be a *correction* of today's behavior; it needs owner sign-off.

## 3. Measured results (loopback; lower bounds)
Event-loop lag = how late a 5 ms `asyncio.sleep` ticker wakes while N requests wait on a held lease for 2 s.

| Variant | 1 waiter lag p50 / p99 | 5 waiters | 20 waiters p50 / p99 / max (ms) | CPU (cores) at 5 / 20 waiters | connects in 2 s (20 waiters) |
|---|---|---|---|---|---|
| **v0 current** (sync on loop) | 0.2 / 19 | 26.9 / 63 | **266 / 287 / 299** | 0.43 / 0.43 | 144 |
| v1 thread per attempt, new connection | 0.2 / 1.1 | 0.3 / 2.3 | 1.3 / 10 / 30 | **0.74 / 2.46** | 321 |
| v2 thread per attempt, connection reused | 0.2 / 1.0 | 0.2 / 2.0 | 0.8 / 6.8 / 32 | 0.28 / 1.20 | 20 |
| **v3 async psycopg, one server-side wait** | 0.3 / 0.8 | 0.3 / 1.2 | **0.3 / 5.8 / 54** | **0.056 / 0.116** | **20** |

| Behavior | v0 | v1 | v2 | v3 |
|---|---|---|---|---|
| Hand-off after the holder releases | 60 ms | 57 ms | 19 ms | **2 ms** |
| Gives up after (target 5 s) | 6.73 s | 6.66 s | 5.36 s | **5.02 s** |
| Mutual exclusion (max simultaneous holders) | 1 | 1 | 1 | 1 |
| Arrival order preserved (FIFO) | no | no | no | **yes** |
| Cancelled waiter leaves a connection / phantom lock | no / no | no / no | no / no, but an unretrieved `ResourceClosedError` surfaced (race with a worker thread) | no / no |

Reading: moving blocking work off the loop (v1, v2) fixes lag but multiplies CPU (new TLS/SCRAM handshakes in parallel threads); loopback hides TLS cost, so production CPU would be higher. v3 removes both the polling and the blocking.

### Systemic: synchronous DB calls per request (`docs/AUD-72-DB-STATEMENTS.json`)
| Request | statements | local DB time |
|---|---|---|
| `POST /api/exercises` | 14 | 23 ms |
| `…/conversation/listened` | 11 | 17 ms |
| `…/attempts` (upload + STT bookkeeping) | 10 | 19 ms |
| **`…/assess`** | **45** | 63 ms |
| `…/coach-audio` | 13 | 24 ms |
| `…/conversation/ready` | 25 | 36 ms |
| **One exercise turn** | **118** | ≈180 ms |

Event loop blocked per turn ≈ statements × RTT: **0.12 s at 1 ms, 0.35 s at 3 ms, 2.4 s at 20 ms, 9.4 s at 80 ms, 24 s at 200 ms** (commits add round trips not counted here; this is a lower bound). Whether this matters in production depends entirely on the real RTT and is the first thing to measure. If the database is not in the same region as the Render instance (Singapore) this, not the lease, is the dominant cost.

## 4. Options compared
| # | Approach | Fixes lag | CPU on 0.15 vCPU | Keeps exclusion | Timeout accuracy | Cancellation | New dependency | Complexity | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `asyncio.to_thread` per attempt (new connection) | yes | **worse** (2.5 cores @20) | yes | 6.7 s | needs shield + cleanup | none | M | reject as end state |
| 2 | Thread per attempt, one connection per waiter | yes | better than 1, still polling | yes | 5.4 s | fragile (observed race) | none | M | fallback only |
| 3 | **Native async psycopg + server-side wait with `lock_timeout`** | **yes** | **best** | yes | **5.0 s** | clean (`async with` / close) | **none** (psycopg async ships in `psycopg[binary]`) | **S–M** | **recommended** |
| 3′ | SQLAlchemy async engine instead of native psycopg | yes | similar | yes | 5.0 s | clean | **adds `greenlet`** (not installed today) | M | not needed |
| 4 | Coalesce same-account requests in-process **before** touching the DB (per-account `asyncio.Lock` with a 5 s timeout, then one lease attempt) | removes waiters (not the holder's connect) | good | yes on one process; DB lease still guards other processes | exact | trivial | none | S–M | good complement, not a replacement |
| 5 | Keep polling, add backoff/jitter and a deadline | partial (fewer attempts) | partial | yes | exact (deadline) | unchanged | none | XS | stop-gap only |
| 6 | Drop the DB lease, rely on in-process locks | yes | best | **no across processes/instances/scripts** | n/a | n/a | none | S | reject: weakens a stated guarantee |
| 7 | Fail fast (no waiting) | yes | best | yes | none (409 at once) | n/a | none | XS | changes UX/timeout behavior; reject |
| 8 | Make the *whole* repository async (or offload every repo call) | yes | depends | yes | n/a | n/a | async driver/greenlet | **L–XL** | separate project (§7) |

## 5. Recommended smallest safe fix (design only; NOT implemented)
In `app/auth.py` replace the polling block with one helper (sketch):
```python
async def acquire_account_lease(url, key):                      # returns an open AsyncConnection (the lease) or raises
    conn = await psycopg.AsyncConnection.connect(host=..., port=..., user=..., password=..., dbname=...,
                                                 connect_timeout=5, prepare_threshold=None, **url.query)
    try:
        await conn.execute("SELECT set_config('idle_in_transaction_session_timeout','0',true)")   # same as today
        await conn.execute("SELECT set_config('lock_timeout','5000ms',true)")                     # transaction-local: the pooler discards startup options
        await conn.execute('SELECT pg_advisory_xact_lock(%s)', (key,))                            # blocks server-side, FIFO
        return conn
    except psycopg.errors.LockNotAvailable:                      # SQLSTATE 55P03 -> same 409 and message as today
        await conn.close(); raise HTTPException(409, 'Your account is saving another step. Wait and retry.')
    except BaseException:
        await asyncio.shield(conn.close()); raise
```
and `await asyncio.shield(lease.close())` in the existing `finally`. Everything else (exempt path, identity verification, scoped repository, GET bypass) is unchanged.

**Preserved:** same advisory key and *transaction-scoped* lock (works in both Supavisor session and transaction modes); lease held for the whole request and released on success, exception and client disconnect; per-account exclusion across workers; 409 status/message; `idle_in_transaction_session_timeout=0`; repository revision compare-and-swap (not involved).
**Changes (need sign-off):** timeout becomes exactly ≈5 s (today: `100 × (50 ms + attempt)`); waiters are served roughly FIFO; each *waiter* holds one pooler connection while waiting (today it churns short ones).

## 6. Risks
1. **Pooler behavior (highest).** Production uses Supabase's Session pooler. A blocking wait holds a pooler client connection plus a server connection for ≤5 s per waiter; the pooler's session-mode limit (plan-dependent, e.g. "max clients reached") bounds concurrent waiters. Mitigate with option 4 (coalesce in-process so DB waiters are normally 0) and/or a cap on concurrent waiters that returns the same 409. **Must be validated through a real Session pooler on a non-production Supabase project**, and through PgBouncer (session and transaction modes) locally.
2. **Startup options are discarded by the pooler** (already learned in AUD-20) — hence `set_config(..., true)` inside the transaction; never `options=-c lock_timeout…`.
3. **Cancellation edge cases** around the connect/execute boundary (client disconnect while connecting). Needs targeted tests and `shield` on close.
4. **Event-loop policy:** psycopg async needs a selector loop; fine on Linux/Render/WSL (uvicorn default), not on a Windows ProactorEventLoop dev setup — document.
5. **Auth-path change**: must keep the AUD-68 exemption and all 65 `test_auth_lease_exemption` cases green; any regression is a security regression.
6. **Behavior change in timeout** (5 s exact vs RTT-dependent) — communicate; arguably a bug fix.
7. This fixes the *waiting* pathology only. It does **not** reduce the 118 sync statements per turn (§7).

## 7. Out of scope but important: synchronous repository calls
Since every handler calls the sync repository directly, the loop is blocked for every statement. Options, from cheapest: (a) measure RTT/statement time in production (telemetry, XS); (b) co-locate the database with the API (ops, possibly the biggest win); (c) cut statements (e.g. 45 in `/assess`: repeated `repo.profile()`/`lesson()` reads; S–M per route, behavior-preserving, test-heavy); (d) convert DB-only routes to plain `def` handlers (FastAPI runs them in a threadpool; S per route, but most routes interleave awaits); (e) an async repository (L–XL). Recommend a separate issue after step 0 shows the real numbers.

## 8. Test requirements for the eventual implementation
1. All existing lease tests unchanged and green: `tests/test_auth_lease_exemption.py` (exempt route, near-miss paths, every other mutating route queues, reads never lease) and `tests/test_telemetry.py::test_telemetry_report_never_waits_for_the_account_lease`.
2. **Exclusion across workers:** two app instances (two event loops) on one Postgres; concurrent mutating requests for one account never overlap; different accounts overlap.
3. **Timeout:** with the lease held, a waiter returns 409 with the identical body after 5.0 s ± 0.5 s and leaves no connection (`pg_stat_activity`).
4. **Hand-off/FIFO:** release → next waiter proceeds in < 50 ms; arrival order preserved.
5. **Cancellation:** client disconnect while waiting, while connecting, and mid-request releases the lock and the connection; the next request proceeds immediately; no unretrieved task exceptions.
6. **Loop-lag regression:** with 20 waiters, ticker p99 < 25 ms (the experiment script as a test; generous bound).
7. **Failure mapping:** connect refused / auth failure behave as today (error response, no hang); pooler exhaustion yields a defined 409/503, never a hang.
8. **Pooler matrix:** PgBouncer session and transaction mode in CI or a documented manual gate; one run against a non-production Supabase Session pooler.
9. **Lease lifetime:** held until the response is complete on success, 4xx/5xx and exception paths.
10. **Revision guarantees:** the existing stale-revision tests (`test_per_learner_locking`, lifecycle suites) pass unchanged.
11. Run the full suite on SQLite and PostgreSQL, with `AUDLI_PER_LEARNER_LOCKS` both unset and true.

## 9. Complexity and sequencing
| Piece | Size |
|---|---|
| Step 0: production telemetry for lease-acquire time and DB statements/time per request | XS (0.5 day) |
| Recommended fix (option 3) + tests + local pooler matrix | S–M (1–2 days) |
| Real Session-pooler validation (needs a non-production Supabase project) | S, needs owner access |
| Optional in-process coalescing (option 4) | S–M (1 day) |
| Reducing statements per turn / async repository | M per hot route … L–XL |
Suggested order: step 0 → option 3 (+ pooler validation) → decide on option 4 → separate issue for the repository.

## 10. Decisions requested
1. Approve the direction (option 3) to be implemented later, or prefer a stop-gap (option 5) first?
2. Accept the timeout becoming an exact ≈5 s?
3. Provide a non-production Supabase project/pooler for validation and tell us (or let telemetry measure) the production database region.
4. Agree to a separate issue for synchronous repository calls once step 0 produces real numbers.
