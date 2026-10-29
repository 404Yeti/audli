# AUD-94 / PR #112 release-readiness audit

> Historical stage evidence. Current integrated deployment and rollback authority: `docs/AUD-94-RELEASE-CANDIDATE.md`.

2026-10-28. **NO-GO for production.** Approved application source: `4d9988faee46c1c3cdede7d7034deffffeec5dfd`. No production writes, deployments, provider calls or merges were performed. PRs #110/#111 remain outside this work.

Audit candidates and rehearsals live separately on `codex/AUD-94-release-readiness`; they are not fixes already present in PR #112. In particular, atomic installation, ledger version 8, quarantine recovery and retention helpers below are local candidates only. Production activation remains unapproved.

## Verified schema and permissions

Read-only production catalog queries found schema versions **1–6**, 25 public application tables, 172 columns, 89 constraints and 48 indexes. The disposable public-schema clone matches these columns, constraints and indexes exactly; see `results/schema-comparison.json`. No learner rows, credentials, audio or transcripts were read from production.

Production application tables are owned by `postgres`, with RLS enabled, not forced, and no public policies. `postgres` and `service_role` bypass RLS; anon/authenticated do not. Existing service-role table ACLs grant `Dxtm` (TRUNCATE, REFERENCES, TRIGGER, MAINTAIN), not ordinary data access. More importantly, postgres-created tables inherit those privileges for anon/authenticated/service_role. **RLS does not protect against TRUNCATE.** Do not rely on ENABLE RLS alone for new internal tables.

Public schema has client USAGE, not client CREATE. A managed event trigger enables RLS on new tables. Proposed functions use SECURITY INVOKER with a fixed search path. The candidate explicitly revokes table and function grants from PUBLIC and all client roles, forces RLS on internal tables, and grants only reviewed backend DML/function access in the same transaction. Backend-role policies permit trusted server access across accounts; owner validation and fenced SQL are still the authorization boundary. They are not end-user RLS policies.

The application's actual production database login and inherited privileges remain unverified. The catalog connection's role does not establish the Render application's role. Before release, obtain a credentials-free, read-only runtime classification of current_user/session_user, superuser/BYPASSRLS, membership and effective grants. Do not rotate credentials as part of this audit.

## Migration and rollback rehearsal

Local PostgreSQL **17.10** versus production **17.11**; synthetic NOLOGIN role aliases reproduce relevant table ownership, ACLs, default privileges, RLS and RLS-on-create behavior. This is equivalent for the public application boundary tested, not a complete Supabase Auth/storage/managed-extension clone. Local server is loopback-only and providers are deterministic mocks.

Confirmed release blockers:

* PR #112's installer references `review_improvements` from optional migration 007, which is absent in production. Decide explicitly whether to ship 007 or remove the dependency; do not silently install it.
* PR #112 is still a local-only installer, not a numbered production migration. Its original multi-transaction installation is unsuitable for inherited production grants.
* A proposed ledger version 8 is rejected by existing repository startup. A guard-aware compatibility release must accept all reviewed versions before DDL or ownership activation.

Candidate rehearsal: one transaction installs explicitly authorized 007 plus claims tables/functions/guards, hardens grants, creates recovery indexes and writes version 8. Injected failure after DDL rolls everything back to version 6 with no claims tables. Successful empty rollback removes version 8/claims objects and preserves additive version 7. Populated rollback is refused rather than deleting operation receipts. This is a candidate design, not an approved deployment script.

Indexes include owner/primary-key replay access, account slots, `(owner,status,expires)` recovery lookup and the owner/exercise/attempt-expression lookup for completed assessment uncertainty. No production index was created or benchmarked. The benchmark uses the approved source's indexes, not these new candidate indexes.

## Quarantine, erasure and retention

`quarantine.py` provides a local operator-only recovery primitive: pause mutations; inspect owner-bound operation and canonical-state hash; require expiry and explicit acknowledgement of uncertainty; compare hash again in the transaction; invalidate the old operation and increment the fencing token without changing canonical learner state. Same IDs remain tombstoned. Late callbacks cannot finalize. It never infers delivery from unrelated revisions or repeats providers automatically. Mid-cleanup failure rolls back invalidation and slot release together.

Tests cover eight synthetic compatibility labels with and without a persisted intermediate checkpoint. These exercise the shared primitive, **not eight complete optional-route HTTP workflows**. Existing HTTP tests cover check-in, generation, assessment, closing and cancellation. A production authenticated operator tool, audit policy, support UX and per-route partial-outcome decisions remain required. Provider execution cannot generally be exactly-once after a crash between provider completion and durable receipt.

`retention.py` rehearses public-account erasure: fence active ownership, remove operation/slot dependencies, then run existing public erasure. Other accounts remain unchanged; old callbacks cannot commit. It does not rehearse Supabase Auth session revocation, auth-table deletion, storage buckets or filesystem deletion. Existing account deletion does not include the new claim dependencies and must be integrated before activation.

Minimization refuses active claims, open lessons and unevaluated exercises. It clears private operation JSON while retaining compact invalidated identity/fingerprint/fence tombstones. No arbitrary time-to-live deletion of replay identity is safe yet; retention duration and storage budget need approval. Canonical lesson/evidence retention is unchanged.

## Authoritative route coverage

Native claim/fence paths: exercise upload, assessment/follow-up/final evaluation/adaptation, readiness, lesson advance, reset and explicit recording replacement. Full integration restores original lesson closing/projection while admitting remaining mutation requests through compatibility ownership. Check-ins, legacy evaluation, generation/TTS, onboarding, homework, planner POSTs, profile changes and review improvement use that compatibility bridge. Database guards cover the owner-bearing application tables, including writes outside repository transaction helpers. Telemetry POST is intentionally exempt from learner-state ownership.

GET lesson/eligibility settlement participates in lifecycle handling. GET exercise audio and tutor-voice preview participate in provider admission. Ordinary status/read routes bypass admission.

**Coverage is not complete:** `GET /api/plan` calls `repo.roadmap()` → planner reconciliation and can persist a changed roadmap, but is classified as an ordinary GET by the bridge. Database guards protect conflicts; this is not explicit durable ownership and may surface a conflict to a reading client. Make it genuinely read-only or admit reconciliation explicitly, with concurrency tests.

**Account deletion is POST `/api/account/delete`, not DELETE.** The bridge's method-based DELETE refusal does not protect that actual route. If enabled, it reaches reauthentication and the old deletion implementation under a compatibility claim. It needs an explicit early gate or fully integrated drain/erasure protocol. Do not describe it as safely disabled by the DELETE check. No real deletion/authentication was attempted.

The catch-all bridge provides fail-closed admission for new mutation routes but is not evidence that every optional feature's provider/cache/state semantics have been fully rehearsed. Keep a route inventory CI gate when adding routes.

## Overlap, drain and interrupted deployment

Local verification: **66 existing assessment/integration tests passed**, plus **22 new release-rehearsal tests passed**. The approved PR112 remains OPEN/DRAFT at the exact reviewed commit, with all eight required CI jobs green. Browser CI was verified on that head; it was not rerun locally for the separate audit-only helpers.

Existing PostgreSQL/HTTP tests rehearse guard-aware bridge/claims workers in both arrival orders, legacy advisory ownership blocking new admission, duplicate uploads/assessments, expiry, cancellation during providers/final commits, reset, no stale writes and fast-following acknowledgements. Drain certification rejects unknown active IDs, expired unresolved operations and rollback to unaware workers. New local tests rehearse interrupted transactional DDL, startup version rejection and refusal of populated destructive rollback.

This does **not** certify a real rolling deployment of old production workers. Prerequisites: every overlapping worker is guard-aware and schema-compatible; traffic is paused externally while drain is certified; no process remains able to create new work after the snapshot. A database snapshot alone does not stop admissions. Never rollback directly to an unaware pre-bridge binary. Never delete active slots or use expiry alone as proof that provider work stopped.

## Exact-head performance

Two waves per concurrency, same 72 ms simulated DB RTT, pool 3/overflow 0, 300 ms auth, 1.1 s mocked STT, 3.5 s mocked assessment, unchanged P2 deadline/three-send policy. Each flow includes two uploads, two assessments, readiness and persisted advancement. Generation/TTS and physical network behavior are excluded. Source hashes and raw results are committed. Python 3.12.13 locally versus previously verified production 3.12.16. This is a lab measurement, not production capacity certification.

Full integration at exact `4d9988f`; ranges are the two separately computed wave percentiles, not pooled percentiles:

| Learners/wave | Automatic | p50 seconds | p95 seconds | Connection-seconds/wave | Pool-wait p95 ms |
|---|---:|---:|---:|---:|---:|
| 1 | 2/2 | 18.25–18.58 | 18.25–18.58 | 6.44–6.64 | none |
| 2 | 4/4 | 18.21–18.21 | 18.26–18.27 | 12.74–12.76 | none |
| 4 | 8/8 | 18.75–18.76 | 19.57–19.59 | 25.31–25.38 | 362–373 |
| 8 | 16/16 | 22.14–22.25 | 23.05–23.18 | 50.55–50.81 | 537–568 |
| 16 | 32/32 | 33.55–33.64 | 35.38–35.58 | 101.24–101.88 | 1,247–1,266 |

**62/62 automatic completions, zero manual recoveries, 124 STT and 124 assessments, zero extra STT or duplicate assessments.** Each recording was sent once: this normal-path benchmark does not itself exercise replay. Per 16-learner wave: 32 attempts, 16 final evaluations and 16 adaptation events, as expected. Peak application physical sessions: **3** throughout; maximum request-owned connections during mocked STT: **0**. Physical-session metric excludes monitoring/admin sessions. It is not Supavisor utilization.

A second exact-head native-only run also completed 62/62; 16-learner p50 33.76–33.78 s, p95 35.37–35.83 s, connection-seconds 101.08–101.46. Thus disabling the final compatibility installer alone does not explain the result.

Historical optimized `4e5ed23` results were about 16.50 s single-learner and 26.73/27.88 s p50/p95 at 16, with 72.51 connection-seconds. Final-head integration is roughly **26% slower in 16-learner median**, with ~40% more connection occupancy. These are historical comparisons, not a fresh controlled A/B of the old binary. Do not reuse the older figures as final release evidence. Pool waits are substantial at 16; profile before any claim of unchanged performance. No optimization was attempted in this audit.

## Proposed deployment and recovery checklist — NOT authorized to execute

Preconditions:

1. Resolve schema 006/007 dependency and ship reviewed atomic versioned DDL, privilege revocations and security tests; prove startup compatibility before changing the ledger.
2. Verify the actual backend role, managed Auth/storage erasure permissions and complete erasure behavior; approve retention/tombstone policy.
3. Resolve mutating GET planner admission, account-delete integration and production quarantine/operator recovery. Add endpoint-level optional-feature tests.
4. Ship/rehearse a schema-compatible guard-aware bridge separately; all overlapping and rollback workers must participate. Obtain actual Supavisor utilization and configured worker/pool limits. The 15 backend slots and 200 client limit are different constraints; free PostgreSQL slots do not establish pooler headroom.
5. Reconcile final-head latency regression and pass complete backend/PostgreSQL/Chromium/WebKit CI for the eventual revised release commit. Existing eight green jobs attest to PR112's approved head, not these unpushed audit candidates.
6. Obtain separate activation/provider-budget approval and physical iPhone acceptance. PR110/111 remain unmerged.

Deployment sequence after approval: capture flags/revisions and utilization; pause mutation admission externally; drain every active claim or perform reviewed canonical recovery; apply additive migration atomically; deploy only guard-aware schema-compatible workers; verify health/capabilities/ACLs and no claims drift; enable only a separately approved bounded cohort; observe before expanding. Preserve all existing recording/retry/provider settings.

Proposed initial monitoring thresholds, to calibrate against an actual production baseline:

* Reserve at least 3 of 15 pooler backend slots. Stop expansion at >=12 sustained for 60 s; stop immediately on any connection/pool timeout. Count every overlapping worker and background/admin consumer.
* Stop expansion on pool-wait p95 >500 ms for 5 minutes or comparable request p95 >20% above baseline for 5 minutes. The 16-learner lab result already exceeds the proposed wait threshold; do not approve that concurrency from this evidence.
* Immediate stop/rollback on cross-account access, stale commit, duplicate persisted progression/evaluation, missing evidence, new persistence errors, unexpected claim takeover or recording loss.
* Pause admissions on any unexplained quarantine; inspect operation ID/kind/status/fence/timing and outcome counts only. Never log tokens, audio, private transcripts or learner identifiers. Correlate only opaque upload/operation IDs with limited retention.

Rollback: pause new mutations first; retain claims tables/receipts; settle in-flight transactions; inspect drain report; recover unresolved operations individually without automatic provider repetition; then revert only to a guard-aware schema-compatible binary. If drain cannot be proven, keep traffic paused and escalate. Do not drop populated claims schema or roll back to unaware code. DDL rollback is only for the rehearsed empty-schema case.

## Physical iPhone Safari — manual action required

No physical iPhone is available to this agent. No provider-backed acceptance was run. After a separately authorized test deployment and budget:

1. Normal Wi-Fi and cellular: complete check-in, short affirmative/negative answer, substantive answer, follow-up and final completion. Verify persisted progression and exactly one accepted attempt/final assessment.
2. Interrupt upload with airplane mode, restore connectivity; verify retained recording and stable upload ID, at most three actual sends, bounded failure/status recovery and no question replay or duplicate provider execution while an operation is processing.
3. Send again reuses the recording/ID; Record again explicitly invalidates old work before capture; Replay question reuses cached audio where available. Verify a late old callback cannot replace the new answer.
4. Background Safari and return; repeat with lock/unlock during sending and assessment. Verify no silent overwrite or false delivery confirmation and status recovery after reconnect.
5. Reload during sending/processing and after an uncertain outcome. Verify lost in-memory audio is disclosed, completed canonical state is adopted only with authoritative evidence, and explicit recording action is offered where necessary.
6. Record UTC time, iOS/Safari version, network condition, opaque upload/operation ID, send count, total timing and recovery outcome. Do not export HARs containing authorization/cookies/audio or private transcripts. Stop on any safety trigger above.

Remaining manual inputs: runtime backend-role classification and pooler utilization; security/retention policy decisions; separately approved deployment/activation; physical device tests. They cannot be replaced by mocked CI.

## Reproduction

Use an owned disposable loopback PostgreSQL server on port 55449 with `pg_stat_statements` preloaded, plus `scripts/pg_delay_proxy.py --listen 55451 --target 55449 --one-way-ms 36`. No production URL or credential is needed. The lab helper refuses non-loopback hosts and drops only its generated disposable database.

```
python -m pytest -q experiments/assessment_claims
python -m pytest -q experiments/release_readiness/test_readiness.py
python -m experiments.release_readiness.benchmark \
  --root /path/to/clean-4d9988f-checkout \
  --claims-root /path/to/clean-4d9988f-checkout \
  --mode staged --installer integration \
  --output experiments/release_readiness/results/pr112-integration.json
```

Repeat with `--installer native` and a distinct output. Keep the exact-head checkout clean: candidate migration/security/recovery changes in this audit branch were **not** used in the benchmark. `source_hashes` identifies the original assessment package; hash this new harness separately when reproducing. Re-running tests compares against committed catalog snapshots and performs no production metadata read.
