# AUD-40 production preflight

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

The final release review stopped because production revision
`0a2f515ee2d987bd3304af5d768df6f6d5117666` accepts only schema 001–003 and cannot
restart after 004. Production was verified on ledger 001–003; no migration or
deployment proceeded. The three planner behavior fixes remain a separate local
implementation and are not included in this bridge.

The focused compatibility candidate is based on that exact production revision.
It accepts only schema 003/004 ledgers and preserves optional planner lesson
metadata without activating planning. See [bridge certification and controlled
transition](AUD-40-SCHEMA-BRIDGE.md) for verification and rollback details.

Local checks passed: full SQLite/PostgreSQL backend **1332 passed, 16 skipped**;
schema-004 selection **369 passed, 15 skipped**; final focused startup/lifespan
checks **10 passed, 9 skipped**; browser **137 passed, 1 native-media-fixture skip**;
frontend units, lint, TypeScript, production build and diff whitespace checks.
All provider/identity calls are mocked. No paid provider calls occurred.

This local certification does not complete production preflight: deployment of
the bridge on 003, production health, verified backup restoration, migration
compatibility on restored data and post-004 bridge health remain mandatory future
gates. The full planner release needs a separate focused PR and fresh regressions.
AUD-40 remains In Progress; no production release is claimed.
