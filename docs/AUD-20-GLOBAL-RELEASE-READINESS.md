# AUD-20 full-platform accent release readiness

Status: **NOT READY — release gates remain open**. Scope is every authenticated
Audli learner, existing and newly registered. There is no account allowlist or
account-specific eligibility gate. AUD-20 remains In Progress until deployed and
verified in production for this population. The minimal compatibility bridge
has been committed, merged and deployed. The full inactive AUD-20 application,
migration 003 and activation remain pending; no paid synthesis has run.

## Current technical release evidence — 2026-10-11

Private application backup restored into isolated PostgreSQL 17.11. All 17 table fingerprints match the same exported production snapshot with UTC and extra_float_digits=3 normalized; earlier string comparison failures were formatting differences. The backup includes all Audli application learner state and generated audio in public, not managed Supabase Auth/storage schemas. Migration 003 on the restored snapshot preserved every learner row and initialized enabled=0, daily_limit=0, characters=0, concurrency=2. See AUD-20-BACKUP-VERIFICATION.json. Nothing has been applied to production.

Read-only live ElevenLabs checks confirm all three exact voices accessible, Flash v2.5 supported and no premium multiplier. Subscription is active: 93,496 of 121,314 credits used (27,818 remaining), maximum credit extension 0 and can_extend_character_limit=false. Provider overage is disabled; application reservations still count submitted characters rather than actual billed credits. Shared tutor and external usage draw from the same included quota. See AUD-20-PROVIDER-SPENDING-CHECK.json and AUD-20-VOICE-ACCESS-CHECK.json. No synthesis calls made.

The isolated release branch starts at bridge 2454f04 and excludes unrelated workspace changes. It preserves current production onboarding, authentication, tutor service and frontend. A compatibility regression in preference/topic handling while voices are unavailable was corrected; 92 focused existing-preference/consent/approval tests passed. Full backend run: 818 passed, 3 expected skips; two additive response-field assertions were updated and both affected API suites reran with 75 passed. This covers 820 distinct passing backend cases with overlapping reruns, not 893 unique tests. Frontend unit suites: 58 passed; typecheck/lint passed; production build passed. npm production audit: zero vulnerabilities; pip check: no broken requirements. Two-instance local disable measurements: SQLite 3.316ms, PostgreSQL 17.710ms; both passed, production propagation still unmeasured. Review evidence is explicitly bundled into the image for runtime hash validation.

Proposed global admission cap: 5,000 characters per UTC day, one concurrent synthesis, at most 4,000 characters per passage, existing 20-second provider deadline/90-second uncertain-outcome lease. These controls apply to every authenticated learner without an allowlist; exhaustion safely uses ordinary listening. Shared disable remains uncached with a two-second control deadline. Production measurements remain pending. Refreshed public production preflight passed 19/19 health/auth rejection/origin checks; successful authenticated playback remains deferred. Error-level monitoring returned no matching logs in the 03:20–03:40 UTC window, a limited observation rather than a guarantee. This proposal has not changed production configuration.

The product owner separately authorized three live synthesis calls / 1,500 combined characters, included credits only, $0 overage and no retries. All three calls passed in the isolated application path: British 0.75, Australian 1.0, Irish 0.75; 472 characters each, 1,416 total. Audio decoded, expected voice routing persisted, replay bytes matched and one-passage consent was consumed. Overage was checked disabled before each dispatch. No production-authenticated playback success is claimed; see AUD-20-LIVE-VOICE-VERIFICATION.json. Dedicated two-account authenticated production acceptance is deferred as an owner-accepted unverified risk.

## Active-release authorization and accepted verification gap

The product owner authorizes deployment and global activation in this release after mandatory backup, migration, security, spending and health protections pass. An inactive deployment alone is not the requested outcome. The dedicated two-account authenticated production acceptance test is explicitly deferred by the owner. Production cross-account isolation, authenticated passage/tutor playback for those accounts remain **unverified risks**, not successful tests. Existing authentication, ownership and consent controls must remain intact; available automated checks remain required. Missing dedicated sessions are no longer an execution blocker.

## Current acceptance policy — product-owner waiver, 2026-10-11

The latest explicit owner instruction supersedes historical numerical-review requirements below. All three specified passage voices are qualitatively approved for all authenticated learners. Numerical ratings are waived, not fabricated or marked passed. The auditable decision is [AUD-20-PRODUCT-OWNER-APPROVAL.json](AUD-20-PRODUCT-OWNER-APPROVAL.json); see [approval policy](AUD-20-QUALITATIVE-APPROVAL.md). The validator accepts either valid numerical review or this exact source-bound owner decision, while retaining model, rate, voice identity and audio-hash gates.

Technical and operational gates remain mandatory. Production is still on schema 2 with bridge `2454f040b693ee5b53ebca1fad5574d4460d8a7c`; no migration, full release, activation or paid synthesis was performed in this follow-up. The dedicated two-account session gate is deferred under the accepted verification gap above. Verified backup restoration and live provider checks are not yet complete. No paid-test budget has been authorized.

Current follow-up verification: 39 focused validator/global-release tests passed; the offline review tool emits all three approved mappings. No synthesis or production writes occurred.

Historical sections below describe earlier decisions and measurements; any requirement for additional subjective ratings is superseded by this explicit waiver.

## Secure access follow-up — 2026-10-11

Protected database access has now been found and verified without copying or
displaying credentials. `/home/lenovo/.config/audli/database.env` is mode 600
and points to the intended production TLS session pooler. Explicit read-only
transactions confirm PostgreSQL 17.11, migrations `[1,2]`, no accent controls,
stable aggregate counts across the two inspections, valid owner relationships
and validated constraints. Historical row-by-row preservation has not been
proven without a prior private snapshot. The pooler did not retain the startup
read-only option, so the final check explicitly set and verified transaction
read-only mode before any data queries.

No approved production test credentials or authenticated sessions were found.
Authenticated passage playback, saved tutor playback and live two-account
isolation remain pending. Public health/authentication-rejection checks passed
**19/19** again; Render returned no matching error or request-5xx logs in the
03:07:00–03:09:10 UTC observation window. Bridge `2454f04` remains live.

Execution stops at the missing-session gate: no backup/restore attempted, no
migration 003, no further deployment, no production data/configuration writes
and no paid provider calls. See [secure session procedure](AUD-20-SECURE-TEST-SESSIONS.md)
and [nonsecret access evidence](AUD-20-ACCESS-VERIFICATION.json).
The owner-run helper `/tmp/aud20-create-test-sessions.py` prompts for normal
sign-in and stores access-only tokens outside the repository with mode 600.
Syntax checked only; no live sign-in or session invention performed. Missing
Audli profile/audio would require separate authorization before initialization
or paid generation. Linear was restored from Done to **In Progress**, as the
owner requires; completion remains gated on global activation and acceptance.

## Bridge production deployment — 2026-10-11

PR [#12](https://github.com/404Yeti/audli/pull/12) merged the minimal bridge as
`2454f040b693ee5b53ebca1fad5574d4460d8a7c` (source commit `a4a6932`). Render
deployment `dep-db5fju3rjlhs73aocrm0` became live at 02:53:18 UTC; Vercel
production deployment `6990115100` succeeded on the same SHA. All three PR
checks passed; no branch-protection requirements or rulesets were configured.
No check was bypassed. Only schema recognition and typed snapshot compatibility
were changed; no accent routing, voice/provider/auth configuration or frontend
code changed. See [bridge design and rollback](AUD-20-SCHEMA-BRIDGE.md) and
[deployment evidence](AUD-20-BRIDGE-DEPLOYMENT.json).

Isolated PostgreSQL 17.11 full-003 upgrade/restart/rollback: **20/20 passed**;
synthetic learner rows unchanged across migration; defaults disabled/zero budget.
Focused SQLite/PostgreSQL regressions: **241 passed, 1 skipped**. Full backend
regressions: **485 passed, 1 skipped**; the additional schema-3 evidence-loop
case was checked separately (**3 passed, 2 SQLite skips**). `pip check` passed;
production npm audit found zero vulnerabilities. No paid provider was used.

Public production checks before and after deployment: **19/19 each passed**,
covering backend/custom-domain/Vercel health, Supabase auth mode, missing/invalid
bearer rejection and origin rejection before provider dispatch. Render queries
returned no matching error-level logs or request 5xx in the observed
02:52:40–approximately 02:56:31 UTC window. Startup diagnostics confirmed the
custom Audli ElevenLabs voice, Flash model, ordinary OpenAI/Coral passage routing
and existing Coral tutor fallback. These observations do not prove authenticated
playback success or absence of future errors.

**Execution stopped before migration:** production remains `[1,2]` and has no
`accent_control` table. No production backup was taken or verified, 003 was not
applied, and the full inactive AUD-20 application was not deployed. Private
database access for a restorable backup and normal authenticated test sessions
for existing passage/tutor playback are unavailable. These required acceptance
gates must be satisfied before resuming. Do not generate replacement audio to
perform these checks without separate paid-synthesis authorization.

After 003, the **minimum rollback floor is bridge SHA
`2454f040b693ee5b53ebca1fad5574d4460d8a7c`**. Never roll back to `df5140c` after
003; never delete tables, reset learner data or downgrade migration history.
The bridge preserves known inactive-release snapshot fields and ignores their
practice semantics. Rollback keeps configuration and database state intact.
The pending full release must be isolated from the mixed research workspace and
rebased onto this production bridge, preserving current onboarding and tutor TTS.

Next required input: private local file paths containing the production session
pooler database URI and normal authenticated test sessions for two existing test
accounts. Keep values out of issue comments, tool outputs, commits and chat.
Then verify authenticated cached playback/account isolation, verify a restorable
backup, apply 003, check the running bridge, and deploy the separately verified
inactive release. Global activation still requires the valid human approval,
authorized budgeted live checks and production control/monitoring acceptance.

## Production execution preflight — halted 2026-10-11

The owner authorized committing, pushing, merging and deploying the inactive
AUD-20 release, with a verified backup before production migration. Activation
and paid synthesis remain separately unauthorized. Execution stopped at the
mandatory migration compatibility gate; no commit, PR, backup, production
migration, production configuration change or deployment was performed.

Render reports live deployment `dep-db51b295efls73a70hfg` at commit
`df5140ca4796a6102bcd5e3b91b402a91f511854`. The exact deployed source was exported
and its PostgreSQL repository startup tested on isolated PostgreSQL **17.11**,
matching the production major/minor version. Baseline migrations 001/002 and
then the proposed 003 were applied to a disposable empty database, with no
production learner data. Before 003 startup passed. After 003 startup failed
at schema version validation: the deployed binary requires exactly `[1,2]`,
whereas 003 records `[1,2,3]`. Therefore restarting the current application or
rolling back to that binary after migration would fail. The isolated database
was removed and its PostgreSQL server stopped. Machine-readable evidence:
[AUD-20-RELEASE-COMPATIBILITY.json](AUD-20-RELEASE-COMPATIBILITY.json).

Migration SQL itself completed locally and seeded `enabled=0`, `daily_limit=0`,
`characters=0`, `concurrent_limit=2`. These checks do not establish production
backup recovery, authenticated production acceptance or activation readiness.
The previous regression results below were not rerun after this stop gate.

Required next step: prepare and verify a minimal compatibility bridge release
that runs on both schemas 1/2 and 1/2/3 before applying 003. It must validate
known schema versions, preserve ordinary listening and tolerate any new persisted
profile/lesson fields needed by the inactive release. Verify bridge rollback
compatibility using synthetic old/new snapshots, then deploy and verify the
bridge on schema 1/2. Only then resume verified-backup, migration and inactive
release execution, using the bridge as the binary rollback target. Do not use
the currently deployed schema-2-only binary as a post-migration rollback target.
This prerequisite remains unimplemented; no compatibility checks are bypassed.

## Formal voice gate

| Accent / candidate | Exact passage voice ID | Human approval |
| --- | --- | --- |
| British / Daniel | onwK4e9ZLuTAKqWW03F9 | Explicit owner approval; numerical ratings waived |
| Australian / Clara | tyepWYJJwJM9TTFIg5U7 | Explicit owner approval; numerical ratings waived |
| Irish / Jessica Gallagher | DbwWo4rVEd5NrejHYUnm | Explicit owner approval; numerical ratings waived |

Run the existing offline review tool:

```bash
.venv/bin/python -m scripts.validate_passage_voices review --output data/voice-evaluation/aud20-validation-20261010
```

It verifies all twelve existing sample hashes and approved identities without synthesis. Runtime evidence delivery must include the owner record, exact results ledger and hashed source clips in an operator-controlled directory on every instance. The current image does not deliver this directory; delivery and startup validation must be verified before activation. Approval does not certify live provider access, production playback or provider spending restrictions.

## Global configuration and infrastructure

See AUD-20-GLOBAL-CONFIG-PROPOSAL.json. Initial deployment remains
`AUDLI_ACCENT_PRACTICE_ENABLED=false`, mapping `{}`, shared `enabled=0`, daily
limit zero. No production values have been written. Environment enabled is a
local deployment ceiling; the shared PostgreSQL flag must also be enabled.
Human approval/hash validation runs at production startup/CLI enablement; a shared
row cannot approve a voice. No per-account eligibility restriction exists.

Migration 003 creates `accent_control` and `accent_reservations` atomically, with
RLS and revoked PUBLIC/anon/authenticated access; no learner data is rewritten.
Existing migrations 001/002 remain unchanged. The migration runner and Docker
packaging include 003. This build requires schema versions 1,2,3 on PostgreSQL;
SQLite local upgrades and learner imports preserve existing history. All API
instances must share the **same writable primary** database. No Redis is added.

The singleton row serializes reservations, operator configuration and counter
updates with `SELECT FOR UPDATE`; separate instance pools use the same lock.
An admission verifies the persisted lesson owner, active EXERCISE phase, current
ordinal and unexpired explicit consent, then atomically claims characters and a
90-second synthesis lease. Scope hashing prevents paid re-dispatch for the same
learner/lesson/ordinal across retries, crashes and budget days. There are no paid
request retries. A scope whose provider outcome was lost uses ordinary listening
on retry; already saved audio preserves Replay/Retry voice without new charges.

Fleet defaults: concurrency two, per-request maximum 4,000 characters, daily
UTC character allowance zero until explicitly chosen. Shared and environment
ceilings both apply; instances cannot multiply the shared allowance. Daily usage
rolls over using database UTC time, never process restart. Failure, timeout and
cancellation retain reserved characters. Uncertain work holds concurrency until
its 90s lease expires; successful HTTP completion releases its slot. Expired
leases stop counting immediately at admission and abandoned active status is
updated lazily on the next successful admission. Never clear scope tombstones
or reset counters during rollback. See PERSISTENCE.md for migration/pool details.

Flag reads have **no positive cache** and a 2s asynchronous operation deadline;
unavailable/timed-out state means no accent offers, discovery or synthesis.
Independent control pools use 0.2s pool, 1s connect, 750ms statement and 250ms lock
timeouts and do not consume the ordinary lesson pool. No restart or public control
endpoint is required. Ordinary listening still depends on its normal database and
provider; total database failure is not an offline listening guarantee.

Disable is effective for admissions after its transaction commits. A reservation
committed before disable is in-flight; it rechecks flag revision and live lease
immediately before dispatch, together with fresh owned consent, ordinal, phase and
expiration checks. A call already dispatched may finish and be billed,
bounded locally by the 20s provider timeout (maximum configurable 60s). After
completion the flag/revision is rechecked; disabled or off/on-changed work falls
back to ordinary speech. A disable occurring after that final check cannot retract
already returned/committed audio. Previously saved owned audio remains replayable. An already-issued check-in
question may remain in its saved checkpoint; replying cannot override the shared
flag or authorize new accent synthesis. Fresh lessons read the shared flag.
The remote provider may continue work beyond HTTP cancellation and even the lease;
provider-side hard caps are still necessary for a monetary ceiling.

Spoken discovery remains bounded/separate with persistent refusal. Saved interest
never assigns a passage. Consent is one passage only and decline/ambiguity/
unsupported/cancelled/paused/expired grants use ordinary voice. Reviewed speeds
are 0.75/1.0 only; other adaptive speeds fall back without changing difficulty.
Tutor voice `yKYzqEa22xh5PdidhN70`, scoring, progress, topic selection and transcript
gates remain unchanged. No selectors/screens are added.

## Measured flag propagation

First isolated test: PostgreSQL **17.11**, two independent application control
pools, synthetic authenticated accounts, flag disable followed by concurrent
fresh reads: both observed disabled in **16.992 ms** after commit; SQLite **3.029 ms**.
Source log `/tmp/aud20-fleet-first.log`, 28 passing cases. This is measured local
observation, not a production latency/SLA claim. The enforced per-operation
fail-closed deadline is 2s; no stale positive result is used after that deadline.
Committed pre-disable admissions are the in-flight exception described above.
Production network, operator command latency and overloaded event-loop scheduling
still need staging measurement. Record the final regression measurement below.

## Multi-user usage and cost exposure

The shared ledger covers accent passages only, not tutor speech, other products,
external clients, provider billing adjustments or old in-flight work. A database
restore can resurrect pre-reservation state: keep accents disabled through restore
and reconcile provider usage before re-enabling. This ledger is not a dollar cap.

One newly synthesized accented passage adds one ElevenLabs request, normally
replacing ordinary passage synthesis. Replay/Retry use saved audio and add no
passage synthesis. Offers, confirmations and fallback explanations can add tutor
characters. Provider timeouts, cancelled/stale generation or lost responses may
consume credits without saving a usable passage; reservations are not refunded.

Assumptions for planning, not measured traffic: `characters = active learners ×
sessions per learner × accepted requests per session × mean passage characters`.
At 500 characters per accepted passage:

| Accepted passages per day | Submitted characters/day | Planning credits/day |
| --- | --- | --- |
| 100 | 50,000 | 25,000–50,000 |
| 1,000 | 500,000 | 250,000–500,000 |
| 10,000 | 5,000,000 | 2,500,000–5,000,000 |

ElevenLabs currently documents Flash/Turbo API pricing at 0.5–1 credit per
character and a shared credit pool across products. Verify this account's plan,
voice multiplier, remaining credits, current USD overage rate and tutor baseline
before approving a budget. No account-specific USD estimate can be certified
without those values. [ElevenLabs pricing](https://elevenlabs.io/pricing), checked
2026-10-11. No new ElevenLabs requests or credit usage occurred in these tests.

## Global verification matrix

| Required scenario | Evidence / remaining gate |
| --- | --- |
| New users with onboarding preferences | Existing onboarding regressions plus preference/consent tests; live approved voice pending |
| Existing saved preferences | Offers/acceptance/refusal/alternate/request fixtures; no automatic assignment |
| Legacy users without preferences | Actual legacy JSON, bounded discovery, persisted refusal fixtures |
| Practice refusal, ambiguity, unsupported request | Consent/default-voice regressions; no repeated same-lesson offer |
| Interrupted/cancelled/expired consent | Owned CAS, pause invalidation, deadline and delayed-write tests |
| Different authenticated learners concurrently | Mock Supabase verified identities; simultaneous owned start/answer and cross-account 404 tests |
| Provider failure/timeout/insufficient credits | 402/429/503 and timeout fixtures, unchanged default audio, no retry |
| Concurrent provider traffic / budget | Atomic shared PostgreSQL/SQLite reservations tested across pools; production-scale staging load/latency pending |
| Replay/Retry/fallback | Persisted passage audio/voice and spoken fallback regressions |
| Authentication and PostgreSQL | Read-only production catalog checks below; isolated PostgreSQL contracts added; real Supabase-authenticated staging checks pending |
| Feature disabled | Default-off and pre-existing grant tests; ordinary lesson available and preferences preserved |
| Approved accent quality end to end | Owner approval recorded; live device/voice verification pending for each enabled voice |

Automated provider/auth fixtures verify plumbing, not actual voice suitability,
actual evaluator accuracy, real account credits or live authenticated behavior.

## Production compatibility and health evidence

Read-only connected Supabase catalog checks on 2026-10-11 found PostgreSQL
17.11, schema versions 1 and 2, UUID ownership keys, JSONB profile/lesson/exercise
snapshots, and lesson revisions. RLS is enabled and direct SELECT/INSERT/UPDATE/DELETE are denied to
anon/authenticated roles on the four inspected application tables. No table,
policy, role, configuration or learner state was modified. Only catalog metadata
and aggregate compatibility booleans were returned, with no learner identifiers
or profile/script contents. Existing persisted snapshots are JSON objects and
lesson version 1; legacy rows without preferences or consent exist and require
the tested default model fields.
Existing request-scoped server identity verification is preserved; browser UUIDs
and editable user metadata are not authorization sources. Auth validation uses
the server's user endpoint, consistent with [Supabase get_user documentation](https://supabase.com/docs/reference/python/auth-getuser).
The changelog was reviewed; actual production regression/load tests remain
necessary. The read-only catalog result is not a full PostgreSQL contract pass.

Read-only public probes returned health 200/ok/openai, Auth configuration
200/supabase, and unauthenticated profile/current-lesson 401s. No authenticated
production lesson was modified or tested.

Use `/api/health` for process liveness; it does not prove DB writes, provider
availability, credit sufficiency or successful authenticated lessons. Verify
public auth configuration and unauthenticated 401s, then authenticated staging
ordinary/accent flows with the current production-compatible configuration.
Record PostgreSQL version/schema validation, account ownership, legacy JSON,
concurrent operations, transcript gates, pause/cancellation and default-off behavior.
Do not use production learner writes to compensate for a missing isolated test DB.

Monitor aggregate success, provider fallback and limit fallback events, submitted
characters, API error rate/latency, provider 402/429/5xx/timeout rate, ledger usage,
remaining credits and tutor fallback. Logs contain fixed categories/numbers only,
never scripts, account IDs, JWTs, provider response bodies or raw learner audio.
Confirm operational dashboards, alert delivery/owner and flag propagation time
before release. Alert on any isolation/gating/wrong-voice incident; disable on
credit reserve breach or sustained errors/fallbacks. No monitoring configuration
or alert thresholds have been applied to production.

## Deployment and rollback checklist — not executed

1. Complete formal human CSV, credibility/pacing and adaptive-rate policy gates;
   deliver exact evidence/mapping and test every approved voice live end to end.
2. Approve daily character allowance N, fleet concurrency M (1–16, proposed two),
   ElevenLabs account hard limits/overage policy, tutor credit headroom and alerts.
   N=0 remains safe off; no positive production budget is proposed as approved.
3. Measure staging concurrency/load, real Auth/primary DB compatibility, live voices,
   monitoring delivery and disable propagation under production-like load.
4. Obtain explicit production migration/deploy/config authorization. Apply the
   complete `docs/postgres-003.sql` after existing 001/002, or run
   `python -m scripts.migrate_postgres` as database owner. Verify versions 1,2,3,
   RLS/revocations and initial disabled control row. Do not manually add a marker.
5. Deploy a reviewed compatible build to every API instance with:
   `AUDLI_PERSISTENCE=postgres`, verified primary `AUDLI_DATABASE_URL` (TLS),
   `AUDLI_AUTH_MODE=supabase`, existing Auth values unchanged,
   `AUDLI_ACCENT_PRACTICE_ENABLED=false`, `AUDLI_PASSAGE_VOICES={}`,
   `AUDLI_PASSAGE_REVIEW_DIR=data/voice-evaluation/aud20-validation-20261010`,
   `AUDLI_PASSAGE_TTS_MODEL=eleven_flash_v2_5`, timeout 20, max characters 4000,
   max concurrent M, daily characters N (initially zero). Preserve existing
   tutor provider/model/key and voice ID. No production configuration was changed.
6. Run `python -m scripts.accent_controls status`; verify health, Auth 401s and
   ordinary authenticated lessons/replay while off. The health endpoint is only
   liveness, not a provider/credit/write check.
7. After separate activation authorization and passing all gates, deliver validated
   voice evidence/map, set the environment ceiling true with N/M, roll all compatible
   instances while the shared flag remains off. Run the authorized operator command:
   `python -m scripts.accent_controls configure --daily-characters N --concurrent M`,
   then `python -m scripts.accent_controls enable`. Enable validates production
   Settings and review evidence. Never hand-edit approvals or reset usage counters.
8. Verify the intended global population and each voice in production, monitor
   aggregate outcomes/latency/credits and keep AUD-20 In Progress until verified.

Emergency feature rollback (authorized operational action): from a privileged
backend shell with current production configuration run
`python -m scripts.accent_controls disable`, then `status`. This updates **one
shared database row**, increments revision and blocks new admissions on every
instance without restart, schema migration or learner-data edits. Verify fresh
requests use ordinary listening/no offers, existing owned replay, transcript gates,
preferences, scores and progress. Already dispatched calls may finish/bill; new
retries cannot re-dispatch their paid scope. If DB state is unavailable, accents
fail closed automatically; restore DB connectivity without clearing the ledger.
Keep the compatible binary and schema; downgrade is a separate compatibility
review because older strict domain models/schema checks may reject new state.

## Remaining blockers

Mandatory CSV reviews and extra credibility/pacing checks; reviewed adaptive-rate
coverage/policy; evidence delivery to each production instance; live end-to-end
checks of each approved voice (not authorized/run); real Supabase-authenticated
staging and production-like load/propagation measurements; chosen positive budget,
provider account credits/USD overage/hard caps and tutor headroom;
production connection headroom (three lesson plus two control connections per process)
and primary/pooler compatibility with control-session timeout options; monitoring alerts
and operational owner. Process-local infrastructure limitations are replaced by
shared controls but production migration 003/deployment remain unexecuted.
AUD-28 tracks the existing development dependency advisory separately.
Production action requires explicit authorization after release gates pass.

## Verification for this infrastructure revision — 2026-10-11

- Full backend regression across SQLite and disposable PostgreSQL **17.11**:
  **759 passed, 1 skipped**, 304.17s. The skip is the Postgres row-lock timeout
  case on SQLite; the PostgreSQL version passed. A prior run exposed an old
  non-UUID test fixture; it was corrected without changing product UUID checks.
- Final accent/fleet/consent/global/persistence regression after the dispatch
  consent guard: **215 passed, 1 skipped**, 88.00s. The preceding stale-result
  focused run passed 158 cases with the same one SQLite skip (68.21s). This includes forced event-loop
  delay, atomic fleet caps, uncertain provider outcomes, hard worker exit,
  restart/deduplication, explicit 002→003 upgrade, RLS, ownership and consent.
- Additional two-instance authenticated API integration: **2 passed**, 4.59s
  (SQLite/Postgres). Exactly one mocked accent synthesis with concurrency one;
  the other learner receives ordinary fallback. Consent is consumed, shared
  disable preserves owned replay and cross-account audio returns 404.
- Migration/fleet focused run: **38 passed, 1 skipped**, 22.27s; overlaps the
  suites above. Do not add these counts as unique tests.
- Final local disable observations: full run PostgreSQL **17.839ms**, SQLite
  **4.565ms**; focused guard run PostgreSQL **14.669ms**, SQLite **3.428ms**,
  final dispatch-guard run PostgreSQL **15.629ms**, SQLite **3.575ms**;
  each across two independent authenticated app control pools after commit.
  These are local observations; no production propagation measurement is claimed.
- Frontend unit suites **53 passed**; separate voice-handoff **4 passed**.
  TypeScript, ESLint and production build passed. Browser **90 passed, 1 skipped**
  in 2.6m; the dedicated real-backend/native microphone/playback test passed
  separately (**1 passed**, 4.7m), using offline speech fixtures.
- Production dependency audit: **0 vulnerabilities**. `pip check` found no broken
  requirements (not a vulnerability scan). `git diff --check` passed.
- Administrative configure/disable/status smoke passed on a fresh local SQLite DB;
  no production CLI commands were executed. All 12 real clip hashes still match;
  mandatory CSV has 0 documented raters and formal approved mapping remains `{}`.

Logs: `/tmp/aud20-fleet-backend-final.log`, `aud20-fleet-freshness-final.log`,
`aud20-fleet-auth-integration.log`, `aud20-fleet-dispatch-final.log`,
`aud20-fleet-migrations.log`,
`aud20-fleet-frontend.log`, `aud20-fleet-handoff.log`, `aud20-fleet-types.log`,
`aud20-fleet-lint.log`, `aud20-fleet-build.log`, `aud20-fleet-browser.log`,
`aud20-fleet-integrated.log`, `aud20-fleet-audit.log` (all under `/tmp`).
Durable content-free summary: AUD-20-FLEET-VERIFICATION.json.

Fixtures establish protocol/state plumbing, not actual accent quality, provider
credit behavior, live Supabase authentication or production load performance.
No production learner data, paid synthesis, commits, merges, deployment or
production configuration changes were used. The readiness phrase is withheld
until every remaining gate passes.
