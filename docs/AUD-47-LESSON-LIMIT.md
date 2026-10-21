# AUD-47 daily lesson limit — Phase 2 (ledger, atomic start, eligibility)

Status: code and tests only. **Migration 006 has not been applied anywhere and enforcement does not exist.**
`AUDLI_LESSON_LIMIT_MODE` accepts `off` (default) or `observe`; `enforce` is rejected by configuration until Phase 3.

## Policy
One lesson per verified learner per rolling 24 hours from the server-recorded start; 10 minutes of wall-clock lesson
time from the same start (enforced in Phase 3). Refresh, pause, reconnect, other devices and tabs change neither.

## Design: a one-row-per-learner ledger, not an exclusion constraint
`lesson_entitlements(user_id PK → users, last_started_at, next_eligible_at, last_lesson_id, voided_at, updated_at)`.

A PostgreSQL `EXCLUDE USING gist (… tstzrange …)` constraint is the right tool for an append-only log with many
rows per learner (it rejects overlapping ranges). It is the wrong tool for one mutable row per learner: the primary
key already guarantees a single row, there is nothing to overlap, and legacy history would have to be excluded by
predicates. The ledger therefore uses:

1. **Atomic claim** in the same transaction as the lesson insert: the profile row is locked, then one statement
   `INSERT … ON CONFLICT (user_id) DO UPDATE … WHERE next_eligible_at <= :start`. The window can only advance when it
   has already elapsed; a lost race updates zero rows and is refused.
2. **Database backstop** (`lesson_entitlement_guard`, BEFORE UPDATE trigger): moving `last_started_at` earlier than
   the stored `next_eligible_at`, writing a window that is not exactly 24 hours, or extending a window raises. It
   compares values on the row only, so it needs no clock and also stops raw SQL and buggy code paths.
3. **Durability**: no foreign key to lesson rows. `reset_onboarding` deletes open lesson rows and retention jobs may
   delete others; neither can touch the ledger.
4. **Existing learners**: migration 006 backfills each learner's latest `lesson_lifecycles.started_at`. Until then,
   and for lessons started by a release that predates the ledger, the effective window is the later of the ledger and
   the latest lesson row. Historical lesson rows are never modified.
5. **Failed starts**: the claim and lesson insert share one transaction, so a storage failure leaves no trace.
   `void_allowance(lesson_id)` returns the allowance only for the ledger's current, unvoided lesson at revision 0 and
   may be called only after a verified server/provider failure (wired in Phase 3, together with writing `expired`).
   Microphone denial, abandonment and client retries never reach it.

## Modes
* `off`: no ledger reads or writes; `/api/eligibility` reports `mode: off, eligible: true`. Production behavior is unchanged
  and the app starts against a database without migration 006.
* `observe`: starts record the ledger; a start inside the window is **logged** (`lesson_limit would_refuse learner=<hash>`)
  and not blocked, and does not extend the window. Startup fails closed if 006 is missing.

## GET /api/eligibility
Authenticated, read-only, provider-free, usable during cooldown. Fields: `mode`, `enforced` (false in this release),
`eligible`, `reason` (`eligible|cooldown|lesson_in_progress`), `server_time`, `next_available_at`, `retry_after_seconds`
(rounded up), `last_started_at`, `open_lesson{id,status,started_at,expires_at}`, `lesson_max_seconds`. All instants
are server UTC; the browser clock is never consulted.

## Migration 006 (docs/postgres-006.sql, generated and checked by tests)
Creates the ledger (RLS on, browser roles revoked), backfills, installs the trigger, adds `expired` to
`lesson_status_valid`, and recreates `one_open_lesson_per_learner` for `status IN ('active','paused')`.
Release order: bridge (PR #58) → apply 006 (separate owner approval) → this release with mode `off` → `observe`.
Rollback: `off` needs no schema change; the table and trigger are additive and can remain.
