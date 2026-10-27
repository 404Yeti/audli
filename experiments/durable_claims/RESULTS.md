# AUD-94 — Results and review recommendation

Local proof of concept complete. **Proceed only with a further local round-trip reduction experiment; do not deploy this implementation or start full migration yet.** Durable claims prove the connection-ownership model, but this straightforward implementation materially regresses upload latency.

## Measured matrix

Two synchronized waves at each concurrency, 62 requested uploads per architecture. Real HTTP/PostgreSQL, nominal 72 ms database RTT, 300 ms mocked authentication, 1,100 ms deterministic mocked STT, actual P2 sender and unchanged policy (three sends, 50-second wall deadline). All runs use the same 32-thread executor and main pool of three with zero overflow. See README for exclusions.

Latency is policy-read start through completed upload acknowledgment, **not speech-end-to-Pip audio**. p50/p95 pool successful requests across both waves; connection-seconds are average totals per wave; physical sessions are the maximum observed per wave. Low-concurrency percentile samples are small and do not establish statistical significance.

| Architecture | Concurrent learners | Completed / requested | p50 / p95 (s) | Peak physical sessions | Connection-seconds / wave | Pool waits (both waves) | Worst-wave wait p95 (ms) | STT calls / duplicates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| current | 1 | 2/2 | 3.86 / 4.17 | 4 | 4.00 | 0 | 0.0 | 2 / 0 |
| current | 2 | 4/4 | 3.49 / 3.51 | 5 | 7.93 | 0 | 0.0 | 4 / 0 |
| current | 4 | 8/8 | 3.55 / 4.22 | 7 | 16.61 | 13 | 392.7 | 8 / 0 |
| current | 8 | 16/16 | 4.37 / 5.15 | 11 | 37.92 | 57 | 783.1 | 16 / 0 |
| current | 16 | 32/32 | 5.83 / 7.84 | 19 | 99.16 | 139 | 2173.7 | 32 / 0 |
| combined | 1 | 2/2 | 3.94 / 4.25 | 4 | 3.99 | 0 | 0.0 | 2 / 0 |
| combined | 2 | 2/4 | 3.55 / 3.57 | 4 | 4.19 | 0 | 0.0 | 2 / 0 |
| combined | 4 | 2/8 | 3.57 / 3.60 | 4 | 4.65 | 5 | 70.8 | 2 / 0 |
| combined | 8 | 3/16 | 3.60 / 6.09 | 4 | 7.50 | 28 | 152.4 | 3 / 0 |
| combined | 16 | 4/32 | 5.03 / 6.48 | 4 | 11.60 | 75 | 464.0 | 4 / 0 |
| claims | 1 | 2/2 | 4.66 / 4.68 | 3 | 2.85 | 0 | 0.0 | 2 / 0 |
| claims | 2 | 4/4 | 4.65 / 4.68 | 3 | 5.72 | 0 | 0.0 | 4 / 0 |
| claims | 4 | 8/8 | 4.67 / 6.05 | 3 | 11.23 | 12 | 915.4 | 8 / 0 |
| claims | 8 | 16/16 | 6.87 / 8.89 | 3 | 22.56 | 52 | 1551.8 | 16 / 0 |
| claims | 16 | 32/32 | 17.50 / 19.21 | 3 | 55.96 | 196 | 4799.1 | 32 / 0 |

At 16 learners, claims reduce checked-out connection-seconds by 43.6% and peak sessions from 19 to 3. Successful-upload p95 increases from 7.84s to 19.21s. This is a capacity/correctness result, not a latency win.

`current` is source 732e420. `combined` is local scratch source #111 at 272e41c with the #110 app changes from 4e51c16, with admission enabled at one, immediate503, no queue. `claims` is the local standalone-upload prototype; other mutation routes retain old ownership. Source/package hashes are in results/provenance.json and per-run source hashes. No production configuration was read or changed.

Current completed 62/62 with 62 STT calls and 62 attempts. Combined completed 13/62 with 13 calls/attempts; 49 recordings needed manual recovery in this burst workload. Claims completed 62/62 with 62 calls/attempts. Every benchmark invocation matched a unique stable recording; zero duplicate STT calls in the normal matrix. No request exceeded three actual audio sends. Normal-matrix server work settled with zero outstanding checkouts.

Current request-owned checked-out connections at STT: 1. Combined: 1. Claims: 0. Main pooled physical sessions remain connected, shared by learners; no claim that all physical database sessions disappear. The upload-only workload does not exercise the separate accent pool. The local server has capacity above 15, so current 19-session success cannot be extrapolated to the production 15-backend session pool.

## Why latency regresses

An ordinary one-learner upload issues 15 SQLAlchemy-observed statements on current/combined versus 33 on claims. The prototype deliberately uses explicit ownership, slot, operation, expiry, checkpoint and legacy-replay queries; its claim/finalization transactions are shorter than provider-spanning leases but still expensive across a 72 ms link. At 16 learners these sections queue behind the three-connection main pool. P2 timeouts introduce additional replay reads and sends, increasing contention. No additional STT is caused in this matrix.

## Correctness evidence

`117 passed, 1 skipped` across 27 prototype PostgreSQL tests plus existing upload idempotency, conversation and per-learner locking regressions, using both PostgreSQL and SQLite where those suites support them. The skip is the SQLite parameter of a PostgreSQL-only account-advisory-lease test. The full application suite and remote CI were not run; no PR or production application code is changed.

- 16 simultaneous duplicate claims produce one owner; eight competing finalizers produce one attempt and pending checkpoint.
- In-flight duplicate HTTP uploads return 202, status stays processing until committed, and a later upload replays the same attempt without STT. Another account cannot read its status.
- An actual killed subprocess leaves an expiring claim; takeover receives a higher fence. Expired/stale workers cannot overwrite state. Completed canonical replay is permitted without a second write.
- Transaction failure after insert rolls back attempt, checkpoint and completion together. Reset invalidates the running token; changed question and unresolved pending evidence reject writes.
- Cancellation before starting, during claim, during STT and during finalization is covered. A deterministic cancellation-deadline test leaves uncertain work fenced and observes the late thread; a reset prevents its later commit.
- Concurrent independent learners enter mocked STT with no checked-out connections. Low-confidence recognized audio remains subject to the unchanged server assessment gate: 422 uncertain, no evaluation, adaptation or completed-attempt update.
- Real middleware tests for old-first and bridge-first overlap preserve the original PostgreSQL advisory lock, result replay, one provider call and one persisted attempt.
- A deliberately expired claim while its old mock provider is paused produces two STT invocations but exactly one persisted attempt/checkpoint. This is the unavoidable external-call ambiguity window, not a failure of persisted idempotency.

## Migration and deployment risks

1. Bridge mode retains long-lived leases and gives no capacity gain. Claims-only must not coexist with an unaware legacy writer. The prototype precondition is not runtime detection; all writers need a reviewed bridge protocol and a verified drain before activation. Rolling rollback to unaware code is also unsafe without a drain.
2. This implements standalone upload acceptance only and conservatively rejects accounts with lesson history. It does not migrate check-in, assessment/adaptation, generation, onboarding, end/reset/expiry or GET settlement. Exactly-once final learner progression remains a full-architecture proof obligation.
3. The experimental202/status protocol is not implemented in the production web client. Stable upload IDs and the existing three-send cap remain unchanged. Background/reload disclosure and physical Safari behavior still require integration acceptance.
4. A 60-second claim TTL can exceed the existing 50-second delivery deadline. The crash tests prove safe takeover, not automatic recovery within the current client deadline. Provider timeouts/renewal/expiry need design without changing retry policy implicitly.
5. Provider execution/billing cannot generally be exactly once. Worker death before durable transcription loses only server-memory audio; recovery requires the client-held recording or an explicit re-record after reload. No durable audio persistence was introduced.
6. Session pooling retains physical app-pool connections. After full lease removal, the earlier conservative budget remains 5 per worker and 10 across two overlapping workers, plus other clients; the upload-only observed 3 is not a replacement for that budget. Production Supavisor pressure and Render resource limits are not simulated.
7. Missing production work includes schema/version/security review, all-writer fencing, provider/memory concurrency bounds, operation retention, monitoring and reversible bridge/drain procedures. No activation is proposed here.

## Recommended next step

Approve a separate local iteration to batch claim/finalization reads and conditional writes (owner-scoped CAS/RETURNING or a tightly reviewed transactional database function), preserving all proof obligations. Rerun the same matrix and long-tail mocked STT/crash profiles. Target single-upload latency close to current and no material p95 regression at concurrent load, while retaining bounded sessions and complete automatic delivery. Do not hide this regression by increasing pools, admission limits, retry deadlines or production capacity.

PR110 remains useful for event-loop responsiveness and whole-transaction offload. PR111 remains useful for lease-bearing bridge/legacy routes; its JSON acknowledgment barrier is unnecessary on fully migrated routes once final transaction cleanup precedes acknowledgment. Both PRs remain OPEN at their reviewed heads and were neither merged nor deployed.

## Artifacts

- `results/current.json`, `combined.json`, `claims.json`: raw numeric request/wave results and runtime/package/source hashes.
- `results/summary.json`: pooled latency and aggregate comparison above.
- `results/provenance.json`: source revisions, important app-file hashes and dependency versions.
- `results/tests.txt`: final focused regression result.
- `README.md`: architecture, scope and reproduction instructions.
