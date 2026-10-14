# AUD-40 planner port onto current main

Baseline: ce55cbc5766174d117f7fa006320d04dabbb55a3; public production health
confirmed this exact deployed bridge on 2026-10-14. Planner is not deployed.

Port adds only planner modules, owned roadmap storage, Plan/check-in UI,
post-commit refresh and start fallback. Typed-name models/checkpoints, current
confirmation-based onboarding reset, accent library/consent/voice routing,
ElevenLabs budgets/fallback, tutor voice and authentication are preserved.
Current ESLint/Oxlint packages/configuration, lockfiles and both Vercel
preview-disable entries are retained. Lesson topic preferences take precedence
over a previously selected outline topic at the existing conversational boundary.

Synthetic persisted-profile fixture was serialized with the exact bridge. It
roundtrips typed_name/name_question_revision and accent preferences through
planner feedback on SQLite/PostgreSQL. Existing typed-name/reset/auth/accent
regressions run with the full suite. Planner tests cover database/timeout/generation
faults, independent rollback, ordinary audio generation fallback, historical
activation, concurrent completion idempotency and answer/skip persistence.

Migration004 exactly matches the certified restore rehearsal and bridge fixture:
SHA256 fced6e1053cffe371d159dd457589aaed37f2cbc8cd8c39c698b0c5fad469c3c.
New PostgreSQL regression checks exact003-to004 preservation and exclusion of
pre-migration completions. Required rollback CI checks test deployed bridge ce55
against both003/004 separately from the new full application's004 suite.

Initial local failures were corrected in migration/profile test setup: synthetic
old-schema fixtures remove later tables/ledger together, and persisted profile
fixtures include relational values alongside JSON. A browser fallback case first
timed out during account connection and passed isolated; full rerun/CI required.
Focused SQLite/PostgreSQL suite:216 passed,3 skipped. Web unit/lint coverage/build/
TypeScript passed. Both npm audits:0 vulnerabilities. Python audit:no known
vulnerabilities. Full local reruns:905 backend passed,17 skipped;1382 SQLite/PostgreSQL passed,
18 skipped;138 browser passed,1 native-media case skipped in that mocked suite.
Final additional compatibility/fault/migration run:52 passed,2 backend-specific
skips. Native HTTP/audio acceptance and exact rollback003/004 are mandatory
GitHub CI gates. All eight GitHub jobs must pass; actual runs/results will be
recorded in the PR/Linear. Certified SQL retains its original trailing whitespace
so the rehearsed migration hash is not changed.

Release gate: no merge/deployment/production migration during this port task.
Later release requires fresh production schema/backup-state preflight, authorized
production004, bridge-on004 verification, full backend/frontend deployment and
non-billable planner acceptance. Real-provider audio/evaluation/live learner
scenarios are not certified by mocked regressions. AUD-40 remains In Progress.
