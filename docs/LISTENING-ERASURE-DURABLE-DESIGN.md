# Durable, idempotent account erasure: proposal (not implemented; needs your approval)

## The remaining weakness in #138
#138 deletes the learner's stored audio first, then their rows. It never reports success early and never leaves the account half-deleted in the database, but object
deletion cannot be rolled back, so after a partial R2 failure a learner who then abandons the deletion can be left with generated lessons whose audio is gone ("broken lessons")
until they retry.

## Smallest safe fix: rows first, a durable key queue, an idempotent sweep
1. **One short transaction** (what the route already runs): plan the learner's object keys; `INSERT` them into a new queue table; delete the 2.0 rows and the account; commit.
   After the commit the learner and all their lessons are gone: nothing is visible, so nothing can be "broken". Before it, nothing changed.
2. **Sweeper** deletes each queued object (idempotent: a missing object is success), removes the queue row on success, and on failure increments `attempts` and retries with
   backoff. It runs (a) once right after the commit as a background task, (b) every 10 minutes and at startup inside the API process (which already holds both storage
   credentials; no new service, no change to the generator or spoken worker), and (c) as `python -m app.listening.erasure_sweep` for operators.
3. **Alert**: the existing ops health check reports any queue row older than 24 hours, and the count.

### Schema (migration 014, additive, operator opt-in)
`listening_erasure_keys(object_key text primary key, store text check in ('lessons','recordings'), enqueued_at, attempts int default 0, last_error text)`.
It stores random object keys only: no user id, no content. RLS enabled and revoked from `anon`/`authenticated` like the other 2.0 tables; the API role gets
SELECT/INSERT/UPDATE/DELETE on it. `ON CONFLICT DO NOTHING` on insert makes retries safe.

### Failure matrix
| Failure | Result |
|---|---|
| R2 down during the deletion request | deletion still **commits** (rows + queue). Learner is told: account and lessons deleted; stored audio files are removed in the background and retried until done |
| Process dies after commit, before the sweep | queue rows persist; the startup/10-minute sweep finishes |
| Sweep fails for one object | that row stays, retried with backoff, alerted after 24 h |
| Same deletion repeated | no-op (nothing left; keys already queued or gone) |
| DB failure | rolled back: nothing changed, nothing queued |
| Another learner / shared lessons | untouched: the plan only contains this learner's private keys (unchanged guard) |

### What changes in code (small)
`erasure.py`: `plan_erasure` stays; `erase_rows` also enqueues the planned keys; the objects-first step in `Eraser.prepare` goes away; add `sweep_queue(engine, stores)`.
`account.py`/`main.py`: start the sweep task and call it after a successful deletion. Tests: the failure matrix above on SQLite and PostgreSQL.

## Residual risks (honest)
* Between commit and a successful sweep, objects still exist in R2 (unreachable: signed URLs are only issued after an ownership check against rows that no longer exist). The
  privacy page must say deletion of stored audio is completed in the background and retried, not instant.
* A generation job running at the very moment of deletion could write an object after the plan. Mitigation: the job's publish step fails without its owner row; a reconciliation
  sweep for `lessons/` objects with no database row older than 24 hours is a later, optional step.
* R2 lifecycle (1 day on `listening-tmp/`) remains the backstop for recordings.

## Decision needed
Approve migration 014 + the in-process sweeper (replaces the objects-first step). Until then #138 stays unmerged and deletion stays off.
