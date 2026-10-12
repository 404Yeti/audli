# AUD-34: independent test-only onboarding reset

This branch is based on current main and extracts Restart Onboarding without the permanent-deletion implementation in draft PR #20. It adds no deletion route, deletion control, Supabase administrative operation, or privileged Auth credential. Do not merge PR #20 as part of this release. AUD-34 stays open; deletion/retention and AUD-36 provider-history work remain outstanding.

## Authorization and configuration

Every protected request retains existing Supabase `/auth/v1/user` verification using the publishable key and the caller's bearer token. Confirmed, nonanonymous identity supplies the repository owner; browser UUIDs and user metadata cannot authorize reset. Authentication, MFA settings, Auth identities, and sessions remain unchanged.

In production, only UUIDs in the server-only `AUDLI_ONBOARDING_TEST_ACCOUNTS` JSON array are authorized. Its default is `[]`, which denies every production account. Invalid UUIDs fail configuration validation. Development environments permit reset through their existing authentication mode. No frontend environment variable or user-editable metadata grants access.

GET `/api/onboarding/reset-controls` returns only `restart_available: false` for unauthorized accounts. POST `/api/onboarding/restart` requires authorization, exact `RESTART ONBOARDING` confirmation, and the current nonnegative onboarding revision. Additional owner/password fields are rejected. Settings displays the control only when the server authorizes it. Capability-fetch failures keep it hidden.

**Production authorization blocker:** the owner has not supplied the explicitly authorized test accounts. No production allowlist was changed or verified in this review. Do not infer permission from existing users or this repository's test fixtures. Before approval, resolve the supplied identifiers against existing confirmed Supabase accounts, verify the API service uses production environment and Supabase authentication, configure the exact UUID array using a save-only operation, and verify the saved values. Saved configuration is distinct from runtime verification: after an owner-approved release, check an allowlisted account succeeds and an ordinary account receives 403. No live account reset was performed for this review.

## Exact reset boundary

Only these learner-profile fields reset to their schema defaults:

| Field | Reset |
| --- | --- |
| `name` | `Listener` |
| `target_language` | Default English |
| `goal` | `Understand everyday English` |
| `interests` | Default `["technology"]` |
| `target_situations` | Empty list |
| `accent_preferences` | Unspecified preferences with no selected accents |
| `onboarding_status` | `not_started` |
| `onboarding` | Answers, pending recognition, stages, clarification state, typed-name state, and checkpoints reset; welcome introduction restored |
| `initial_listening_profile` | Cleared onboarding snapshot |

The onboarding revision and name-question token advance monotonically rather than returning to zero. Old reset requests, typed-name submissions, audio uploads, and other revision-bound onboarding submissions cannot operate on a new run.

Current learning scores, difficulty, focus, completed-attempt counts, lessons, attempts, evaluations, evidence, conversations, adaptation history, lesson membership, audio assets, and provider-budget records are preserved. All unrelated profile fields are preserved. Existing generated onboarding audio caches are preserved and keyed by prompt/configuration; raw learner recordings are not introduced or retained by reset. The frontend removes only the current account's transient session timer and reloads into onboarding; Auth credentials and accessibility preferences remain saved.

## Persistence and failure handling

Reset updates the authenticated owner's profile in one transaction with a PostgreSQL row lock and revision comparison. It rejects any active or paused lesson and any current incomplete exercise. A failed transaction rolls back; a lost response is resolved by reloading the authoritative checkpoint. The client only reloads after receiving the expected destination and next revision, and otherwise shows the error without claiming success.

In authenticated PostgreSQL deployments, all mutating API requests for reset-capable owners take the same owner-specific advisory transaction lease, including time awaiting provider responses. Ordinary production accounts retain their existing request behavior. This closes the race where a different worker could finish saving a lesson after reset checks. Locks use a separate unpooled connection, avoiding application pool exhaustion while waiting; each in-flight mutation for a reset-capable account uses an additional connection. Contention returns 409 after bounded retries. Cancellation/error releases the connection and transaction lease. Different accounts use different locks. SQLite remains a development/test backend with the existing application lock.

**Release procedure:** old API processes do not participate in this lease. The owner-approved release must drain old workers and in-flight mutations before test accounts use reset; do not enable this during mixed-version overlap. No schema migration or Auth administrative grants are required. This review does not deploy or merge anything.

## Verification

Review performed 2026-10-12. Tests use mocked provider/Auth responses and disposable local databases; no paid provider calls or live learner mutations were made.

- Full backend/PostgreSQL run on the initial `be53624` main baseline plus reset changes: **1,171 passed, 5 skipped**. Main advanced during the run; the final locking refinement and current `fc7a9d2` baseline were checked separately below.
- Final backend/PostgreSQL integration run on `fc7a9d2`: **211 passed, 2 skipped**, covering reset, authentication, persistence, and the newly merged Australian-passage/global-release checks. PostgreSQL-only concurrency tests execute on PostgreSQL and intentionally skip their SQLite fixture variants.
- Reset-specific run: **21 passed, 2 skipped**, including rollback after the profile update, whole-table/history/audio preservation, other-owner preservation, typed/spoken repetition, resume, and cancellation recovery.
- Frontend unit tests: **58 passed**. TypeScript, ESLint, and the optimized production build passed.
- Authentication/mobile browser regression: **50 passed**. **6 final reset browser cases passed**, including revoked authorization and an unconfirmed response.
- All **23** built public JavaScript assets scanned: no account-deletion route/control/confirmation text or service-role-key marker. Backend route tests confirm account-deletion and account-controls paths are unregistered and return authenticated 404.

Production readiness remains blocked on the owner's explicit test-account selection and saved allowlist verification. This is a reset-only code review; no production behavior is claimed verified before an approved release.

Both frontend project roots disable Vercel deployments for `feat/AUD-34-onboarding-reset`. Commits/PR use `[skip render]`; the branch is not merged into Render's auto-deploy branch. These guards protect review preparation only, not a future owner-approved release.
