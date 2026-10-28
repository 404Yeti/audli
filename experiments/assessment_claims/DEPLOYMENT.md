# Compatibility, deployment and rollback proposal

**Design only. No production migration, activation or deployment is authorized.** This branch installs only in disposable local databases and exposes no production feature flag. Existing flags and PR110/111 remain untouched.

## Writer matrix

| Writer | This review stage | Required before activation |
|---|---|---|
| Exercise STT/upload | Durable slot, stable ID, fenced atomic attempt/pending commit | Typed processing response/status recovery in production client |
| Assessment/follow-up/final evaluation/adaptation/session completion | Durable claim, evidence checkpoint, grounded typed provider result, atomic fenced commit | Full remote CI, realistic faults/load, privacy review |
| Ready / lesson advance | Own stable operation; revision/CAS + evaluation/feedback guards | Client acknowledgement/resume compatibility |
| Pause/end, expiry/GET settlement, explicit record-again | Atomic invalidation + fence rotation + mutation | Complete server entry-point and expiry scheduler rollout review |
| Confirmed onboarding restart | Same-transaction invalidation and original reset/allowance checks | Retention, tombstones and account-deletion migration |
| Question/profile/lesson/raw repository writes | Database guard rejects live-operation bypass/owner reassignment | Convert every actual entry point; graceful conflict handling |
| Generation, check-ins, tutor audio/TTS, closing/homework, other onboarding/provider APIs | Claims-only worker rejects unconverted mutations; legacy bridge behavior remains separate | Migrate each route before feature-complete production canary |
| Account deletion/retention/admin maintenance | Guard/FK failures prevent active ownership bypass | Explicit invalidate/drain/delete and privacy-retention procedure; no disabling guards to work around failures |
| Global content/config/provider-budget catalogs | Outside per-learner ownership | Separate admin authorization; never change here |

## Safe sequence to propose later

1. Review and version an additive production schema with least-privilege function execution, security/search-path rules, indexes/constraints, tombstone retention and upgrade/downgrade compatibility. Do not install the local experimental schema as a production migration.
2. Ship a guard-aware **bridge** release with claims-only behavior disabled. Keep advisory ownership for provider-spanning work. All writers must either use fenced transactions or explicitly invalidate them; old GET settlement and error handling must understand conflicts. Verify schema/protocol versions and resolve any previous operation format. Durable IDs/results remain readable.
3. Drain every unaware old worker/request before installing/enforcing ownership guards or enabling connection-free processing. The bridge tests establish request serialization, not an automatic method of detecting live old binaries. Unknown active formats fail closed. Initial rollout overlap with unaware/unbounded workers is not covered by a finite connection budget.
4. Only after all routes/client recovery are compatible, request a separately approved low-traffic claims-only canary. Preserve idempotency/retries and every other current flag. Bound provider/memory concurrency separately from short DB pool capacity. Verify old/new aware-worker overlap using the same authoritative fencing protocol.
5. Monitor request latency, checkouts/physical sessions/pool waits, lock rejection/deadlock rates, pending/expired claims, stale-token refusals, replay/duplicate provider counts, evaluation/event uniqueness, profile increments and lesson revisions. Include resume/record-again/reload and physical Safari tests. No activation is implied by this document.

## Rollback

- Stop new claims-only admission and drain or explicitly invalidate in-flight operations using the guard-aware protocol. Preserve accepted recordings/transcripts, completed operation receipts, idempotency keys and committed progression.
- Return to **guard-aware bridge mode**, retaining advisory leases. Do not roll back to an unaware binary while new claims can exist. Do not delete operation tables, remove guards or force-release unknown DB cleanup merely to restore traffic.
- Resolve unknown commits by reading canonical persisted state. A client deadline or worker exit does not prove the provider stopped. Expiry/takeover must rotate tokens and keep old results from writing.
- If state invariants fail, pause affected writes and investigate from privacy-safe numeric/correlation evidence. Replaying operations must never duplicate evaluations, adaptation events or lesson advancement. Do not "repair" by deleting learner work.

## Remaining capacity limits

Only migrated claims-only upload/assessment/advancement paths eliminate provider-spanning connections. Legacy bridge and unconverted provider routes still retain them. Upload-only/full-exercise local session counts are not the complete production budget. After full lease removal, the prior conservative main3+accent2 budget remains5 per worker/10 across two overlapping aware workers plus other clients. Supavisor15 backends and200 clients remain separate limits. Production headroom, actual worker/thread count, and non-exercise/background traffic require fresh read-only verification before proposing rollout.
