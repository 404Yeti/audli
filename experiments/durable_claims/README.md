# AUD-94 — Local durable upload claims proof of concept

**Review only. Not wired into application startup, not a production migration, and not ready to deploy.**
The original working tree and PRs #110/#111 are untouched. No provider SDK calls, production database access, flags, infrastructure, merges or deployments are involved.

## What exists

- `store.py`: disposable PostgreSQL-only operation and per-account slot tables, atomic claim/finalization, database-clock expiration, monotonically increasing fencing tokens, owner-bound deterministic AUD-89 attempt IDs, explicit reset invalidation, immutable input fingerprint and question/checkpoint binding.
- `service.py`: request-bound mocked STT between transactions. Cancellation of a database thread does not release ownership underneath it. Cancellation waits at most two seconds for an uncertain transaction; a tracked task may finish later, while its claim remains fenced. A retry reads canonical completion or waits for expiration. No durable recording storage and no job queue.
- `routes.py`: local harness replaces only the exercise upload route, preserving real authentication, audio validation and the existing assessment routes. Missing stable IDs are rejected by the experiment. Existing production behavior is untouched. Prototype 202/status handling is explicitly experimental and is not silently enabled for the existing web client.
- `test_claims.py`: real PostgreSQL correctness tests, synthetic inputs/providers.
- `benchmark.py`, `client.mjs`, `metrics.py`: real HTTP, real PostgreSQL, actual unchanged P2 `deliver()`/policy parser and three-send policy. Only the prototype adds explicit processing-status polling. Neither retries nor timeouts are modified.

Every experimental schema write requires a loopback PostgreSQL URL with an `audli_claims_*` database name. The harness creates and drops unique databases. No credentials, recording IDs, account IDs, audio or transcripts appear in result files.

## Atomicity and ownership

Claim locks the verified learner profile, then the account slot, validates exercise/question/pending state, and atomically records the new token and running operation. No connections remain checked out while mocked STT runs. Idle physical SQLAlchemy pool sessions can remain connected.

Finalization locks the same owner/slot and verifies operation scope, token, live expiration and checkpoint. Attempt insertion, the real repository's pending conversation checkpoint, operation completion and slot release commit together. Injected transaction failure rolls back all four. Concurrent finalizers return the same canonical result.

The proof of exactly-once progression is deliberately scoped to **upload acceptance and the pending checkpoint**. Upload never creates an assessment, adaptation event, completed session or score. Existing confidence/assessment gates remain effective. Migrating final assessment/adaptation and lesson/check-in ownership is outside this prototype.

A different recording cannot replace existing pending evidence. A reused upload key with different bytes is rejected. A legacy attempt can replay by the established deterministic ID, even though legacy rows have no byte fingerprint. Replay does not rewrite its checkpoint.

Cancellation before work starts creates nothing. Cancellation in STT conditionally abandons the current claim after the coroutine unwinds. An uncertain claim/commit is not treated as cancelled database work. Worker death leaves an expiring operation. A replacement receives a higher token; a stale finalizer either fails or returns an already completed canonical result without writing.

Expiration cannot prevent duplicate external work: a paused/crashed worker's provider may have finished before its result was stored. The fault test deliberately produces two mocked STT invocations but only one accepted attempt/checkpoint. This PoC does not promise exactly-once provider billing.

## Old/new worker overlap

Bridge mode retains the **original long-lived middleware advisory lock** throughout processing. Both old-first and bridge-first real-middleware tests prove serialization and canonical replay with one STT invocation. The bridge therefore provides compatibility, but no connection-capacity gain.

Claims-only mode is restricted to the isolated experiment. `claims_only_allowed` rejects declared legacy workers or nonparticipating writers. This is a tested precondition, **not runtime discovery of old binaries**. An old worker ignores operation rows, so a short advisory check alone cannot make claims-only overlap safe.

Production transition would need a reviewed bridge release covering every writer, then verified drain of legacy workers before claims-only activation. End/reset/expiry, GET settlement, generation, onboarding, preferences and administrative writes must participate or invalidate operations. A code rollback to an unaware worker also needs draining/fencing; an ordinary rolling rollback is not proven safe.

## Benchmark boundaries

See `RESULTS.md` and `results/*.json` for measured results. The workload is upload-only, with server-verified synthetic accounts, fixed 300 ms authentication, 1,100 ms mocked STT, 3-second generated WAV input and a local TCP proxy adding nominal 72 ms database RTT. Latency includes the authenticated policy read through completed JSON acknowledgment. It is **not** speech-end-to-Pip latency.

Main pool size is 3 with zero overflow; accent pool capacity remains 2. The accent pool is not exercised by upload-only traffic. Combined #110+#111 explicitly enables the local safeguard at limit one, immediate rejection, no queue. Current/claims runs have no new admission guard. All configurations use per-learner locks, idempotency, retries, offload, lazy ping, read autocommit and state-only transitions; state-only checkpoints stays false. A 32-thread executor is used for all variants to avoid introducing different executor limits; production thread capacity is not inferred. Turn tracing and latency aggregation are off during these synthetic measurements. These are local settings, not production changes.

Physical session peaks come from SQLAlchemy connect/close events, not a Supavisor deployment. Connection-seconds integrate **checked-out** connection time, including held middleware leases; they exclude idle retained pool time and pool waiting. Pool waits measure blocking QueuePool queue-get calls over 1 ms; connection setup/pre-ping/query time is excluded. Query counts exclude driver-level commands such as pre-ping. Provider counts are per stable synthetic recording; identifiers are never emitted.

The local PostgreSQL server allows more than 15 connections: this intentionally exposes legacy demand instead of pretending to emulate Supavisor. A successful 19-session run does not establish feasibility on the production 15-backend pool. The 200-client pooler limit is a separate constraint. No production capacity or safe learner-count claim follows from this experiment.

## Reproduction

Use Python 3.12 and the pinned versions recorded in the results, Node 22 with TypeScript stripping, and an isolated PostgreSQL server listening only on loopback. The checked-in local helper uses a disposable trust-auth server at port 55449 with a synthetic password; it never loads production credentials.

```bash
.venv/bin/python -m pytest -q experiments/durable_claims/test_claims.py
.venv/bin/python scripts/pg_delay_proxy.py --listen 55451 --target 55449 --one-way-ms 36
.venv/bin/python -m experiments.durable_claims.benchmark \
  --root /path/to/source --mode claims --repetitions 2 \
  --output experiments/durable_claims/results/claims.json
```

Use clean source revision `732e420` for current; a scratch checkout of `272e41c` with PR110's app diff from `732e420..4e51c16` applied for combined. In `app/auth.py`, preserve the #111 guarded branch and use PR110's `await self.offload.release(candidate)` only in its `permit is None` branch. The final report records scratch-source hashes. Do not merge either PR to reproduce the comparison.

## Recommendation boundaries

Claims remove AI-duration connection ownership and the one-operation-per-worker guard, but do not eliminate finite database throughput. This intentionally straightforward implementation adds query round trips and is not a latency optimization. Batch transactional SQL/reads and remeasure before considering migration of authoritative assessments. Do not compensate by increasing pools, retry deadlines, admission limits or production flags.

Additional production work would include schema/version/security review (including private-schema permissions/RLS where applicable), complete writer inventory, retry/status client integration, provider/memory concurrency bounds, expiration/renewal and recovery UX, operation retention, observability, bridge/drain/rollback procedures and physical browser acceptance. None is activated here.
