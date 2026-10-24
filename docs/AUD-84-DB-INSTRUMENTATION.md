# AUD-84 — Database work instrumentation and regression budgets (measurement only)

First step of the round-trip reduction plan ([proposal](AUD-82-DB-ROUNDTRIP-DESIGN.md), PR #85). **No behaviour, pooling, authentication, lock, region or endpointing change.** Everything here runs only when `AUDLI_LATENCY_AGGREGATES=true` (already on in production); with it off nothing is installed and the new test proves nothing is recorded.

## What is added
| Signal | Where | Meaning |
|---|---|---|
| `routes` object in `Latency summary` lines | per route template, e.g. `POST /api/attempts/{attempt_id}/assess` | calls, mean and max **statements, pool checkouts and transactions** per request. Bounded to 128 routes (overflow joins `other`); names must match the stage-name pattern, so concrete paths and ids can never appear. |
| `db.commit`, `db.rollback` | stages | Time the driver spends on `COMMIT`/`ROLLBACK`, including the **reset-on-return rollback after a read**. On an idle connection the driver returns immediately, so the counts show how many were real round trips and the times show what each cost. Also `lease.commit`/`lease.rollback` for the lock engine. |
| `req.read.txn_end`, `req.write.txn_end` | stages | The same time summed per request. `req.*.unattributed` now **excludes** it (it was previously hidden inside `unattributed`), so `unattributed` is smaller than before for the same traffic. |
| `loop.lag` | stage | How late a 100 ms timer fires: time the event loop was blocked by synchronous database calls or CPU work. Its `max_ms` and upper-bound quantiles show stalls (earlier evidence: a 50 ms sleep took up to 3.1 s). Ten wake-ups per second. |
| Summary format | each line still carries a `stages` object; route lines are additional parts | existing readers keep working; `part`/`parts` numbering covers the route lines. |

## Privacy
Numbers and route templates only. No SQL text, bind parameters, tokens, identifiers, concrete URLs or error messages. Tests assert no UUID, SQL keyword or `Bearer` appears in the emitted lines.

## Regression budgets (`tests/test_db_budget.py`)
A complete synthetic lesson runs over the real HTTP app with the instrumentation on, on SQLite and on real PostgreSQL in the PostgreSQL CI job. Each route's worst-case statements, checkouts and transactions must stay within a ceiling set to the values measured on 2026-10-24. Counts are deterministic, so a failure means the code now does more database work on that route (each statement or transaction is about 72 ms in production). A second test fails when a ceiling has more than 3 statements of slack, so budgets get ratcheted down when code gets leaner (the P5 change lowers the `/conversation/ready` ceiling in its own PR). A new route fails the test until it is given a budget.

| Route | Statements | Checkouts | Transactions |
|---|---|---|---|
| `POST /api/attempts/{id}/assess` | 65 (PG) / 67 (SQLite) | 21 | 21 |
| `POST /api/exercises/{id}/conversation/ready` | 31 / 32 | 14 | 14 |
| `POST /api/lessons/{id}/exercise` | 21 / 22 | 12 | 12 |
| `POST /api/exercises/{id}/attempts` | 23 / 25 | 7 | 7 |
| `POST /api/exercises/{id}/coach-audio` | 15 / 16 | 11 | 11 |
| `GET /api/exercises/{id}/conversation` | 11 | 11 | 11 |
(the full table is in the test)

## Reading it in production
`Latency summary` lines now answer "which route is doing the database work?" directly: compare a route's `queries_mean` and `checkouts_mean` with its `http.*` time. Multiply statements, checkouts and twice the transactions by the measured round trip (`db.pool_ping` mean, ~72 ms) to see what the database link costs that route.

## Rollback
Unset `AUDLI_LATENCY_AGGREGATES` (or revert the PR). No schema or data involved.
