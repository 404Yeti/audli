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
- Final consolidated PostgreSQL migration suite: 62 passed; its 24 integration cases include covering the native stage, compatibility integration, drain/rollback, restricted backend role and generation-to-completion pipeline.
- Frontend unit suite, lint, production build and TypeScript passed. Final sender/processing/notice checks: 42 passed; broader selected frontend regressions: 68 passed. Counts overlap.
- Remote run 40616937480 at 6e66037: Backend, PostgreSQL, Frontend, schema 003/004 and Browser passed. Browser comprised 51 classroom WebKit cases, 24 Chromium/WebKit recovery cases, 361 mocked browser regressions, 18 HTTP short-choice cases and one native media lesson. These are different suites, not a physical iPhone result.
- That initial remote run failed Security on 14 provenance/fixture false positives. Exact-value/file allowlists were added after inspecting redacted findings; no broad secret-scan exclusion or required-check waiver was added. Final CI must be green before review approval.
- This Ubuntu 26.04 host initially lacked WebKit libraries. An isolated library rehearsal launched WebKit but pages crashed; authoritative WebKit evidence above is the passing official CI image, not a claimed local pass. No timeout was increased and CI host checks remain enabled.

## Release decision

NO-GO production activation. See MIGRATION-SAFETY.md for native-vs-compatibility guarantees, uncertainty recovery, least-privilege schema rehearsal requirements and bridge-only rollback. See IPHONE-ACCEPTANCE.md for the proposed physical test. Native handlers and compatibility guards are development integration, not an installable production migration. No merge/deploy approval is requested.

## Safety review corrections

Native claims cannot steal an expired compatibility operation whose partial effects are unknown. Replaying an invalidated Record again receipt cannot clear a newer answer. Streams never receive a second JSON response if cleanup fails after headers were sent. Reset/expiry/replacement take the short legacy advisory key as well as the owner row lock. Read-only drain reports distinguish processing, expired/unresolved and unknown operation formats; rollback certification refuses every nonempty category and all unaware target protocols. The report requires incoming mutations to remain stopped externally; it cannot certify a fleet by itself.

The full installer restores the original listening acknowledgment and full lesson projections; it is not limited to the seeded listening checkpoints used by the earlier benchmark. The real HTTP regression covers mocked generation, listening, native upload and assessment, atomic adaptation, readiness, closing and COMPLETED persistence, checking zero provider-wait checkouts and exact call counts.

Buffered audio and JSON fast-following regressions pass at zero authentication latency; unbuffered audio retains ownership through body completion. Processing feedback is text-only, and repeated uncertain manual status recovery remains a DeliveryFailed state with audio retained and no further POST. Final remote CI is linked in the PR review record; no local WebKit pass is claimed.

## Final recovery additions

Record again tombstones the stable recording ID even if the old send has not reached claim admission. Delayed old uploads are refused before STT. A lost replacement acknowledgment can be recovered idempotently only at the same empty question/revision; an old replacement cannot discard newer pending evidence. The client refreshes the check-in revision after replacement before starting its new recording.

Recognition retries identify the acknowledged previous attempt. The server permits this replacement only when its own STT reliability check failed or its completed assessment recorded a transcription concern, and retains both attempt rows. The browser cannot override reliable/ambiguous pending evidence or weaken confidence/comprehension gates. The replacement ID stays with the Blob across transport retries.

Local final Chromium recovery suite: 14 passed, including explicit recording replacement in exercises and check-ins. Remote CI must rerun the full required matrix at the final reviewed commit, including the 28-case Chromium/WebKit recovery suite; first-run results above are provenance, not a claim of final-head CI success.
