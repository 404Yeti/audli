# Migration-safety verification

Baseline: approved `4e5ed23`; separate review branch `codex/AUD-94-migration-safety`. Final counts and remote CI links are recorded below after verification. All providers are deterministic mocks. No production schema, flags, infrastructure or provider accounts are modified.

## Implemented

- P2 understands typed processing receipts, polls matching operation completion without consuming audio sends, retains in-memory recording and receipt through uncertainty/backgrounding, and reads status before explicit Send again.
- Native exercise assessment/progression keeps its existing atomic fences. Remaining original provider-backed routes use shared durable admission and transaction-level fencing, including direct engine connections. Unknown legacy transaction/provider outcomes are quarantined, not automatically rerun.
- Original lesson closing/completion response and validation preserved; reset/expiry invalidation works without an exercise. Explicit recording replacement revokes only matching evidence ownership.
- Guard-aware bridge admission shares the old advisory key; middleware exemptions are per instance. Activation with declared unaware old workers is refused. Actual unaware fleet drain remains an operational gate.
- Local non-owner role/RLS/EXECUTE rehearsal and browser-role denial; schema and account-deletion/retention gates documented.
- Existing full-path benchmark artifacts remain byte-for-byte unchanged. Safety work adds checks and must be benchmarked independently before a latency claim is made.

## Verification record

- Full backend: 2,065 passed, 79 skipped (SQLite/default mode); one existing Starlette deprecation warning.
- Full PostgreSQL-enabled backend: 3,000 passed, 82 skipped; one existing deprecation warning.
- Final consolidated PostgreSQL migration suite: 53 passed, covering the native stage, compatibility integration, drain/rollback, restricted backend role and generation-to-completion pipeline.
- Frontend unit suite, lint, production build and TypeScript passed. Targeted sender/processing tests: 36 passed after cancellation/deadline hardening.
- Remote run 40616937480 at 6e66037: Backend, PostgreSQL, Frontend, schema 003/004 and Browser passed. Browser comprised 51 classroom WebKit cases, 24 Chromium/WebKit recovery cases, 361 mocked browser regressions, 18 HTTP short-choice cases and one native media lesson. These are different suites, not a physical iPhone result.
- That initial remote run failed Security on 14 provenance/fixture false positives. Exact-value/file allowlists were added after inspecting redacted findings; no broad secret-scan exclusion or required-check waiver was added. Final CI must be green before review approval.
- This Ubuntu 26.04 host initially lacked WebKit libraries. An isolated library rehearsal launched WebKit but pages crashed; authoritative WebKit evidence above is the passing official CI image, not a claimed local pass. No timeout was increased and CI host checks remain enabled.

## Release decision

NO-GO production activation. See MIGRATION-SAFETY.md for native-vs-compatibility guarantees, uncertainty recovery, least-privilege schema rehearsal requirements and bridge-only rollback. See IPHONE-ACCEPTANCE.md for the proposed physical test. Native handlers and compatibility guards are development integration, not an installable production migration. No merge/deploy approval is requested.

## Safety review corrections

Native claims cannot steal an expired compatibility operation whose partial effects are unknown. Replaying an invalidated Record again receipt cannot clear a newer answer. Streams never receive a second JSON response if cleanup fails after headers were sent. Reset/expiry/replacement take the short legacy advisory key as well as the owner row lock. Read-only drain reports distinguish processing, expired/unresolved and unknown operation formats; rollback certification refuses every nonempty category and all unaware target protocols. The report requires incoming mutations to remain stopped externally; it cannot certify a fleet by itself.

The full installer restores the original listening acknowledgment and full lesson projections; it is not limited to the seeded listening checkpoints used by the earlier benchmark. The real HTTP regression covers mocked generation, listening, native upload and assessment, atomic adaptation, readiness, closing and COMPLETED persistence, checking zero provider-wait checkouts and exact call counts.
