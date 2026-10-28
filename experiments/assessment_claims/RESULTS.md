# AUD-94 — Authoritative assessment stage results

**GO for the next reviewed development stage; NO-GO for production activation or merging/deploying PR110/111.** The local staged backend now covers authoritative spoken assessment, follow-up issuance, final evaluation/adaptation, session completion, readiness and lesson checkpoint advancement. It is not a production feature-complete lesson client/server rollout.

Implementation: separate `codex/AUD-94-assessment-claims` review branch from approved `3d7fb27`. Application startup, production schemas/flags/infrastructure and frontend transport source remain unchanged. This package installs only in disposable loopback PostgreSQL databases with deterministic mock-provider configuration.

## Full-path measurements

Real HTTP and PostgreSQL 17.10; nominal 72ms DB RTT; mocked 300ms auth, 1100ms STT and 3500ms assessment; main pool 3/zero overflow; 32 executor threads; idempotency/P2 enabled; unchanged three-send cap and 50-second delivery deadline. Each measured exercise starts with a seeded clip/listening checkpoint, then two recordings, two assessments with one follow-up, final adaptation, readiness and persisted lesson advancement. Generation, tutor TTS/playback, real microphone capture and physical cellular/Safari are excluded. This is not speech-end-to-Pip latency.

Two waves per concurrency, 62 requested exercises per variant. Latencies below pool successful completions across both waves; the control failure is counted separately and not assigned a successful latency. Small sample sizes do not establish statistical production acceptance.

| Concurrent learners | Current completed | Current p50/p95(s) | Staged completed | Staged p50/p95(s) |
|---:|---:|---:|---:|---:|
| 1 | 2/2 | 28.32/28.63 | 2/2 | 16.50/16.54 |
| 2 | 4/4 | 28.51/28.53 | 4/4 | 16.37/16.44 |
| 4 | 8/8 | 29.01/32.15 | 8/8 | 16.49/17.09 |
| 8 | 16/16 | 38.44/48.55 | 16/16 | 19.42/20.02 |
| 16 | 31/32 | 65.98/75.55 | 32/32 | 26.73/27.88 |

At 16 learners, staged p95 is 63.1% lower and checked-out connection-seconds per wave are 93.7% lower. This includes batching/projection offloads as well as durable ownership; it is not an isolated flag-toggle measurement.

| At 16 learners | Current | Staged |
|---|---:|---:|
| Peak app physical sessions | 21 | 3 |
| Connection-seconds/wave | 1142.549 | 72.507 |
| Pool waits, both waves | 606 | 664 |
| Worst-wave pool-wait p95(ms) | 6803.6 | 883.9 |
| Client SQL statements/wave | 2746 | 560 |
| Server execution ms/wave | 228.315 | 792.111 |
| Max request-owned checkouts at STT/assessment | 1 | 0 |
| Max audio sends per recording | 3 | 1 |

The control emitted pool-timeout diagnostics at its unchanged 10-second QueuePool timeout and one 16-learner exercise needed manual recovery. Its stopped client did not cause a false completed evaluation: persisted counts show the remaining transcript/partial evidence separately. Precise client error classification was not captured for that failure, so it is not attributed to Safari or a 12-second network signature. No pool, timeout or route was changed to improve either variant.

The local server permits more than 15 sessions. Control success with 20–21 app sessions is not proof of feasibility on the supplied 15-backend Supavisor session pool; 200 pooler clients is a separate limit. The upload/assessment workload excludes the accent pool and other traffic. Full migration still needs production capacity verification and an old-worker drain.

## Persisted and provider invariants

| Total across all levels | Current | Staged |
|---|---:|---:|
| Completed exercises | 61 | 62 |
| Requested exercises | 62 | 62 |
| STT calls | 123 | 124 |
| Assessment calls | 122 | 124 |
| Persisted attempts | 123 | 124 |
| Persisted turns | 122 | 124 |
| Evaluations | 61 | 62 |
| Adaptation events | 61 | 62 |
| Duplicate STT | 0 | 0 |
| Duplicate assessment | 0 | 0 |

Every staged successful response follows committed transaction cleanup. No provider wait retains a request-owned connection in claims-only mode. Atomic progression tests also read persisted profile counts and lesson revisions/counts. Normal duplicates replay/join exactly the same operation; a different live operation conflicts rather than returning an ambiguous processing join.

## Correctness and review findings

- Final PostgreSQL/SQLite broad regression: 219 passed, 1 expected SQLite skip for a PostgreSQL-only lease test. After the last recovery-identity change, the complete 38-case staged suite passed again. Existing P2 sender/deadline/recording-delivery checks: 31 passed. Results overlap and should not be added as independent case counts. Full remote CI and physical Safari tests were not run.
- Covers concurrent duplicates/finalizers/learners, killed worker, stale/expired claims, cancellation/unknown commits, live reset/pause/expiry, question/input changes, uncertainty/confidence boundaries, two-follow-up limit, late transaction rollback, account isolation, client verdict rejection and actual lost HTTP response recovery.
- Reuses the existing grounding, final-evaluation, adaptation and terminal-feedback domain functions. Insufficient evidence is unknown, not zero; unreliable recognition does not adapt. Evaluation/adaptation uniqueness and profile/version checks remain transaction backstops.
- Every current learner-owned table has a guard. A review found and corrected cross-parent inserts that could otherwise derive ownership from only one reference. All relevant parent references are checked. Active-question existence is also validated by a deferred constraint at commit to preserve the original conversation-before-followup transaction order. Both legacy-first and bridge-first real-middleware tests pass.
- Wrong reset revisions roll back invalidation; explicit record-again is CAS-bound to the pending attempt/question and preserves evidence rows. Unconverted direct writers fail closed, and NOWAIT owner checks prevent reversed lock waiting from becoming a deadlock.
- Deliberate crash/expiry ambiguity can repeat external provider work whose result was not persisted. Exactly-once persisted progression is proved for this scoped path; exactly-once external billing is not promised. No real providers were called.

## Compatibility and next gates

See README.md for scope and DEPLOYMENT.md for the proposed writer matrix, bridge/drain sequence and rollback. The guard-aware bridge retains original advisory ownership and therefore still holds a connection during provider work; its compatibility tests are not claims-only capacity tests. Unaware workers must be drained before enforcing a new ownership protocol. Roll back only to guard-aware bridge code after resolving in-flight claims; never delete receipts/learner work or drop guards to recover traffic.

Next development gates: typed processing/status/resume and explicit record-again client integration; remaining provider writers (generation, check-ins, tutor audio/TTS, closing/homework and onboarding); complete account-deletion/retention semantics; versioned least-privilege schema/role rollout and conflict handling; full CI/WebKit/physical Safari; fresh capacity/monitoring approval. Claims-only review mode rejects unsupported mutations, so it is intentionally not a drop-in production lesson client. The stage advances completed-exercise lesson checkpoints; it does not yet migrate the complete closing/homework/lesson-completion lifecycle.

Keep the existing 60-second server claim TTL and 50-second P2 deadline policy: timeout is unconfirmed delivery, never permission to steal a live claim; preserve the recording and use status-first explicit recovery. No existing production flag was changed. PR110/111 remain unmerged and undeployed.

## Provenance and artifacts

- `results/current.json`, `staged.json`: exact raw waves, numeric request/SQL profiles, provider counts, persisted counts, package versions and source hashes.
- `results/summary.json`: pooled comparison above. `tests-*.txt`: successful targeted verification outputs.
- Control source is `732e420` with the full-path harness captured at review checkpoint `2187614`. Final staged hardening changes ownership validation and the 202 join identity contract; the control never executes staged functions or returns 202. Shared mock latencies, pool/deadline settings, normal client sequence and P2 source are identical. Unused staged-file hashes recorded with the control can be verified against `2187614`; final staged hashes identify the reviewed implementation.
- Synthetic setup clears its own per-wave data outside measurements, with guards enforced during workload. No production credentials/session files were read. All experimental databases and local services are cleaned up after verification.
