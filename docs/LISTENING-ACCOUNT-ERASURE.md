# Audli 2.0 account erasure (AUD-104)

Account deletion (`POST /api/account/delete`, AUD-34) also erases everything Audli 2.0 holds about the learner. **Inert unless `AUDLI_ACCOUNT_DELETION_ENABLED` is on;
nothing in this change enables it.**

## What is erased (`app/listening/erasure.py`)
Attempts, answers, responses, spoken-answer records, any recording still stored, jobs, lesson requests, and **every lesson the learner generated** (versions, exercises,
keys, rubrics, audio records) plus the **audio objects** in private storage (lesson bucket and recordings bucket). Shared library lessons and other learners are never
touched. Aggregate spending counters are not learner data and stay.

## Three steps; no database transaction is ever held across storage calls
1. **Plan** (short read). List the objects that belong only to this learner. Refuse, changing nothing, if anything is unexpected (another account's activity on a private
   lesson, an object name outside its store's prefix).
2. **Objects** (outside any transaction, in a worker thread). Delete them; deleting is idempotent. Recordings first, then lesson audio; if any object in a store fails we stop
   before the next store. Any failure raises before the database is touched: HTTP 503, account intact, nothing reported as deleted.
3. **Rows** (the existing short deletion transaction with the account row lock). Re-derive the object list; if the learner now has objects the plan did not delete (a lesson
   finished generating, a recording arrived) roll back with "please try again". Otherwise delete the rows. Only a committed transaction returns `deleted: true`.

### Failure matrix
| Failure | Database | Objects | What the learner sees | Retry |
|---|---|---|---|---|
| Storage unreachable / one object fails | untouched | some already gone (irreversible) | 503, nothing deleted, try again | safe, idempotent |
| Unexpected data (foreign activity, odd key) | untouched | untouched | 503 with reason, contact support | after review |
| New data arrived mid-way | rolled back | already deleted ones stay deleted | 503, try again | safe |
| Commit fails | rolled back | already deleted | 503 | safe |
| Crash after objects, before rows | untouched | gone | next attempt is a fresh request | safe |
**The honest consequence:** object deletion cannot be rolled back, so after a partial failure some of the learner's generated lessons can have lost their audio until the retry
completes. The learner is never told the account was deleted unless it was. A durable cleanup queue was considered and rejected as unnecessary: no object is ever deleted
*after* the rows, so there is nothing left that only a queue could finish.

## Database change: migration 013 (additive, operator opt-in, no table changes)
`docs/postgres-013.sql` adds `listening_erasing_lesson(uuid)` and replaces three guard functions. Published lessons stay immutable and audio append-only, except that a lesson
can be deleted inside a transaction that sets `audli.erasing_owner` to that lesson's own owner. The API role needs DELETE on the 2.0 tables
(`docs/postgres-listening-api-role.sql`, regenerated). Rollback SQL drops the helper. 009-012 are untouched.

## CI
`tests/test_listening_erasure.py` runs on SQLite and on real PostgreSQL. A dedicated CI step runs it verbosely and **fails if the two PostgreSQL-only tests (guards stay strict;
real erasure through the triggers) are skipped or do not pass**.

## Recommended activation sequence (each step needs your approval)
1. Merge this PR (flag off, migration not applied: no production change; the code does not require 013 unless a learner is deleted).
2. Back up. Apply `python -m scripts.migrate_postgres --listening` (applies 013) in a quiet window; run `docs/postgres-listening-api-role.sql` as the owner if the API runs as that role.
3. Confirm health, then complete the other AUD-34 prerequisites (provider copies and backups are outside this code: docs/AUD-34-ACCOUNT-DELETION.md).
4. With the flag still OFF in production, verify on a disposable PostgreSQL copy or staging with an account that has a generated lesson and a recording: delete it; confirm the lesson,
   its R2 objects and rows are gone and a second account is unaffected; simulate a storage outage and confirm a clean retry.
5. Only then set `AUDLI_ACCOUNT_DELETION_ENABLED=true`, delete a real disposable production account, and update /privacy (it says generated lessons are not yet removed automatically).
