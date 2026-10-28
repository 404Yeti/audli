# AUD-94 P0 remediation — review handoff

2026-10-28. Draft PR: https://github.com/404Yeti/audli/pull/113

**GO for code review and further controlled release preparation; NO-GO for production activation.** The local code blockers are remediated. Production backend identity, current pooler headroom, production wiring and physical acceptance remain gates. Nothing here authorizes a migration, role change, merge or deployment.

Code baseline: audit `58e47a0`; approved PR112 comparison `4d9988f`; tested remediation `cc04a31642cf6e5cc26701f828d5db09db21dd7a`. The PR is stacked on the unchanged PR112 branch. PR110/111/112 remain unmerged. Both Vercel project files disable previews for the remediation branch; Render previews are disabled and its deployment branch remains main.

## 1. Explicit migration chain and atomic permissions

Read-only production inspection still reports **001–006**, not007. The disposable clone is reconstructed from checked-in001–006 and checked against the production catalog: **172 columns, 89 constraints, 48 indexes**, exact equality. It reproduces relevant ownership, default ACLs, RLS-on-create behavior and synthetic role permissions. Local PostgreSQL17.10 versus production17.11 remains a patch-version difference.

Migration007 is an explicit prerequisite, including its advisory-coaching table; it does not enable the coaching feature. The normal migration runner still stops at007. It now recognizes a previously installed008 without attempting to install it. Neither application startup nor the runner activates claims or runs008 automatically.

`docs/postgres-008.sql` is a generated, numbered **review candidate**. It locks the ledger and requires the exact001–007 sequence. Unversioned claims tables or functions cause refusal rather than replacement. New tables, functions, guards, indexes, RLS and privilege revocations commit together with version008. Failure before the marker leaves no partial claims installation. It creates **no sequences**; the candidate runner refuses a future unreviewed sequence dependency.

All inherited privileges, explicitly including **TRUNCATE**, are revoked from PUBLIC and the browser/service roles for all five newly introduced tables (review007 plus four claims/audit tables). All client function EXECUTE access is revoked. RLS is forced on these tables; RLS alone is not a TRUNCATE defense. Tests execute real denied TRUNCATE statements as each client role, including the bypass-RLS service-role alias, and require SQLSTATE42501.

The local runner can grant the explicitly selected non-owner, non-bypass backend role within that same transaction. The backend receives DML on operation/slot tables and SELECT/DELETE on recovery audit for account erasure; it cannot append or update audit evidence. The separate pre-provisioned NOLOGIN operator role can read canonical state, lock profiles, update only claim status/slot fencing columns, and append/read audit evidence. It cannot edit learner evidence or delete audit rows. No role or membership was created in production.

The checked-in SQL artifact is owner-only, without invented production-role grants. After the actual backend role is verified, generate/review a role-specific migration using the tested runner/grant primitives. Do not assume the synthetic `audli_audit_app` role exists in production or execute the local-only installer there.

Startup accepts008 only if the required tables/columns, every learner-table guard, all five invoker functions and the deferred checkpoint-parent guard exist and are enabled. Tests reject missing columns, disabled guards and SECURITY DEFINER substitutions. SQLite remains schema007. Unknown/fabricated future ledgers still fail closed.

## 2. Planner GET and account-deletion ownership

`GET /api/plan` is now explicitly an owned GET in the compatibility bridge. It claims before lazy reconciliation; each transaction is fenced; a JSON response is acknowledged only after the receipt completes. An active or expired unresolved account operation produces409 without overwriting it. No provider is added to plan reads. This may temporarily make the plan view unavailable while the same account is processing; ordinary lesson recovery is preserved.

Account deletion is protected at the **actual mutation**, not by assuming its HTTP method. `app.account.delete_account` detects claims schema and refuses callers without the internal admitted-erasure callback, before deleting files or records. The local integration replaces the real POST route only when deletion is enabled, retaining live-session validation, password reauthentication and strict request validation.

The erasure transaction locks Auth identity first (matching initialization), then takes account advisory ownership, profile and slot locks. It refuses **all active slots, including expired claims**, until explicit recovery. It removes claim/audit dependencies and invokes the existing ordered application/Auth erasure in that same transaction. No connection is held during reauthentication. There is no generic receipt to finalize after the account has been removed. A successful acknowledgement follows commit; repeated stale-token deletion is rejected. Exceptions expose fixed diagnostics, not SQL parameters or account IDs.

Tests cover both sides of claim/delete races, repeat deletion, expired ownership, cancellation waiting for transaction outcome, storage refusal, late Auth deletion failure and rollback. They verify other accounts remain present and deleted identities cannot be recreated by late requests.

## 3. Authorized quarantine recovery

Local tool: `python -m experiments.release_readiness.recovery_tool --operation <opaque-operation-id>`.

The database URL is read from `AUDLI_TEST_POSTGRES_URL`, never from command arguments. The tool refuses non-loopback/non-disposable targets before connecting. Database role membership authorizes the operator; an HTTP client cannot supply an acceptance verdict. Ownership is resolved inside the authorized database session rather than placing learner IDs in shell history.

Procedure:

1. Stop incoming conflicting mutations and verify the old worker's disposition. Expiry is not proof that remote provider work stopped.
2. Inspect the operation and canonical-state hash. Completed results use canonical status/replay; native upload/assessment operations use their native recovery. The generic tool only recovers quarantined compatibility requests.
3. For an expired request, explicitly pass `--checkpoint`, `--acknowledge-unknown` and a reviewed `--reason` (`worker_terminated`, `deployment_interrupted`, `client_cancelled`).
4. In one transaction, lock/recheck ownership, compare canonical state, invalidate the old request, advance its fencing token and append audit evidence. Record database session/effective actor, reason, opaque operation, before/after fence, hash and timestamp. No audio, transcript, request body or password is copied to the audit.
5. Resume from canonical state. The result explicitly says **delivery unconfirmed / no automatic provider retry**. The old operation remains tombstoned; it cannot be blindly resent. Existing client status recovery and explicit recording replacement remain the recovery path. A changed hash requires reinspection, never an inferred successful upload.

Audit-write failure rolls back claim invalidation and permit release together. A late worker cannot commit. Repeating recovery cannot clear a newer claim. Operator privileges are exercised under a real restricted role in PostgreSQL, not only mocked authorization checks. Production credentials, support authorization/membership provisioning and packaging remain separately reviewed deployment tasks; this CLI is intentionally local-only.

## 4. Erasure and retention boundary

Production metadata confirms empty storage (zero buckets/objects), no custom Auth-user DELETE triggers, and cascading identity/session/MFA/OAuth/token/WebAuthn ownership constraints. SCIM/flow state with non-cascading ownership is handled explicitly by the existing eraser. The `postgres` role has SELECT/DELETE/UPDATE privileges on the touched Auth tables; **this does not establish that Render actually uses that role**.

Local rehearsal uses synthetic Auth tables with the observed ownership/FK actions and restricted backend grants. It verifies deletion of application rows, claims, audit records, identity, sessions, refresh tokens, flow/SCIM records and cascading MFA/OAuth/WebAuthn/token rows, preserving another account's rows. This is a managed-surface rehearsal, not a byte-for-byte Supabase Auth clone. It does not execute a real user deletion, signout or provider request.

Storage objects cannot be deleted safely by SQL metadata deletion. The supported empty-storage architecture is tested; any bucket causes a fail-closed response before erasure. A future nonempty storage deployment needs a reviewed Storage API deletion workflow, not this helper. Generated filesystem caches retain existing ownership/path checks and can be rebuilt from DB blobs after transaction rollback. Learner raw audio remains in-memory only.

Retention requirements:

* Preserve canonical evidence and operation identity for active/open/unfinished work. No age-only takeover or key deletion.
* After work is closed, minimization may remove sensitive operation JSON while retaining compact invalidated identity/fingerprint/fence tombstones, so old requests cannot resurrect work. No new retention duration is silently chosen.
* Account erasure removes its canonical records, claims, tombstones and recovery evidence. Shared catalogs and anonymized/spent aggregate budgets remain under existing policy; deletion does not refund consumed budgets.
* Database backups, platform/security audit records without stable ownership FKs, cloud logs, email/billing records and providers' own retention cannot be automatically erased by this transaction. Document approved retention periods and support procedures before production activation. JWT cryptographic validity is not revoked by deleting a row; live-session checks and absence of the Auth identity prevent restoration of the application account.

## 5. Read-only runtime and capacity findings

`results/p0-production-readonly.json` captures the final metadata/count snapshot: schema006; seven PostgreSQL client sessions, one Supavisor backend session, zero lock waiters; empty storage. No learner rows or credentials were read.

Render reports one service instance; source Docker command uses one worker, primary pool3/overflow0 and separate accent pool up to2. The current effective login, process tree and pooler-client occupancy are **not verified** by that source or by the management SQL connection. Read-only SSH stopped at host-key verification; it was not bypassed. `scripts/read_runtime_role.py` is prepared for the authorized Render Shell: it opens one short read-only connection and prints only role capabilities and aggregate counts.

Previously verified Supavisor settings were session-pool size15 and client limit200. They have different meanings: backend sessions consuming PostgreSQL capacity versus accepted pooler clients. One observed Supavisor backend is not proof that14 slots are available to Audli. Current utilization/reservations and effective worker counts require the pending Shell/dashboard output.

Projection, not a measured guarantee: fully migrated worker pools can retain up to5 physical sessions, or10 for two overlapping workers, plus other consumers. Guard-aware legacy bridge workers can still hold NullPool lease connections across AI; that exposure is not bounded by the main pool. Do not mix unknowable legacy demand into a supposedly bounded10-connection budget. All unaware workers must be retired before ownership activation. Pooled leases, existing flags and pool sizes remain unchanged.

Potential configuration work, **not applied**: provision/review the backend and operator roles/membership; if runtime uses an owner/BYPASSRLS login, review a dedicated least-privilege backend credential; verify actual pooler capacity and deployment-overlap limits. Do not increase pools or enable pooled leases to conceal missing headroom.

## 6. Correctness and CI evidence

* 105 local claims/release tests passed (66 original,22 audit,17 P0). The17 P0 cases passed again after final startup-manifest checks and real TRUNCATE-denial coverage.
* Initial broad local PostgreSQL run:2995 passed,82 skipped,5 failed. Three tests had stale schema008-as-future assumptions; two diagnostics exposed an unnecessary new startup query. Corrected code/tests reran successfully:77 passed,21 expected skips across affected suites. Incomplete008 still fails; this was not a waived compatibility check.
* Remote CI **all eight checks green at code commitcc04a31**, run https://github.com/404Yeti/audli/actions/runs/40642926098 : Backend, PostgreSQL integration, both schema rollback jobs, Frontend, Browser, Security and CI required. The PostgreSQL job now includes the release-readiness regressions. Use PR checks for any subsequent evidence-only head.
* Local Chromium recovery14 passed. Ubuntu26.04 WebKit could not be certified: required host libraries were unavailable, and a private-library launch later crashed pages. No timeout/assertion was changed. The supported Ubuntu24.04 remote Browser job passed instead. Browser/physical-device equivalence is not claimed.

All development/rehearsal providers are deterministic mocks. CI clears provider credentials and enforces offline test isolation. No provider credits were spent.

## 7. Fresh full-path benchmark

Both runs were sequential on the same otherwise quiescent local server:72 ms simulated DB RTT; primary pool3/overflow0;300 ms mocked auth;1.1 s STT;3.5 s assessment;32 executor threads; unchanged P2 deadline/three-send policy. Two waves at1/2/4/8/16 learners. Each flow includes two uploads, two assessments including a follow-up, readiness and persisted progression; generation/TTS excluded.

Baseline is exact4d9988f with its original schema installer. Remediation is exactcc04a31, reconstructs schema006/default ACLs, explicitly applies007/008 and runs requests as non-owner/non-bypass `audli_audit_app`. Both recorded clean source trees; raw source/package hashes are included. Permission/schema differences are intentional treatment differences, not an identical-schema microbenchmark. Startup/setup is outside latency windows. No other local test processes ran during measurement.

Ranges below are the two independently computed wave percentiles, **not pooled quantiles**:

| Learners/wave | PR112 p50 seconds | Remediated p50 seconds | PR112 p95 seconds | Remediated p95 seconds |
|---|---:|---:|---:|---:|
|1|18.26–18.37|18.35–18.58|18.26–18.37|18.35–18.58|
|2|18.18–18.19|18.28–18.29|18.20–18.22|18.30–18.32|
|4|18.44–18.75|18.55–18.78|19.61–19.96|19.60–20.03|
|8|22.35–22.35|22.06–22.59|23.16–23.25|23.05–23.50|
|16|33.90–33.95|33.94–34.00|35.41–35.54|35.52–35.55|

Each architecture: **62/62 automatic, zero manual recoveries;124 STT and124 assessments; zero extra STT/duplicate assessments; peak3 application physical sessions; zero request-owned connections during mocked STT; one send per recording**. The normal-path benchmark does not itself validate lost-response replay; fault/duplicate tests do.

At16, baseline connection-seconds101.04–101.50 versus remediation101.65–102.08; recorded pool-wait p95 baseline1.285–1.298 s versus remediation1.236–1.290 s. At8, connection-seconds50.64–51.02 versus50.70–50.86; recorded pool-wait p95 baseline529–550 ms versus544–560 ms. Physical-session counts exclude observer/admin sessions and are not Supavisor telemetry.

No material new regression was observed: mean wave median changed about+0.8% at1 and+0.13% at16. Two waves are not statistical proof or a production capacity claim. The prior historical~26.7-second optimized median is still below both current builds; this sprint did not attempt another latency optimization. The sustained pool waits at16 still argue against activation at that concurrency without capacity/load acceptance.

Raw results: `results/p0-pr112-baseline.json`, `results/p0-remediated.json`. Harness supports `--schema installed` and `--schema migration8`; no pool increase or timeout relaxation was used.

Artifact provenance note: the secret scanner classified18 source SHA256 values next to API filenames as generic API keys. Each value was independently verified against the source file. The manifest now separates `path` and `sha256` into array records, retaining every hash and measurement. The unreviewed evidence commit was replaced; no scanner rule or allowlist was weakened. Runtime application code remains the measuredcc04a31 source.

## 8. Deployment, rollback and risk register

Proposed sequence **after separate approval**:

1. Obtain the actual backend-role snapshot, current session-pooler utilization/configuration and effective worker/process count. Approve role/membership and retention decisions. Confirm provider budget and physical acceptance arrangements.
2. Promote/package the local integration/operator primitives into a separately reviewed production startup/activation mechanism. Current production startup does not import the experimental installer. Keep dormant protocol support distinct from activation.
3. Deploy a guard-aware, schema006/007/008-compatible bridge before migration. Retire every unaware worker. Ensure the rollback binary supports008 and all conflicting writers. Never activate ownership merely because a version marker exists.
4. Pause incoming mutations externally; certify drain; resolve each expired/unknown outcome canonically. Snapshot public data/ACLs without learner-data exports in logs. Apply explicitly approved007 then008/role-specific atomic hardening. An interruption between007/008 leaves supported additive007. Recheck ledger, function/guard manifest, effective privileges, sequences and health before reopening traffic.
5. Admit a separately approved low-traffic cohort, then perform physical iPhone tests: normal Wi-Fi/cellular lesson, connectivity interruption, retained recording/Send again, explicit Record again, cached Replay question, Safari background/return, lock/unlock, reload disclosure and stale-callback rejection. Correlate opaque operation/upload IDs, numeric timing/send counts and recovery outcomes only.

Monitoring/stop thresholds to approve against a real baseline: reserve at least3 of15 backend slots; stop expansion at>=12 sustained60 seconds; immediate stop on pool exhaustion/connection timeout. Pause expansion for recorded pool-wait p95>500 ms or comparable request p95>20% above baseline for5 minutes. The16-learner lab already exceeds the proposed pool-wait threshold; this is not permission to activate16 learners. Stop immediately for stale commits, cross-account access, lost evidence, duplicate progression/evaluations, unexplained quarantine, persistence errors or recording loss.

Rollback: pause new mutations, retain claims/audit/tombstone tables, let transactions settle, inspect all active slots and recover unresolved outcomes explicitly. Revert only to a guard-aware008-compatible binary with adequate legacy-connection capacity. If drain cannot be certified, keep mutations paused. Empty-schema DDL rollback is rehearsed and preserves007. Populated rollback refuses deletion; never truncate receipts or roll back to an unaware binary. Current flags/providers/routing remain unchanged.

| Risk | Evidence / mitigation | Remaining gate |
|---|---|---|
|Missing007 / partial008|Explicit chain, atomic failure tests, full startup manifest|Review and approve role-specific production artifact|
|Inherited TRUNCATE / public access|Atomic revocations; real denied statements; restricted-role tests|Actual runtime identity and grant review|
|Planner/deletion bypass|Owned GET; mutation-level erase callback; race/expiry tests|Production integration wiring and physical acceptance|
|Uncertain compatibility outcome|Authorized operator; canonical hash; fencing; atomic audit|Provision production authorization/support procedure; no automatic provider replay|
|Incomplete erasure|Public/Auth FK rehearsal; restricted grants; storage fail-closed|Managed Auth grant verification; backup/log/provider retention policy; future storage workflow|
|Pooler exhaustion / overlap|Peak3 measured; source pool projection; no settings changed|Current pooler/client utilization and all old workers accounted for|
|Latency / acceptance|Fresh controlled baseline comparison; no material added regression|Real-device acceptance; high-concurrency pool waits remain|

Manual inputs still required: the authorized Render Shell JSON from `scripts/read_runtime_role.py`, current Supabase session-pooler dashboard values/utilization, policy decisions and a physical iPhone test after deployment/provider testing is separately authorized. A management-role query or mocked benchmark cannot substitute for these.
