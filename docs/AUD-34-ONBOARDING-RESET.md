# AUD-34: Restart Onboarding for authenticated learners

The owner has approved Restart Onboarding as a standard authenticated learner feature. PR #22 removes the former test-account allowlist and associated configuration. It contains no permanent-deletion implementation, deletion routes/controls, Supabase administrative operation, or privileged Auth credential. PR #20 remains draft; AUD-34 stays open for deletion and AUD-36 remains tracked for provider-history/retention work.

## Authentication and ownership

Every protected request retains Supabase `/auth/v1/user` verification using the publishable key and the caller's bearer token. Confirmed, nonanonymous identity supplies the request-scoped repository owner; browser UUIDs and editable user metadata cannot select a target account. Authentication, MFA settings, Auth identities, and sessions remain unchanged.

GET `/api/onboarding/reset-controls` supplies the authenticated learner's current onboarding revision and reset explanation. Settings shows Restart Onboarding to authenticated learners. POST `/api/onboarding/restart` requires exact `RESTART ONBOARDING` confirmation and the current nonnegative onboarding revision. Additional owner/password fields are rejected; no target-user URL parameter exists. Missing, invalid, expired, unconfirmed, or anonymous authentication is rejected by the existing authentication boundary. Local shared identity remains development-only.

## Exact reset boundary

Only these learner-profile fields reset to their schema defaults:

| Field | Reset |
| --- | --- |
| `name` | `Listener` |
| `target_language` | English (`en`) |
| `goal` | `Understand everyday English` |
| `interests` | Default `["technology"]` |
| `target_situations` | Empty list |
| `accent_preferences` | Unspecified preferences with no selected accents |
| `onboarding_status` | `not_started` |
| `onboarding` | Answers, pending recognition, stages, clarification state, typed-name state, and checkpoints reset; welcome introduction restored |
| `initial_listening_profile` | Cleared onboarding snapshot |

Current learning scores, difficulty, focus, completed-attempt counts, lessons, attempts, evaluations, evidence, conversations, adaptation history, lesson membership, audio assets, and provider-budget records are preserved. All unrelated profile fields are preserved. Existing generated onboarding audio caches remain keyed by prompt/configuration. Reset introduces no recording deletion or retention change. The frontend removes only the current account's transient session timer and reloads into the spoken introduction; Auth credentials and accessibility preferences remain saved.

The onboarding revision and name-question token advance monotonically rather than returning to zero. Old reset requests, typed-name submissions, voice uploads, heard/start acknowledgments, answers, and completion requests cannot operate on a new run. The server rejects stale revisions and the client closes the old request/audio lifetime when it reloads. Repeated resets and refreshed/resumed onboarding use the authoritative saved checkpoint.

## Persistence and failure handling

Reset updates only the authenticated owner's profile in one transaction with a PostgreSQL row lock and revision comparison. It rejects any active or paused lesson and any current incomplete exercise. A failure after UPDATE but before commit rolls back. A lost/unconfirmed response is resolved by reloading the authoritative checkpoint. The client only reloads after receiving the expected destination and next revision; otherwise it displays the error without claiming success.

All authenticated PostgreSQL mutating API requests take the same owner-specific advisory transaction lease, including time awaiting provider responses. This prevents a different worker from finishing a lesson save after reset checks. Locks use a separate unpooled connection; each in-flight mutation uses an additional database connection. Contention returns 409 after bounded retries. Cancellation/error releases the connection and lease. Different accounts use different locks. SQLite remains development/test-only and uses the existing application lock.

No schema migration or Auth administrative grants are required. Old API processes do not participate in this lease: complete the first rollout and old-worker shutdown before any real production reset. Do not issue a real production reset during deployment. The owner has authorized merge and Render/Vercel deployment after checks pass, but requires separate confirmation before resetting an actual production learner.

## Verification and release

Release regression results (2026-10-12):

- Full backend with SQLite and disposable PostgreSQL: **1,218 passed, 6 skipped**. Skips are SQLite variants of PostgreSQL-only checks; those PostgreSQL variants passed.
- Save-lock prerequisite isolated from main: **121 passed, 1 SQLite variant skipped**.
- Frontend unit tests: **58 passed**. TypeScript, ESLint, and optimized production build passed.
- Full Chromium regression initially: **135 passed**, 1 optional integrated-media scenario skipped, and one reset reload assertion timed out during SDK restoration with six workers. Its wait now permits 15 seconds for session restoration; all **7 reset browser cases passed** in the final run. This yields **136 distinct passing browser cases**, including desktop and mobile reset, authentication, onboarding, lesson lifecycle, and password recovery.
- **23** public JavaScript assets scanned: no account-deletion control/route/confirmation or service-role-key marker. Backend tests verify authenticated account-deletion paths are unregistered (404).

The first Render rollout is sequenced through a save-lock prerequisite PR before PR #22, preventing mixed old/new worker races. Deployed revisions and production health results will be recorded on AUD-34. Tests use mocked providers and Auth responses plus disposable PostgreSQL/SQLite databases. No paid provider calls or real production resets are authorized for this review.

Required coverage includes authenticated success, missing/invalid authentication, body/URL cross-account rejection, other-owner preservation, complete history/evidence/audio preservation, rollback after update, simultaneous resets, active/paused lessons, in-flight saving, cancellation recovery, repeated typed/spoken onboarding, stale voice/completion requests, checkpoint resume, and desktop/mobile confirmation and navigation.

The review branch has Vercel preview guards for both frontend roots. These guards apply only to this branch and do not disable main's production deployments. The release merge commit must not carry a Render deployment-skip marker. Production health/access checks must cover Render and both app aliases. Authenticated production capability verification needs an existing authorized session; absence of that session must be reported as unverified rather than treated as passing. No real reset will be performed without owner confirmation.
