# AUD-20 schema compatibility bridge

This bridge uses only existing migration-002 storage. PostgreSQL startup accepts
exactly `[1,2]` or `[1,2,3]`, still validates every existing required table/column,
and rejects missing, malformed or future migration chains. A database containing
only migration 001 still needs 002 to support current lessons. SQLite remains on
its existing migration policy. The bridge does not apply 003 at startup or ship
an accent controller, flag, voice mapping or passage routing.

Typed snapshot fields preserve inactive AUD-20 lesson and exercise metadata
when reverting from the full AUD-20 application. Existing `suggested_accent`
snapshots remain readable. Unknown fields are still rejected. The bridge does
not act on stored accent consent, generate accent passages, or change provider,
authentication, assessment, difficulty or tutor-voice configuration.

## Verification

Isolated PostgreSQL 17.11: **20/20** full-migration upgrade/restart/bridge rollback
checks passed; see [AUD-20-BRIDGE-VERIFICATION.json](AUD-20-BRIDGE-VERIFICATION.json).
Synthetic learner rows were hashed before/after the complete 003 SQL and were
unchanged. Existing preferences, lesson retrieval, cached audio, transcript
gates and account isolation passed before/after restart. Control defaults were
disabled, daily allowance zero, reserved characters zero and concurrency two.
No production data or paid providers were used.

Focused SQLite/PostgreSQL bridge, persistence, authentication and lesson tests:
**241 passed, 1 SQLite-only PostgreSQL test skipped**. Full backend regression passed **485 tests, 1 PostgreSQL-only test skipped**
(the additional schema-3 assessment case was verified separately). `pip check`
passed; `npm audit --omit=dev` reported zero vulnerabilities. Public production
health/authentication-rejection baseline passed **19/19** without provider calls.

## Deployment and rollback floor

Deploy and verify this bridge while production is still at migrations 001/002.
Do not apply 003 until an appropriate restorable production backup is verified.
After 003, **this bridge release is the minimum rollback binary**. Record its
merged SHA and Render deployment before migration. The prior production SHA
`df5140ca4796a6102bcd5e3b91b402a91f511854` rejects schema 3 and must not be used
for rollback after 003. Do not delete learner rows or downgrade migration history.
Rollback means redeploying the recorded bridge revision, retaining all database
tables and provider/authentication settings. Keep the global accent flag disabled
and voice mapping empty in the subsequent AUD-20 release. Authentication and
saved passage/tutor playback must be verified without paid synthesis.

Production deployment, backup and migration results are recorded separately;
these local results do not establish production acceptance.
