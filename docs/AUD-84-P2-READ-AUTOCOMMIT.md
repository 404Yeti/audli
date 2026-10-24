# AUD-84 P2 — read-only operations in AUTOCOMMIT (flag OFF by default)

Flag: `AUDLI_DB_READ_AUTOCOMMIT` (default **false**). Reversible by unsetting it; no schema, data or migration change. Independent of P5 and of the instrumentation. **P1 (pool ping), authentication, account locks, endpointing and database regions are untouched.**

## Why
On Postgres each read in a transaction costs more than the query. With psycopg 3 a `SELECT` first sends `BEGIN` as its own command, the connection is left **in a transaction** afterwards, so returning it to the pool sends a `ROLLBACK`. One row read = ping + `BEGIN` + `SELECT` + `ROLLBACK` = 4 round trips at 72 ms. Production confirmed the cost: `db.rollback` averaged 62 ms (max 72 ms) in the first window with the instrumentation. Checked directly on a real server: after a `SELECT`, the driver reports `INTRANS` in normal mode and `IDLE` in autocommit mode.

## Change
`SQLProgressRepository.read()` replaces `engine.connect()` in the 21 plain-read blocks of `repository.py` (including the per-request scope check in `for_learner`; the two startup schema checks keep a normal connection because they inspect the catalog). With the flag on **and** a Postgres server whose `default_transaction_isolation` is `read committed`, `read()` opens the connection with `isolation_level=AUTOCOMMIT`: no `BEGIN`, no closing `ROLLBACK`. The writes (`transaction()`), locking reads (`FOR SHARE`, the advisory-lock engine), the Auth call and every other module are unchanged.

## Safety
* **Isolation:** at READ COMMITTED each statement takes its own snapshot, so a multi-statement read already sees data committed between its statements. AUTOCOMMIT therefore returns exactly the same results. At startup the app checks `SHOW default_transaction_isolation`; on anything else (or on SQLite) the flag is **ignored** and logged (`AUDLI_DB_READ_AUTOCOMMIT IGNORED (...)`).
* **Guard:** a read connection rejects anything but a plain `SELECT`/`WITH`/`SHOW`, and rejects `FOR UPDATE`/`FOR SHARE`/advisory-lock/`set_config`/`nextval` statements (they would release instantly in autocommit). The guard is switched on for the **whole test suite on SQLite too**, so a mislabelled write cannot hide.
* **Clean pool:** SQLAlchemy resets the isolation level on return; tested that the next checkout is not autocommit, and that a failing write transaction is still all-or-nothing afterwards.
* **Concurrency:** unchanged. Reads take no locks; the account lease and asyncio locks are untouched.

## Proof (`tests/test_read_autocommit.py`, SQLite and real PostgreSQL 17)
1. A complete lesson with the flag off vs on leaves **identical rows in every table** and gives identical responses (`ready`, `/lessons/current`, `/profile`, `/history`, `/plan`, `/eligibility`).
2. Multi-statement read with a concurrent commit between the two statements: sees the new row in both modes; `SHOW transaction_isolation` is `read committed`.
3. A server with `default_transaction_isolation = 'repeatable read'` makes the feature refuse to start (flag ignored, logged); SQLite ignores the flag.
4. Driver state: `INTRANS` (normal) vs `IDLE` (autocommit); no leaked autocommit; scoped repositories inherit the mode.
5. Guard: INSERT/UPDATE/DELETE/DDL/`FOR UPDATE`/`FOR SHARE`/advisory lock all rejected, both modes, both backends.
6. Existing suite: see the PR for the runs with the flag at its default and forced on.

## Measured (real PostgreSQL 17 behind a 75.6 ms round-trip proxy; production is 72 ms), 3 lessons per state
`scripts/measure_flag_latency.py --flag db_read_autocommit` (first call of each route; wall time is the mean of 3):

| Route | Round trips off / on | Wall off | Wall on | Saved |
|---|---|---|---|---|
| `POST /attempts/{id}/assess` | 70 / 48 | 7,572 ms | 5,298 ms | 2,274 ms |
| `POST /conversation/ready` | 73 / 47 | 5,574 ms | 3,611 ms | 1,963 ms |
| `POST /lessons/{id}/exercise` | 57 / 35 | 4,399 ms | 2,731 ms | 1,668 ms |
| `GET /conversation` | 44 / 22 | 3,351 ms | 1,689 ms | 1,662 ms |
| `POST /conversation/listened` | 41 / 23 | 3,128 ms | 1,762 ms | 1,365 ms |
| `POST /exercises/{id}/attempts` | 31 / 21 | 2,901 ms | 2,130 ms | 770 ms |
| `POST /exercises/{id}/coach-audio` | 32 / 20 | 2,881 ms | 1,768 ms | 1,113 ms |
| **Three critical-path routes of a turn** | | **13,355 ms** | **9,196 ms** | **4,158 ms (-31%)** |
| **Whole synthetic lesson** | | **57,529 ms** | **37,731 ms** | **-19.8 s (-34%)** |

The proposal predicted 54.7 round trips, about 3.9 s, for the critical path; measured 4.2 s. Statement and checkout counts do not change, only the `BEGIN` and end-of-transaction round trips of read-only operations. Production should see roughly 4 s per turn.

## Risks
* **The server isolation level in Supabase is not verified from here.** The startup check decides at run time and the log line says what happened; confirm it on the first deploy before reading numbers.
* A Postgres pooler in *transaction* mode could reorder statements across clients; production uses the session pooler (port 5432). Autocommit is a client-side setting and does not depend on the pooler mode.
* Reads in modules other than `repository.py` (account, improvement, homework, Review) are not converted yet.
* Costs not modelled: Supabase commit time and pooler queuing.

## Rollback
Unset `AUDLI_DB_READ_AUTOCOMMIT` on Render (restart about a minute), or revert the PR.
