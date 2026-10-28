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
- Further PostgreSQL, targeted integration, browser and remote results: pending final verification; do not infer passing from this placeholder.

## Release decision

NO-GO production activation. See MIGRATION-SAFETY.md for native-vs-compatibility guarantees, uncertainty recovery, least-privilege schema rehearsal requirements and bridge-only rollback. See IPHONE-ACCEPTANCE.md for the proposed physical test. Native handlers and compatibility guards are development integration, not an installable production migration. No merge/deploy approval is requested.
