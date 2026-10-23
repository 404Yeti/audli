# AUD-34 follow-up — permanent account deletion that covers the current schema

Status: **ready for review; not deployed.** `AUDLI_ACCOUNT_DELETION_ENABLED` is **off by default**: with it off there is no delete route, `GET /api/account/controls` reports `delete_available: false`, and the Settings page renders nothing for it. Merging this is inert; enabling it needs separate approval. This supersedes the deletion half of PR [#20](https://github.com/404Yeti/audli/pull/20) (the onboarding-reset half already exists on main as `app/onboarding_reset.py`).

## Why a new PR
PR #20 deleted through a hardcoded, child-first list written before migrations 004–007. Against current main that list omits `learner_roadmaps`, `homework_assignments`, `homework_reflections`, `lesson_entitlements` and (after 007) `review_improvements`. Each has a foreign key to `users`, `exercises`, `attempts` or `lesson_lifecycles` with no cascade, so one remaining row makes the deletion transaction fail on its constraint and the learner sees "Deletion could not be confirmed". PR #20 also had no check that its list covered the schema.

## How coverage is guaranteed now
`app/account.py` derives what to erase from `app.storage.schema.metadata`:
- Every table is erased unless it is in `schema.NOT_LEARNER_OWNED` (`schema_migrations`, `accent_control`, `accent_reservations`, `homework_discovery_budget`, `listening_content`: aggregate budgets, a shared catalog and the migration ledger; accent reservations are handled separately by hashed scope).
- A table's ownership predicate follows **NOT NULL** foreign keys to `users` (a nullable path would skip rows whose value is NULL; the first draft of this planner did exactly that for a turn's optional follow-up, and the new tests caught it).
- Tables are deleted in reverse foreign-key dependency order, `users` last.
- A table with no ownership path that is not classified raises at plan time: new tables cannot be forgotten silently.
- Tables that do not exist in the connected database (schema 3–6 databases have no 004–007 tables) are skipped.
- `tests/test_schema_ownership_inventory.py` pins the classification (21 learner-owned tables including `users`, 5 not) so changing the schema forces a reviewed decision.

## Concurrency
- `POST /api/account/delete` is a mutation, so it queues behind any in-flight mutation through the existing per-account lease and serializes with it; it then locks the Auth user row `FOR UPDATE`.
- First-request initialization takes `FOR SHARE` on that same Auth row (only when deletion is enabled) and refuses with 401 if the Auth user is gone, so a request verified just before commit cannot recreate the learner afterwards. Reads, writes and a replayed token all get 401 and create nothing (tested).
- The AUD-77 improvement route deliberately skips the lease. Foreign keys make that safe against deletion: its rows are erased with the account, a late completion updates zero rows, and a late claim fails on the missing exercise (tested).

## Also ported from PR #20 (unchanged behavior, all tested)
Fresh password reauthentication every time, bound to the verified owner; refusal for accounts with verified MFA factors (support-assisted deletion; no downgrade); live owned `auth.sessions` check on the JWT's session id; removal of refresh tokens, sessions, flow state and SCIM mappings; refusal if any Storage bucket exists; shared-audio-name and unreadable-cache refusals; validation errors never echo passwords.

## Verification (this PR)
SQLite and PostgreSQL 17: every learner-owned table seeded for two learners and counted with an independent SQL oracle; erasing one learner leaves zero rows of theirs, none of the other's changed, and no orphans (table totals equal the other learner's rows); rollback leaves everything intact; deletion order places every child before its parent; the naive order really violates foreign keys; schema-6 database shapes work; Auth/session/identity cleanup removes only the owner's; storage guard; foreign audio references; unreadable cache; in-flight improvement; resurrection attempts; flag off is inert; enabling requires Supabase Auth and PostgreSQL; reauthentication/MFA/session tests. Browser: dialog, exact confirmation, focus trap, failure recovery, invisible when unavailable.

## Remaining blockers (none are fixed by code in this PR; see PR #20's release review)
1. **Provider copies (AUD-36):** deleting the account does not delete provider-side copies. ElevenLabs retains request text and audio history by default and Audli discards generation IDs. OpenAI keeps abuse-monitoring logs for up to 30 days even with `store=false`; this now also covers AUD-77 language coaching text. The dialog says provider records may remain; it must not claim they are erased.
2. **Backups and restore:** operator dumps and Supabase backups have no verified expiry, and there is no independent deletion ledger, so a restore could resurrect a deleted learner (and their improvements). Reconciliation after any restore is required first.
3. **MFA support process** is unverified: only a contact route exists.
4. **Deployed-environment facts to confirm** before enabling: the backend database role can read/delete `auth.*` as in the 2026-10-12 inventory (repeat it against the current managed Auth schema), Storage still has zero buckets, one API instance and one worker, and old workers are drained so none lacks the lease/existence guard.
5. **Legal holds:** no Audli-specific retention obligation is configured; the owner must confirm none applies.
6. Enabling requires its own approval after the above: set `AUDLI_ACCOUNT_DELETION_ENABLED=true` (needs Supabase Auth and PostgreSQL), verify with a disposable account first.
