# AUD-30 — Universal spoken accent preference discovery

Baseline: deployed ff0593e16c948656b0442a343cf493243d228788 (AUD-29).
Related work: AUD-24 preference discovery and AUD-29 optional practice offers.

## Investigation

The deployed scheduler only runs when `/api/lessons/start` creates a new
lesson. The route immediately returns an existing lesson, including lessons
created before accent discovery was enabled. Their social/topic conversation
therefore proceeds without asking about accents. Topic selection does not
itself schedule discovery.

The discovery counter also counts `accent_discovery=true` snapshots even if
the learner abandoned the lesson before responding. Two such starts exhaust
both discovery opportunities indefinitely. There is no initial lesson-count
threshold: an unspecified preference is eligible immediately; only the second
answered attempt has the existing three-start cadence. Saved `preferred` and
`no_preference` states suppress discovery. Missing legacy preference fields
already default to `unspecified`; no migration is needed.

Discovery remains subject to the existing feature/control availability gate.
An unavailable or disabled accent feature continues ordinary lessons. Saved
responses and refusals are respected; budgets and admission controls are intact.

These are source-confirmed, reproducible scheduling defects. The second
production account's specific cause is not yet confirmed: its identity was
requested, and no production learner records or credentials were changed.
Saved preference/decline, answered-attempt history, and control availability
must be checked for that account before attributing its observed behavior to
one particular defect.

## Change and release proposal

Use the existing REVIEW voice turn at the next welcome/topic acknowledgment
boundary of an eligible returning lesson. Preserve lesson checkpoints, topic
answers, scores and onboarding. Count resolved discovery attempts rather than
unanswered scheduled welcomes. Keep the two-attempt cap and three-start
cooldown. Saved responses never restart onboarding or trigger discovery.

Discovery answers in REVIEW use the same persistence and AUD-29 optional
practice offer as WELCOME. A preference grants no consent. Explicit acceptance
uses the existing timestamped, exercise-scoped one-passage grant; its consumption,
expiry, revocation, voice mappings and Clara routing remain unchanged.

No new screens, schema, provider, infrastructure or tutor voice configuration.
The existing Settings screen now includes the supported accent choices and
no particular accent difficulty, saved through the authenticated profile API.
The final release instruction authorizes commit, merge and Render/Vercel
deployment after the complete spoken discovery and Settings scope passes
verification. The second account identity is not a release prerequisite. Rollback to ff0593e is snapshot-compatible because
only existing fields and phases are used; retain all learner data and controls.

## Verification scope

Focused API/domain tests cover new spoken onboarding, resumed existing check-ins
and topic checkpoints, unanswered starts, saved preferences and refusals,
multiple accents, none, ambiguity/cooldown, voice preference updates, and
verified-account persistence across logout/login. Existing AUD-29 routing tests
cover explicit yes/no/ambiguous/topic answers for British/Australian/Irish,
exact approved voice mapping, one-passage consent consumption and transcript
gates. Auth/provider tests use fixtures, not real production sessions or synthesis.

Verification results: broader backend regressions 307 passed / 2 skipped
(PostgreSQL-only locking/pooler cases; no test Postgres URL configured).
After the final immediate-request guard and topic-selection cases, focused
accent/discovery/offer/consent regressions passed 98/98, including 15 new AUD-30
cases. Frontend unit tests, TypeScript, lint and optimized Next.js production build
all passed. No real production learner walkthrough or paid synthesis was run.


## Final Settings scope

`PUT /api/profile/accent-preferences` accepts only British/Australian/Irish
selections; an explicitly submitted empty selection means no particular accent
difficulty. It uses the existing server-verified identity and owner-scoped
repository. A transaction locks and reads current profile state before replacing
only accent preferences. It cannot write learner IDs, difficulty, scores,
onboarding checkpoints, budget controls or lesson consent. Existing spoken
preference extraction and future offer cadence read the same persisted field.

The Settings controls reuse existing checkbox/button styling, show the saved
preference, allow multiple accents, separate the none choice, handle failed
saves/retries, and discard stale responses after unmount/account change.

Final database verification: 554 passing regressions on SQLite and disposable
PostgreSQL 17.11. The two skips are SQLite variants of PostgreSQL-only tests.
Both exact PostgreSQL variants were additionally verified separately: 2 passed.
This covers control lock timeout and transaction-local pooler timeout settings,
as well as persisted preferences, authenticated account isolation and RLS denial.
Production baseline was inspected read-only: schema [1,2,3], shared controls
enabled, daily limit 5000, concurrency 1, revision 3. No migration is required.

Frontend unit, strict TypeScript, lint and optimized production build passed.
Non-paid browser verification passed 66 broader auth/onboarding/spoken-lesson
cases and the final two Settings persistence/retry cases. The initial retry
assertion matched both the application alert and Next.js's route announcer;
scoping the test locator fixed it. Browser execution used temporary Webpack
configuration because Turbopack cannot follow shared worktree dependencies;
no bundled product configuration changed.
