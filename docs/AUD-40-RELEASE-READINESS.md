# AUD-40 release readiness

## Controlled CI integration and bridge release — 2026-10-14

The owner now authorizes CI integration and bridge-only Render deployment after
all mandatory gates. Production must remain on schema 003; production backup/
restore, migration 004, full planner deployment and paid provider calls remain
outside this task.

Stage 1 passed: CI PR #28 merged as
`7747127b09468bea8bd68d0db6a8b24889dfa1e3`. All eight main push checks succeeded:
https://github.com/404Yeti/audli/actions/runs/38526767633.
The workflow is present on main. Security remediation and strict eight-check
administrator-enforced protection remain intact. Render's existing auto-deploy
is live at this CI merge (`dep-db7imcqvcj2c73e51rt0`); health reports the expected
revision with Supabase auth enabled. Backend source/requirements/Dockerfile have
no diff from pre-integration production. Read-only production PostgreSQL 17.11
verification confirms ledger [1,2,3] and no planner table.

Stage 2 candidate updates the bridge from this main commit. The only merge
conflicts were web/site Vercel configuration; both retain all preview-disable
entries. Bridge backend source, requirements and Dockerfile must remain identical
to certified `4e8514a18a5eb54b81a76d5e53f682c759d239c7`. All eight required checks
must pass again on the exact updated head before protected merge/deployment.
Deployment and final certification evidence will be recorded after those gates.

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
