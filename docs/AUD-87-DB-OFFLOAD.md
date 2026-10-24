# AUD-87 — Synchronous database work off the event loop (AUDLI_DB_OFFLOAD, default off)

## Problem
The repository is synchronous and each call crosses a 72 ms link, so a handler's database sections (tens of round trips) freeze the whole process: measured loop stalls of 3 to 4.5 s in production, 14 s in the 4-learner benchmark, health checks unanswered for 8 s, and every request of every learner delayed by every other.

## Change (flag on)
`app/offload.py` (`Offload.run`) runs a synchronous section in a worker thread and awaits it; with the flag off it calls the function inline. Applied to the lease middleware (lease attempt, scope check, lease close, which still runs if the request is cancelled) and to the hot turn routes: `attempts` (upload), `assess` (prepare / finish around the provider call), `coach-audio`, `ready`, `listened`, `GET conversation`, and the shared `speech()` helper. The code inside each section is unchanged, sections stay strictly sequential within a request, the request context (verified learner scope) is copied into the thread, and exceptions (404/409/422/429) propagate as before. Other routes are unchanged (lesson-phase routes remain a follow-up).

## Proof (`tests/test_db_offload.py`, SQLite and PostgreSQL)
Full lesson: identical responses and identical rows in every table with the flag on; the hot-route repository calls run on the loop thread with the flag off and never with it on; a 0.6 s synchronous section freezes a timer on the loop with the flag off (>0.5 s late) and not with it on (<0.3 s); errors keep their status codes; context travels into the thread; a cancelled request still closes the lease connection. The existing suite also passes with the flag forced on.

## Measured (baseline 18.7 s sequential / 51.1 s with 4 learners)
Sequential turn p50 **17.4 s** (-1.3 s). **4 concurrent learners: turn p50 51.1 s to 18.7 s, p95 66.2 s to 22.8 s; health p95 13.0 s to 120 ms; event-loop lag mean 578 ms to 9.7 ms.**

## Risks and rollback
Database concurrency rises from effectively 1 to the pool size (the application pool is 3 connections, 10 s wait; unchanged). Thread-safety relies on SQLAlchemy's per-call connections (the tests cover SQLite and PostgreSQL). Rollback: unset the flag or revert.
