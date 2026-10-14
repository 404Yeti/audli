# AUD-40 schema compatibility bridge

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

Candidate based exactly on production commit
`0a2f515ee2d987bd3304af5d768df6f6d5117666`, branch
`fix/AUD-40-schema-compatibility-bridge`. This task authorizes local implementation,
isolated tests and a review PR only. No merge, production migration or deployment.
Both Vercel project configurations disable automatic deployments of this review branch
using the existing branch-specific mechanism, preserving the no-deployment boundary
([Vercel git configuration](https://vercel.com/docs/project-configuration/git-configuration)).
Read-only Render service inspection confirmed PR previews are off; automatic
deploy follows `main`. The bridge branch will not be merged in this task.

## Minimum compatibility change

The production adapter previously required the exact ledger `[1,2,3]`. This
bridge accepts only `[1,2,3]` and `[1,2,3,4]`. Missing/noncontiguous migrations and
future versions still fail closed. All existing required tables/columns are
validated. SQLite remains schema 3; startup performs no PostgreSQL migration.
The schema metadata, numbered migration runner and Docker migration list are
unchanged. Migration 004 is not required or shipped as an executable migration
in this bridge; its exact SQL is a test fixture only.

The strict lesson model also accepts the planner's optional `plan_topic` (maximum
160 characters) and `plan_number` (minimum 1). The fields survive checkpoint saves
but do not select content, adjust difficulty or grant accent consent. Unknown
snapshot fields remain rejected. This is needed to read/resume lessons saved
after the eventual planner release, rather than merely start on its database.

There are no planner modules, routes, roadmap reads/writes or check-in generation.
The bridge cannot activate planning, irrespective of environment configuration.
Existing authentication, lesson/assessment persistence, onboarding restart,
accent consent/voice routing and frontend code come unchanged from production.
Production startup uses no shared learner and does not initialize learner data.
Opaque roadmap snapshots, baselines, feedback and revisions remain untouched.

## Isolated verification

PostgreSQL 17.11 runs locally on test port 55441. Each repository fixture creates
a disposable database, applies 001–003, and drops that database after testing.
`AUDLI_TEST_POSTGRES_SCHEMA=4` applies the exact 004 fixture before application
startup. Fixture SHA-256:
`fced6e1053cffe371d159dd457589aaed37f2cbc8cd8c39c698b0c5fad469c3c`.

`tests/test_aud40_schema_bridge.py` verifies both actual schema versions,
authenticated-mode application construction/health, rejection of unauthenticated
learner requests, no planner endpoints, repeat startup with unchanged public rows,
opaque roadmap preservation, migration preservation of assessed lessons, and
resume of a checkpoint containing planner metadata. Incomplete and future ledgers
are rejected without changing rows. Existing regression suites verify lesson
completion, evidence/adaptation, transcript gates, identity isolation, onboarding
and accent consent/routing. AI/TTS and authentication are mocked; no paid calls.

The final certified commit SHA and completed check results are recorded in the
PR and Linear AUD-40 after verification. Certification is local compatibility
evidence, not production deployment or a restored-production-backup certificate.

Completed local checks: schema-004 regression selection **369 passed, 15 skipped**;
final focused actual FastAPI startup/shutdown and bridge tests **10 passed,
9 skipped** (SQLite does not exercise PostgreSQL startup); frontend unit tests,
ESLint, TypeScript and production build passed; browser suite **137 passed,
1 skipped**. The browser skip is the separate native-media acceptance fixture,
which was not run here. Backend tests exercise real API persistence with fake
speech/evidence, while ordinary browser fixtures mock API/media behavior.
Final full SQLite/PostgreSQL regression: **1332 passed, 16 skipped**. No failed
checks remain. Backend skips are backend/version-specific cases, exercised by
their matching PostgreSQL/schema runs. An earlier test run caught diagnostic
wording and a new test's incorrect pause endpoint; both were corrected and the
final full/selected/focused runs passed.
Diff review found no unrelated runtime changes or secrets. Whitespace checks pass
for every changed file except the byte-identical generated 004 SQL fixture, whose
original trailing spaces are retained to preserve the audited migration checksum.

Reproduce using a disposable local PostgreSQL administrator URL supplied through
`AUDLI_TEST_POSTGRES_URL`: run `pytest -q` for the full SQLite/schema-003 suite;
set `AUDLI_TEST_POSTGRES_SCHEMA=4` and run the bridge, lesson lifecycle, auth,
mutation lock, accent consent/practice/global release and onboarding-reset modules
for schema-004 coverage. Never use a production database for these fixtures.

## Controlled transition (future release authorization required)

1. Reconfirm the production revision, database target and main-branch auto-deploy
   configuration. Merge/deploy only this certified bridge while the ledger remains
   001–003; pin the resulting deployed commit and matching image/revision evidence.
2. Verify public health, server-verified authentication, logs and existing frontend
   behavior without paid provider calls. Stop on failure before any migration.
3. Create a private production backup using the established Audli procedure.
   Restore into isolated PostgreSQL and verify application users, profiles,
   lesson history, assessments, adaptation and authentication-related ownership.
   Apply the exact 004 to that restored database and repeat bridge startup/data
   preservation checks. Retain verified backup hashes and restore evidence.
4. Apply 004 explicitly as the backend owner only after those gates pass. Verify
   ledger 001–004, roadmap table protections and unchanged existing learner rows.
   Use the separately reviewed full-release migration runner or complete audited
   004 SQL; this bridge's unchanged migration runner knows only 001–003 and
   deliberately refuses a 004 ledger. It is not the command for applying 004.
5. Verify that the already deployed bridge remains healthy, including a controlled
   restart on schema 004. Certify the deployed bridge as the rollback target.
6. Run all full AUD-40 release gates on a focused release based on current main.
   Deploy the full backend with `AUDLI_PLANNER_ENABLED=false` first, plus its
   intended frontend, verifying their exact revisions and ordinary lesson health.
7. Enable `AUDLI_PLANNER_ENABLED=true` only after release gates pass. Verify owned
   roadmap persistence, migration-time activation baseline, check-in lifecycle
   and logs without paid AI/TTS; disclose remaining real-provider scenarios.
   The planned activation baseline is the migration timestamp: ordinary lessons
   completed during the bridge/disabled window after 004 count when the planner
   later reconciles them. Historical pre-migration lessons must remain excluded.

## Rollback by stage

- Before 004: use a certified bridge revision for rollback once the bridge has
  persisted lesson snapshots containing optional plan fields, including nulls.
  The older strict lesson reader rejects those fields even on schema 003. Returning
  to `0a2f515` or the CI-only merge requires verifying that no such snapshots were
  written; schema 003 alone is insufficient. Prefer a certified bridge revision
  and leave the database untouched.
- Backup/restore validation failure: stay on the healthy bridge and schema 003;
  do not migrate. Preserve production data and investigate the safety gate.
- Failed transactional 004: inspect the ledger/table state; the migration is one
  transaction. Keep the bridge running. Do not manually edit the ledger or delete
  records to conceal a partial state; invoke verified recovery only if needed.
- After committed 004: use the certified deployed bridge revision, never
  `0a2f515`. Leave 004, learner history and all roadmap snapshots intact.
- Full planner failure: first set `AUDLI_PLANNER_ENABLED=false` on the tested full
  application and verify ordinary lessons. If application rollback is necessary,
  deploy this certified bridge backend and matching bridge frontend revision.
  The bridge has no planner API, so the planner frontend is not the matching
  rollback frontend. Resume/completion preserve the two known planner metadata
  fields; no planner milestone is resolved or deleted by the bridge.

Do not revert 004 or restore production just to disable planning. A destructive
restore requires a separately verified recovery procedure and authorization.
Later schema or snapshot changes require renewed rollback certification.
