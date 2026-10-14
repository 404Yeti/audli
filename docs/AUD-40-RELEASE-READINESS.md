# AUD-40 release readiness

The schema bridge is a compatibility-only candidate, based on production
`0a2f515ee2d987bd3304af5d768df6f6d5117666`. The full adaptive planner is not released
or enabled by this change. No merge, production migration, deployment or paid
AI/TTS calls are authorized in this task.

See [schema bridge evidence and rollback instructions](AUD-40-SCHEMA-BRIDGE.md).
The exact tested candidate commit and check results are recorded in its review PR
and Linear AUD-40 after all local checks pass. Future release must deploy the
bridge on 003, verify health, restore-test a production backup, apply 004, verify
bridge health on 004, then deploy and enable the separately verified planner.

Completed local verification: **1332 backend passes**, **369 schema-004 regression
passes**, **10 final focused startup/lifespan passes**, **137 browser passes**;
frontend unit tests, lint, TypeScript, production build and diff checks passed.
Backend skips are backend/version-specific; the separate native-media browser
fixture was skipped. No production backup/restore or live-provider acceptance is
claimed. Full test evidence and limitations are in the bridge document.

After 004, the old revision `0a2f515` is unsafe. The certified bridge backend plus
its matching frontend is the application rollback target; preserve schema 004,
history and roadmap state. Prefer the full application's tested planner-disable
setting if that alone resolves a planner failure. Production certification,
restore evidence and real-user/provider scenarios remain unverified here.
