# AUD-21: fresh plan reads and homework database access

Production measurements motivated this patch; all numbers below are local experiments, not production improvements. No migrations, settings changes, provider calls or TTL caches are introduced.

## Root causes and changes

- Homework listing queried content once per assignment. Fetch up to twelve owned assignments and their metadata with one bounded `IN` query.
- Discovery checked candidate existence and prior assignment per candidate. Batch the composite provider keys and owned content IDs; retain the profile/assignment locks, token check, conflict-safe insert, and single finalization transaction. `RETURNING` avoids post-write refetches.
- Decision preparation separately read assignment, content, lesson and profile. One owner-filtered join supplies fresh context. Reservation still validates authoritative state after transcription.
- Raw homework read connections were treated as potential writers by the claims hook, causing unnecessary fencing statements. The existing guarded `repo.read()` boundary marks actual SELECT-only access without removing write fencing.
- Async plan/homework handlers directly executed synchronous database calls. Use existing cancellation-safe offload for complete repository operations/transactions; provider waits remain outside connections.
- A settled plan GET need not acquire a durable writer receipt. In claims mode, attempt a fresh short projection under the existing profile lock and SELECT-only guard. Missing/changed plans and active/quarantined operations fall back to the existing guarded writer protocol. No authoritative mutation bypasses claims. No state survives the request.

A full response cache would risk stale progress/readiness. Presentation formatting itself adds no SQL, so no TTL cache is included. This also avoids cross-worker invalidation concerns. SQLAlchemy Core tables are used here; ORM eager-loading options would add no benefit.

## Comparable experiment

Run `scripts/benchmark_plan_homework_db.py` against disposable PostgreSQL through the existing `pg_delay_proxy.py` at 36 ms each way. Set `AUDLI_ROUTE_BENCH_ROOT` to baseline or candidate. Restricted LOGIN, schema 008, claims active, fixed mock STT/search waits, six synthetic accounts. See numeric artifact alongside this document.

| Operation | Queries before → after | HTTP mean before → after | DB time before → after |
|---|---:|---:|---:|
| Unchanged plan (five requests) | 40 → 9 | ~3.67 s → 0.99 s | ~3.26 s → 0.76 s |
| Cold plan creation | 42 → 46 | 3.85 s → 4.29 s | 3.42 s → 3.80 s |
| Homework creation | 36 → 35 | 3.36 s → 3.28 s | 2.96 s → 2.88 s |
| Spoken homework acceptance | 70 → 53 | 6.97 s → 5.37 s | 5.77 s → 4.32 s |

Database time includes client/network round trips; this experiment does not separate server execution from transport. Fixture counts differ from the reported production 34/65 homework counts. Peak heartbeat delay fell from 3213 ms to 22 ms; observed SQL on the event-loop thread fell to zero across these routes. Peak checkout remained one, provider-entry checkouts zero, final checkouts zero. This is serial load, not a capacity claim.

The fresh projection succeeded on five of six plan requests. This is not a cache hit rate. Cold/dirty plans pay an extra probe, and plan history still scales with completed lessons. Existing planner lock/statement timeouts remain intact; no timeout increase or contention waiver.

## Review and rollout

Review separately from coach-audio PR #117. The shared bridge edits affect different sections. Required CI and PostgreSQL claims, cancellation, account isolation, rollback, planner, homework and query-budget tests must pass before merge. Production rollout is not authorized by this patch. After separately approved deployment, compare real route query counts, loop stalls and completed homework state; local numbers cannot substitute for phone measurements. Roll back only to schema-008-compatible code.

Verification: 159 targeted SQLite/PostgreSQL cases passed, including packaged claims/cancellation/rollback, dirty and active-claim plan handling, account isolation, provider connection release and bounded homework queries. The initial unsafe joined profile/roadmap read was rejected after the existing concurrent check-in regression failed; the final implementation reads roadmap state only after obtaining the profile lock.
