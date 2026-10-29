# AUD-94 — Optimization results

**GO to continue a staged, reviewed architecture migration; NO-GO for production activation or removing remaining middleware leases.** The local upload proof meets the observed latency and connection targets. Full authoritative progression and all-writer migration remain unfinished.

## Fresh comparison

The same instrumented harness reran current source `732e420`, the original claims algorithm frozen at `9d9fc7e`, and optimized claims. Two waves per concurrency, 62 requested uploads per variant. Fixed 300 ms mocked auth, 1100 ms mocked STT, 72 ms nominal database RTT, pool 3/overflow 0, 32 executor threads, unchanged P2 policy. Latency includes policy read through upload acknowledgment, not speech-end-to-Pip. Percentiles pool successful uploads across both waves; low-concurrency samples are small.

| Learners | Current p50/p95 (s) | Original claims p50/p95 (s) | Optimized claims p50/p95 (s) | Completion per variant |
|---:|---:|---:|---:|---:|
| 1 | 3.86/4.17 | 4.67/4.69 | 2.59/2.60 | 2/2 each |
| 2 | 3.49/3.53 | 4.64/4.66 | 2.69/2.81 | 4/4 each |
| 4 | 3.52/4.22 | 4.68/6.04 | 2.58/2.85 | 8/8 each |
| 8 | 4.40/5.20 | 6.85/8.91 | 2.94/3.27 | 16/16 each |
| 16 | 5.91/7.68 | 17.50/19.17 | 3.80/4.48 | 32/32 each |

At 16 learners, optimized claims reduces p50 by 78.3% and p95 by 76.6% versus original claims. Connection-seconds fall 78.0% versus original claims and 87.7% versus current architecture. All variants completed 62/62 with 62 STT calls and 62 attempts; zero duplicate provider calls in the normal matrix.

| Architecture at 16 learners | Peak sessions | Connection-seconds/wave | SQL statements/wave | Server execution ms/wave | Pool waits, two waves | Worst-wave pool-wait p95 (ms) | Sends, two waves |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 19 | 100.02 | 240 | 13.80 | 138 | 1869.7 | 32 |
| claims | 3 | 55.64 | 645 | 32.40 | 196 | 4571.4 | 58 |
| optimized | 3 | 12.26 | 96 | 25.31 | 120 | 828.1 | 32 |

Full 1/2/4/8/16 connection, query, wait and provider figures are in `results/optimization/summary.json`; raw request/statement profiles are in the three variant JSON files. Observed DB lock-wait samples were zero for independent learners at 10 ms sampling; this does not rule out shorter waits. Original and optimized claims held zero request-owned connections at STT; current held one. Idle physical pool sessions remain connected. The local server allows more than 15 sessions, so legacy 19-session success is not proof of Supavisor feasibility.

## Before/after ordinary upload profile

| Stage | Original client SQL calls | Optimized client SQL calls | Transaction boundary |
|---|---:|---:|---|
| Authenticated policy/upload request scopes | 2 | 2 | Short existing reads |
| Claim | 14 | 2 | One explicit transaction; commit before STT |
| Finalize | 17 | 2 | One explicit atomic transaction after STT |
| Total | 33 | 6 | No split transactions or provider-spanning connection |

The savings batch required owner/slot/operation/question reads, eliminate duplicate ownership lookups within a locked snapshot, reuse database-clock values, and avoid rewriting unchanged conversation child projections. Required checks remain in the batched PostgreSQL reader and Python validation. Claim hashes and stored records remain bidirectionally compatible with the original PoC. SQL templates and exact counts are recorded without bound values.

At one learner, original claims spends about 2.85 s of checked-out DB time while PostgreSQL top-level execution averages only 4.3 ms. Optimized checkout time is about 0.78 s. This is predominantly round-trip reduction, not faster STT or relaxed validation. The helper function adds some local server CPU, but its absolute cost remains milliseconds; nested execution totals must not be added to parent totals. Planning time is not separately measured.

## Why the original 16-learner median was 17.50 seconds

The fresh matrix reproduces the historical result. Long read/check/write sequences hold all three main-pool connections for many network round trips. Requests queue, cross the first-send timeout and retry while the original server work continues. Original claims issues 645 statements per 16-upload wave versus 528 without retries; 29 audio sends replace the 16 first sends. Retries do not repeat STT, but their reads/authentication amplify queueing.

A separate interval-correlated diagnostic (same algorithm, timings and pools; not folded into the matrix) reproduces that behavior. Values below average the middle two recordings defining each16-learner median. Components are interval unions per recording across retries; SQL cursor/commit times are children of checked-out time.

| Diagnostic component | Original claims (s) | Optimized claims (s) |
|---|---:|---:|
| Client wall latency | 17.431 | 3.732 |
| Checked-out DB intervals | 3.623 | 0.755 |
| SQL cursor intervals, inside DB time | 3.363 | 0.599 |
| Commit intervals, inside DB time | 0.231 | 0.152 |
| Pool-wait intervals | 13.087 | 1.200 |
| Mocked auth intervals | 1.204 | 0.604 |
| Mocked STT intervals | 1.101 | 1.100 |
| Executor queue intervals | 0.001 | 0.001 |
| Cross-category overlap, subtract when combining | 1.730 | 0.000 |
| Unclassified server-window time | 0.075 | 0.042 |

The original diagnostic has about 1.73 s of overlap between server requests for the same recording. Adding all work totals directly to client wall time would double-count it. Its remaining unclassified/window-to-client time is roughly 0.15 s, covering framework/audio validation/HTTP scheduling; client retry backoff can overlap the still-running server work. Exact raw intervals are retained. Across each entire diagnostic wave, actual top-level PostgreSQL execution is only a few tens of milliseconds, not seconds. No lock-wait sample or executor starvation explains the17.5s result.

## Correctness and policy

- 149 passed, 1 expected SQLite skip across 59 original/optimized prototype cases (real PostgreSQL for database tests) plus existing upload-idempotency, conversation and per-learner locking tests. Additional JS checks: 20 sender/deadline tests and 11 recording-delivery tests passed. Full remote CI was not run; no implementation PR is changed.
- Preserved duplicate submissions/finalizers, killed worker, expiry/stale fencing, cancellation/uncertain commit, reset, injected rollback, account isolation and real legacy-first/bridge-first middleware overlap.
- Added bidirectional original/optimized claim compatibility, different-recording expiry, partial multi-write failure rollback, inconsistent-projection rejection, issued follow-up/turn/evidence preservation and function privilege/transaction-lock checks.
- 50/59.999/60-second boundaries are deterministic. Proposed policy retains 60-second ownership TTL and 50-second delivery deadline: client expiry is unconfirmed, retains recording and schedules no extra sends; explicit recovery checks status before reclaiming expired work. See `TTL-POLICY.md`. Production status-first UX is not implemented.
- Deliberate provider-expiry takeover still permits two mock STT calls but only one persisted attempt/checkpoint; external exactly-once billing is not promised.

## Recommendation and remaining gates

The observed upload acceptance targets pass: no duplicate persisted acceptance or stale-worker mutation, no connection across mocked STT in claims-only mode, no single-learner regression, substantially lower 8/16-learner latency, and retained/improved connection efficiency. These are synthetic local measurements, not statistical production acceptance.

Proceed next with a reviewed all-writer inventory and bridge/drain/rollback design, then a separately approved migration of one authoritative assessment path and typed client recovery integration. Keep production activation blocked until those proofs and physical acceptance are complete. In particular:
- This still covers standalone upload acceptance/pending checkpoints only, not exactly-once final assessment/adaptation/lesson progression. Lesson/check-in, generation, onboarding, GET settlement, end/reset and other writers remain unmigrated.
- Unaware legacy workers require the original advisory-lock bridge and a verified drain before claims-only activation. That bridge intentionally retains a provider-spanning connection. Original/optimized PoC record compatibility does not solve unaware legacy-writer overlap.
- The local function/CTE approach needs versioned schema deployment, role/security review and coordinated rollback. No function was installed on production.
- TTL/status-first recovery, reload disclosure, retained-recording UX, retention and provider/memory budgets remain integration work. Neither retry timeouts nor pool sizes should be increased to compensate.
- Upload-only tests do not measure complete lessons, real providers, cellular transport, physical Safari or the accent pool. Full connection budget remains 5 per worker / 10 across two fully migrated overlapping workers plus other clients; observed 3 is not a production budget.
- PR110 remains useful for offloading whole short transactions. PR111 remains useful on legacy/bridge lease routes; its JSON barrier becomes unnecessary only after a migrated route commits and cleans up before acknowledgment. Neither PR was merged or deployed.

## Reproduce

Use the same local environment/proxy as `README.md`, with pg_stat_statements preloaded and track=all on the disposable local server. Run the same `benchmark.py` for modes current, claims, optimized; pass `--claims-root` pointing to a clean `9d9fc7e` checkout for original claims. Use `diagnostic_timeline.py` with the same arguments and levels 16/repetitions 1 for interval diagnosis. No production credentials are needed.
