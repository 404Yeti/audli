# AUD-88 — State-only conversation checkpoints (AUDLI_DB_STATE_ONLY_CHECKPOINTS, default off)

First step of `docs/AUD-88-ROUNDTRIP-PLAN.md`; extends the AUD-84 P5 technique (`AUDLI_DB_STATE_ONLY_TRANSITIONS`, unchanged and still off) to two more places.

## Problem (from the traces)
`write_conversation` rewrites the whole conversation (the row plus a `SELECT` and an upsert for every follow-up and turn, the assessment and every evidence unit) on every save. Two saves on the turn's critical path change nothing but the conversation's state and its pending attempt id:
* the upload (`POST /exercises/{id}/attempts`) marks the new attempt as pending: 16 of its 18 write statements on the final turn;
* the assessment checkpoint (`prepare_assessment`, "assessing this answer" saved durably before the provider call): 18 statements on the final turn.

## Change (flag on)
`update_conversation_checkpoint` performs ONE `UPDATE conversations` with exactly the values `write_conversation` gives the row (`data`, `state`, `pending_attempt_id`, `active_followup_id`, `updated_at`). Ownership of the exercise, and that the pending attempt belongs to this exercise, are conditions of the same statement. When exactly one row is not updated (missing row, foreign exercise, foreign pending attempt) the caller runs the unchanged full save and raises the same errors. `prepare_assessment(..., checkpoint_only=True)` keeps the attempt update and the conversation write in one transaction. The child rows are already stored exactly as the rewrite would write them (they are written from the same object, `ON CONFLICT DO NOTHING`/identical upserts), so skipping them loses nothing; follow-up creation, turn appends and completion still use the full path. `/api/health` additionally reports the effective booleans of the new and offload/lazy-ping flags.

## Proof (`tests/test_state_only_checkpoints.py`, SQLite and real PostgreSQL)
* A complete lesson with the flag off and on, with and without `AUDLI_DB_OFFLOAD`: after EVERY request the status, the response and the dump of EVERY table are identical.
* On a conversation with real children (follow-up, turns, assessment, evidence): the full rewrite changes only the `conversations` table, the checkpoint changes only that table, and reloading returns exactly the saved object (transcripts, turns, assessment intact); the two paths give identical databases on twin runs.
* Fallbacks: a pending attempt of another exercise and another learner's exercise raise the same `ValueError` with nothing written; a missing conversation row is re-created.
* Atomicity: if the conversation write fails inside `prepare_assessment`, the attempt's confirmed text is rolled back too; a lost-response retry is idempotent; the checkpoint-mismatch guard is unchanged.
* Wiring (flag off never calls the new paths), statement counts drop on both routes and never rise elsewhere, and eight threads interleaving checkpoint and full saves leave one valid state.
* The existing suite is run with the flag at its default and forced on.

## Measured
Trace of the final turn (`scripts/trace_route_queries.py`, real PostgreSQL): upload 23 to 8 statements (27 to 12 round trips), assessment 65 to 50 statements (69 to 54 round trips): **30 round trips, 2.2 s at 72 ms, on the final turn**; the first turn of an exercise has few children to rewrite so saves little. 
Synthetic benchmark (`scripts/bench_turns.py`: real app, PostgreSQL behind a 72 ms link, 20 sequential and 32 concurrent turns; P2 on in every row):

| Configuration | One learner p50 / p95 (s) | Four learners p50 / p95 (s) | Statements (whole run) | Assessment route statements |
|---|---|---|---|---|
| baseline (current `main`) | 18.6 / 20.8 | 51.3 / 66.8 | 6049 | 48.0 |
| + checkpoints | **17.3 / 18.5** (-1.3 s) | 45.4 / 58.9 | **5131** (-15 %) | **39.5** |
| offload + lazy ping | 14.6 / 16.2 | 16.1 / 18.0 | 6049 | 48.0 |
| offload + lazy ping + checkpoints | **13.2 / 14.1** (-1.4 s) | **14.0 / 16.2** (-2.1 s) | 5131 | 39.5 |

The saving is additive with the offload and lazy-ping flags. It averages 1.3 to 1.4 s per turn (the trace's 2.2 s is the final turn of an exercise; the first turn has little to rewrite). Together with the offload and lazy ping the server-observed turn is 13.2 s; the remaining plan items in `docs/AUD-88-ROUNDTRIP-PLAN.md` are what stand between that and 10 s.

## Risks and rollback
A conversation whose child rows were never written would not be repaired by these saves (same caveat as P5; schema 001 always writes them and every full save repairs them). Rollback: unset the flag or revert.
