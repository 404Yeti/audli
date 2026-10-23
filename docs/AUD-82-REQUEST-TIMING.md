# AUD-82 — Request overhead timing (authentication, pool, queries, lease)

Context: production (Render Singapore) talks to Supabase in **ap-northeast-1 (Tokyo)**. First real Starter data showed ~1.0-1.4 s per request outside provider and lock work, and 360-445 ms for single writes. This change adds **timing only**; no behaviour, authentication, pooling or region changes. It is active only when `AUDLI_LATENCY_AGGREGATES=true` (already set in production).

## Privacy
Stage names are code constants. No SQL text, parameters, tokens, user/learner identifiers, URLs or exception messages are recorded; output goes through the existing aggregate (`Latency summary` log lines, histogram upper bounds). Tests assert no token, SQL keyword, UUID or email appears in the emitted summary.

## New stages (all in `Latency summary`)
| Stage | Measures |
|---|---|
| `auth.verify` | The HTTPS call to Supabase Auth (`/auth/v1/user`) made on every authenticated request. |
| `auth.scope` | `for_learner`: pool checkout, ping and the account-existence read. |
| `auth.lease` | Whole account-lease acquisition on writes (connect + lock queries + any wait). |
| `auth.lease_connect` | Opening the lease connection (a **new** connection per write, `NullPool`). |
| `auth.lease_lock_query` | The `set_config` and advisory-lock statements. |
| `auth.lease_wait_sleep`, gauge `auth.lease_attempts` | Time and retries waiting because another request holds the account lock. |
| `db.checkout` | Whole pool checkout on the shared engine (slot wait + ping + any new connection). |
| `db.pool_ping` | The `pool_pre_ping` health check (one round trip per checkout of an existing connection). |
| `db.connect_physical`, `lease.connect_physical` | A brand-new database connection (TCP, TLS, authentication). |
| `db.query`, `lease.query` | Each statement's execution time. |
| `req.read.*` / `req.write.*` | Per request: `database` (queries + checkouts), `auth_verify`, `lease`, `unattributed`; gauges `db_queries` and `db_checkouts` (counts per request). |

`unattributed` = total request time minus the above. It still contains provider calls (STT, LLM, TTS), commit/rollback round trips, serialization and event-loop waiting, so **read it on GET routes** (no providers) to see pure overhead.

## What the code does today (read, not yet measured)
* **Auth is not cached.** Every protected request waits for a Supabase Auth call before the handler runs.
* **Pool:** `pool_size=3, max_overflow=0, pool_pre_ping=True`. Each `engine.connect()` pings an existing connection (one round trip) before the query; each repository operation opens its own connection, so a request with N operations pays about N pings plus N statements. `for_learner` adds a connection and a read on every request.
* **Writes open a new connection** for the account lease (`NullPool`): TCP + TLS + Postgres authentication, then two statements, held until the response ends.
* **Database calls are synchronous** inside async handlers, so each round trip also blocks the event loop for other requests.
* Commit/rollback is not timed separately; it shows up in `unattributed`.

## How to read it (needs real lessons)
1. Per-request: compare `req.read.database` and `req.read.auth_verify` for GETs with `http.GET /api/...` totals. With Tokyo-Singapore RTT near 70 ms, `db.pool_ping` and `db.query` medians near 70-150 ms confirm round-trip cost; `db.connect_physical` shows the handshake.
2. `req.write.lease` vs the write route totals shows what the per-request lease connection costs.
3. `db.checkout` much larger than `db.pool_ping` means new connections or waiting for the 3 pool slots.
4. `auth.lease_attempts` above 1 means account-lock contention.
Histogram quantiles are upper bounds; wait for n >= 30 per stage before quoting.

## Candidate fixes (not implemented; each changes behaviour and needs approval)
Cache verified identities briefly; drop or reduce `pool_pre_ping`; reuse one connection per request; replace the per-request lease connection; move Render or Supabase to the same region; async/threaded DB access. Choose after the numbers.
