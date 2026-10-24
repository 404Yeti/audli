# AUD-84 P5 — `/conversation/ready` as a state-only update (flag OFF by default)

Flag: `AUDLI_DB_STATE_ONLY_TRANSITIONS` (default **false**). Reversible by unsetting it; no schema, data or migration change. Independent of the instrumentation PR. **No other round-trip phase (P1-P4), account lock, authentication, endpointing or region is touched.**

## What changes
`POST /api/exercises/{id}/conversation/ready` loads the conversation, sets `state = READY_FOR_NEXT` and saves it. Today the save is `write_conversation`, which rewrites the entire conversation on every call: ownership selects, the conversation row, a "prior" select plus upsert for every follow-up and every turn, the assessment, and one `INSERT` per evidence unit. With the flag on, the handler calls the new `save_conversation_state`, a single `UPDATE conversations` for this learner's exercise with exactly the values `write_conversation` gives the row (`data`, `state`, `pending_attempt_id`, `active_followup_id`, `updated_at`). The response, the lock, the ownership scoping and every other step of the handler are unchanged.

**Why skipping the child rows loses nothing.** The children are written in the same transaction as the row, from the same object (tables `conversation_followups`, `conversation_turns`, `turn_assessments`, `assessment_evidence` exist since schema 001). The old code re-upserts identical values, preserves `created_at` and uses `ON CONFLICT DO NOTHING` for the assessment, so for a stored conversation the rewrite is a no-op. The tests below prove it on real data rather than assuming it.

**Unusual cases fall back to the unchanged full save** (so behaviour, including errors, is exactly as before): a pending attempt (needs the attempt-ownership check), a missing conversation row (the full save re-inserts it), an exercise that is not this learner's (the full save raises the same `Exercise not found for this learner`).

## Proof of equivalence (`tests/test_state_only_ready.py`, SQLite and real PostgreSQL 17)
1. **Golden dump:** the same deterministic lesson (ids, provider and clock-free) runs against two fresh databases, flag off and on. Before the readiness call, after it, and across **every row of every table** the normalised dumps are identical, as are the readiness response and later reads (`/conversation`, `/lessons/current`, `/profile`, `/history`).
2. **Only the conversation row changes:** with the flag off *and* on, the only table that differs before vs after `ready` is `conversations`, and within it only `state` and `data` (the state inside the JSON). So the old full rewrite changed nothing else.
3. **Fallbacks:** pending attempt, missing row, other learner's exercise, each checked against the full path.
4. **Handler wiring:** with the flag off the handler never calls the new method; with it on it calls it exactly once.
5. **Concurrency:** four threads interleaving state-only and full saves leave one valid state and intact child rows. In production the readiness request still runs under the same asyncio lock and account lease as before.
6. **Default:** `Settings().db_state_only_transitions is False`.

## Measured effect (real PostgreSQL 17 behind a 75.6 ms round-trip delay proxy; the production link is 72 ms)
Same deterministic lesson, three runs per flag state, each on a fresh database (`scripts/measure_ready_latency.py`, `scripts/pg_delay_proxy.py`).

| `POST /conversation/ready` | Flag off | Flag on | Change |
|---|---|---|---|
| Statements | 31 | **14** | -17 |
| Pool checkouts | 14 | 14 | 0 |
| Transactions | 14 | 14 | 0 |
| Modelled round trips (checkouts + 2 x transactions + statements) | 73 | **56** | -17 |
| Wall time (mean of 3, identical to the millisecond across runs) | 5,555 ms | **4,254 ms** | **-1,301 ms (-23%)** |
| Round trips x 75.6 ms (model) | 5,519 ms | 4,234 ms | model within 1% of measured |

No other route's statement or checkout count changed; the whole lesson's wall time moved from 57.3 s to 55.9 s (one `ready` call). In production's 72 ms link the saving is about 17 x 72 = **1.2 s once per exercise**, on the wait after the feedback audio finishes. Production measured this request at 6.75 s and 6.79 s.

**What P5 does not fix:** the remaining 14 statements and 14 checkouts (11 are the reads that build the response). Reducing those needs P1 (pings), P2 (read-only autocommit) and P4 (repeated reads), which are not part of this change. My earlier estimate that `ready` would fall from 73 to 5 round trips was wrong because it ignored those reads; the proposal document has been corrected.

## Risks
* A conversation whose child rows were never written (none known: schema 001 includes them and every save writes them) would not be back-filled by a `ready` call as it was before. Mitigation: the fallbacks above, and the flag.
* The `UPDATE` has no version check, exactly like the upsert it replaces; concurrency protection is the existing lock and account lease.

## Rollback
Unset `AUDLI_DB_STATE_ONLY_TRANSITIONS` on Render (restart about a minute), or revert the PR.
