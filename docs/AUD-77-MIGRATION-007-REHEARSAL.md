# Migration 007 rehearsal (AUD-77), 2026-10-23

**Nothing here touched production.** The rehearsal ran on a disposable PostgreSQL 17.10 bound to loopback, on a synthetic dataset built by the real application code. Production is still at schema version 6, migration 007 is unapplied, and `AUDLI_REVIEW_IMPROVEMENT_ENABLED` is off. This document is evidence for an approval decision, not an approval.

Tooling: `scripts/migration007/rehearse.py` (30 recorded steps; any failure exits non-zero). Result: **30 passed, 0 failed.**

## Method
1. **Dataset.** `scripts/migration006/make_fixture.py` builds a production-shaped schema-5 database by running the real application code (60 learners with awkward lesson histories), then `docs/postgres-006.sql` is applied exactly as checked in, giving schema 6. Synthetic exercises and attempts are added for review rows to reference. Result: 25 tables, 176 `lesson_lifecycles`, 51 `lesson_entitlements`, 60 users, 12 exercises/attempts, versions 1-6.
2. **Fingerprint.** For every table: row count and an md5 over its rows in a stable order.
3. **Backup and verified restore.** `pg_dump -Fc` (660 KB, 25 data entries), restored into a new database; counts and md5 of every table equal the source and versions are 1-6. The only tolerated `pg_restore` message is the dump's own `CREATE SCHEMA public`; any other restore error fails the step. All further work uses this restored copy.
4. **Apply 007** with the production timeouts (`lock_timeout 5s`, `statement_timeout 30s`) via `psql` with `ON_ERROR_STOP`: 0.04 s.
5. **Behaviour, rollback and recovery** as listed below.
6. **Application gates** run against restored databases with the real `create_app`.

## Findings
| Requirement | Evidence | Result |
|---|---|---|
| 007 applies | versions become 1-7 in 0.04 s on the restored copy | pass |
| Existing lesson and entitlement data intact | every pre-existing table (incl. `lesson_lifecycles`, `lesson_entitlements`) has identical rows by md5; the only other change is exactly one new `schema_migrations` row (version 7) | pass |
| New table is correct | exactly the 14 expected columns; row-level security on; no grants to PUBLIC, `anon` or `authenticated` (`has_table_privilege` false for both roles, which were created on the rehearsal server so the REVOKE branches ran) | pass |
| Constraints | valid rows insert (ready, repeated failed/abandoned/rejected history, a pending); rejected with the right SQLSTATE: second live row on one exercise (23505, partial unique index), ready next to a pending (23505), ready without text (23514), unknown status (23514), unknown learner (23503), unknown exercise (23503); both indexes exist | pass |
| Re-applying 007 | fails and changes nothing (atomic) | pass |
| **Rollback refuses a populated table** | no authorization, wrong-case phrase, partial phrase: each refused with "refusing to drop it without explicit destructive authorization"; table, all 7 rows and version 7 intact afterwards | pass (after the fix below) |
| Authorized rollback | with `SET audli.allow_destructive_rollback = 'DROP POPULATED review_improvements'` in the same session: table dropped, versions 1-6, every other table equals the pre-007 fingerprint | pass |
| Discarded rows recoverable | table-level `pg_dump -t` taken before the rollback; after re-applying 007 the data-only restore reproduces the 7 rows exactly (md5) | pass |
| Empty table | rolls back with no authorization | pass |
| Restoration | a post-007 dump restores exactly (table, rows, versions 1-7); the original pre-007 backup still restores exactly to schema 6 | pass |
| Application gates | schema 6 + flag on fails closed with "migration 007"; schema 6 + flag off starts; schema 7 + flag on starts; `require_lesson_limit_schema()` (AUD-47) passes at both 6 and 7 | pass |

## Defect found and fixed
The checked-in `docs/postgres-007-rollback.sql` **dropped the table unconditionally**, so it would have discarded populated rows. The rehearsal's mutation test (restoring the old file) fails exactly the three "REFUSES" steps, which shows the check can fail and that the old file did not meet the requirement. The rollback is now generated with a guard (`app/storage/migrations.py`, regenerated into `docs/postgres-007-rollback.sql`, pinned by tests): it counts rows first and aborts the whole transaction unless the exact phrase is set for the session. A PostgreSQL test exercises six wrong or missing phrases and then the exact one. The message avoids `%`, which psycopg and SQLAlchemy treat as a parameter marker.

## What this does not prove
- It is not Supabase: no pooler, no real `auth` schema, and `anon`/`authenticated` are plain roles. The migration-006 procedure documented the Supabase session pooler (port 5432) and sending session settings as `SET` commands rather than connection options; this rehearsal applies SQL the same way (`SET` commands in the same `psql` session), but it did not go through a pooler.
- Table sizes are small (176 lessons); lock behaviour under production traffic is not exercised. 007 only creates a new table, so it takes no lock on existing tables.
- `homework_*`, `learner_roadmaps` and `conversation*` tables are empty in the fixture. 007 does not touch them, and the account-deletion work (PR #68) covers them separately.
- The restore was verified from a dump made by the same tooling. A production backup must be taken and restored by the owner's own process before the production apply.

## Proposed production procedure (not executed, needs separate approval)
Same gates as migration 006 (`scripts/migration006/`): read-only preflight (versions exactly 1-6, table absent, no long transactions) -> `pg_dump -Fc` backup with counts from one snapshot -> verified restore into a scratch database -> this rehearsal against a restore of that backup -> apply `docs/postgres-007.sql` in one transaction after the fingerprint, file hash and confirmation phrase match. The application at `aee7693` already accepts schema ledgers [1..6] and [1..7], so no deploy is needed, and the feature stays off until separately enabled. No script rolls back or restores production automatically.

Run it again: start a disposable loopback PostgreSQL 17, export `AUDLI_REHEARSAL_DATABASE_URL`, then `python scripts/migration007/rehearse.py --learners 60 --out report.json`. It refuses to run if `AUDLI_PROD_DATABASE_URL`, `AUDLI_DATABASE_URL`, `DATABASE_URL` or any `SUPABASE_*` variable is set, or if the host is not loopback.
