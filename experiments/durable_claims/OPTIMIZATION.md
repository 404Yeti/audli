# AUD-94 — Local optimization design and profiling

This pass stays on the local PoC branch. No production code paths, flags, schemas, infrastructure, providers or PRs are activated. The original algorithm remains in `store.py`; the optimized implementation is explicitly selected by the local harness.

## Changes

The ordinary original path has 33 client SQL statements: two authenticated request-scope lookups, 14 in claim, 17 in finalization. Repeated profile/slot/operation/question reads and conversation ownership checks each cross the database network. `write_conversation` also rewrites unchanged turn/follow-up/evidence projections on subsequent uploads.

`OptimizedStore` uses two client SQL statements per claim and two per finalization, retaining the same two explicit transactions and two auth lookups (six statements total):

1. A local `SECURITY INVOKER` PostgreSQL function takes the same owner profile lock, initializes/locks the same account slot, checks exercise ownership, locks the conversation and batches the snapshot reads. The function cannot commit. Execution is revoked from PUBLIC and uses a fixed search path with qualified tables. It is installed only in disposable loopback databases, never by application startup.
2. Python validates the same operation status, fingerprint, token, expiry, pending evidence, lesson/exercise/question state and original canonical JSON checkpoint hash. The original `Conversation` schema remains authoritative. Inconsistent JSON/projection fields are rejected.
3. One data-modifying CTE statement applies the atomic mutations. Claim increments the slot fence and upserts the operation. An expired different operation is cancelled; the same expired operation is superseded by its new token without trying to update that row twice in one statement.
4. Finalization inserts the attempt, CAS-updates only the pending conversation checkpoint, completes the fenced operation and releases the slot. RETURNING dependencies order the mutations; every participant count must equal one or Python raises inside the same transaction and all writes roll back. Existing issued turns/followups/evidence remain unchanged.

No provider work occurs inside either transaction. Stable UUIDv5 IDs, the original fingerprint/checkpoint format, expiry and token semantics remain compatible in both directions with original PoC records. Existing abandonment/reset transactions and legacy middleware overlap semantics remain intact. No pool or timeout changes are made.

## Profile methodology

All three variants run through the **same updated harness**, real HTTP, PostgreSQL17.10, nominal72ms database RTT, mocked300ms auth and1100ms STT, main pool3/overflow0,32-thread executor and unchanged P2 three-send/50s delivery policy. `--claims-root` imports the original algorithm from frozen commit9d9fc7e; its source hash is recorded. Historical result files are preserved, but the comparison uses fresh runs under identical instrumentation.

The disposable local server preloads pg_stat_statements with nested tracking. Top-level `total_exec_time` measures database execution (including server-side waits), excluding network round trips and client-pool queueing. Nested totals are reported separately and must not be added to parent execution time. Planning tracking is off, so planning time is not independently measured. A separate direct local connection samples database lock waiters every10ms; zero samples does not prove absence of shorter waits.

SQLAlchemy cursor timings include transport and implicit transaction-start costs. Explicit commit/rollback calls, QueuePool blocking-get waits, executor dispatch, mocked auth/STT and per-recording database stages are measured separately. SQL templates are recorded without bound parameters; results contain no audio, transcript payloads, tokens or account/upload identifiers. Numeric synthetic client indices allow correlation with client wall latency. Connection-seconds integrate checked-out time, not idle retained pool sessions.

With retries, server requests for one recording can overlap: per-recording sums describe work/amplification and are not automatically additive to wall time. Claim/finalize durations also contain cursor/wait/commit time and must not be added to their children. The report distinguishes actual server execution from cursor/transport time and pool waiting; client backoff and framework/validation overhead remain outside SQL. Per-wave counters are snapshots after all HTTP/provider/database work settles.

The local server permits more than15 sessions to expose legacy demand; it does not emulate Supavisor's15-backend limit or200-client limit. Upload-only traffic does not exercise the separate accent pool. No production throughput or safe learner-count claim is made.

## TTL policy

See `TTL-POLICY.md`. Keep the60-second server ownership TTL and50-second client deadline unchanged. Client deadline expiry means unconfirmed delivery, not authority to steal a live claim. No automatic work is scheduled after the P2 cap/deadline. Proposed production integration uses status-first explicit Send again and conservative expiry handling; that UI/client integration is not activated here.

## Scope still excluded

This proves upload acceptance and pending-checkpoint atomicity only. Assessment/adaptation, check-ins, lesson expiry/settlement, generation, reset/end and all other writers still need a reviewed ownership migration. Bridge mode holds the original advisory lease and provides no capacity gain. Claims-only cannot safely overlap an unaware legacy writer. Full deployment requires a schema/security/versioning review, complete writer inventory, drain/rollback protocol, operation retention, provider/memory bounds, recovery integration and physical Safari acceptance.
