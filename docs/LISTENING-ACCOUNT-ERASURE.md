# Audli 2.0 account erasure (AUD-104)

Account deletion (`POST /api/account/delete`, AUD-34) now also erases everything Audli 2.0 holds about the learner. **Still inert unless
`AUDLI_ACCOUNT_DELETION_ENABLED` is on**; this change does not enable it and it needs its own verification (below).

## What is erased (`app/listening/erasure.py`)
Attempts, answers, responses, spoken-answer records, any recording still stored, jobs, lesson requests, and **every lesson the learner generated**: versions,
exercises, keys, rubrics, audio records and the audio objects in private storage. Shared library lessons and other learners are never touched. Aggregate
spending counters are not learner data and stay.

## Order and failure handling
1. Database rows first, inside the deletion transaction. 2. Then the stored objects (deletion is idempotent). 3. If any object cannot be deleted, or if
anything is unexpected (another account's activity on the lesson, an object name outside its store's prefix), the whole transaction rolls back: the account
and all its data remain and the learner can retry. The only remaining window (objects deleted, final commit fails) leaves a lesson whose audio is missing,
never audio without an owner; a retry completes it. Nothing is deleted when 2.0 data exists but no eraser is configured.

## Database change: migration 013 (additive, applied only by operator opt-in)
`docs/postgres-013.sql` adds `listening_erasing_lesson(uuid)` and replaces three guard functions. Published lessons stay immutable and audio append-only, except
that a lesson can be deleted inside a transaction that sets `audli.erasing_owner` to that lesson's own owner. No table or data changes. Rollback SQL drops it.
The API role also needs DELETE on the 2.0 tables (`docs/postgres-listening-api-role.sql`, regenerated).

## Before enabling (owner steps)
1. Back up; apply `python -m scripts.migrate_postgres --listening` (applies 013). 2. Re-run `docs/postgres-listening-api-role.sql` as the owner if the API runs as that role.
3. Complete the other AUD-34 prerequisites (provider copies and backups cannot be deleted by this code; see docs/AUD-34-ACCOUNT-DELETION.md).
4. Verify with a disposable account that has a generated lesson and a recording: delete it, then confirm the lesson, its R2 objects and rows are gone and a second account is unaffected.
5. Only then switch the flag on and update the privacy page (which currently says generated lessons are not yet removed automatically).
