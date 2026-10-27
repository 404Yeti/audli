# AUD-92 — bounded account lease admission (review only)

PR #110 stays blocked. This PR branches from deployed main `732e420`; it does
not contain #110's offloading changes. No production flag, pool, provider,
codec, retry policy, routing or CORS change is authorized here.

## Configuration and scope

- `AUDLI_LEASE_ADMISSION=false` by default. Disabled follows the existing lease
  path, including its existing cleanup behavior. SQLite/local-auth have no
  PostgreSQL account lease and are unaffected by this gate.
- `AUDLI_LEASE_ADMISSION_LIMIT=1` by default, strictly an integer 1–16. The
  value is a **per application worker** bound, not a deployment-wide bound.
- Gate after verified authentication but **before the first lease connection**,
  advisory-lock query, request-owned repository lookup or endpoint execution.
  Rejection does not consume the request body in the lease middleware. The
  unchanged outer size/origin guard can inspect/buffer bytes first.
- All existing lease contenders count, including ACK POSTs while
  `ack_lease_exempt=false`, other accounts, and requests that have not acquired
  the advisory lock. Existing read/telemetry/review exemptions are unchanged.
- One permit spans all sequential polling candidates, sleeps, optional stale
  pooled-connection replacement/spill, provider awaits, repository work,
  rollback and final connection cleanup. There is **no admission queue**;
  existing admitted advisory-lock polling remains, with no new ordering rule.
- A request opens at most **one lease connection at a time**: a failed candidate
  is closed before the next is opened. Pool replacement/spill is sequential.
  Ordinary repository/accent connections are separate and are not covered by
  this permit. Authentication's HTTP work is also outside this bound.

## Cancellation and failures

With admission enabled, lease acquisition runs in a worker thread with the same
account key and statements. Cancellation drains acquisition and any returned
connection through a tracked task: no route/provider work starts for that cancelled
operation. Confirmed close returns the permit; failure or an unconfirmed result
quarantines it. Acquisition/close are threaded even if general DB offload is OFF,
so synchronous socket cleanup cannot prevent the response deadline from firing.
Admission disabled retains the legacy path.

Admitted cleanup and cancellation-drain have a fixed **two-second response-wait
deadline**, measured with a monotonic clock and never reset by repeated cancellation.
On expiry the operation continues in a tracked background future, the permit stays
permanently quarantined, and an unsent successful JSON acknowledgment is discarded.
The outer application reports503; a cancelled request instead propagates cancellation.
This bounds the wait for cleanup, not the underlying synchronous DB operation or
provider/transaction work. A stuck thread cannot be interrupted safely; the worker
may need a forced recycle. There is no automatic reset when a late close finishes.

A close/invalidation failure emits only the constant
`lease_admission_cleanup_failed; permit quarantined`; no private error details.
With limit one, that worker refuses further lease operations. The health endpoint
remains usable. Tracked cleanup futures observe late exceptions without logging
payloads and are removed on completion; pending cleanup is bounded by admitted
capacity. Ownership and transaction boundaries remain unchanged.

## Exhaustion and current browser behavior

Immediate HTTP 503 with `Retry-After: 1` and a non-sensitive busy message. No
endpoint, STT, evaluator, TTS, attempt write or progression runs for that rejected
request. The message does not claim that an earlier ambiguous send was unreceived.

The unchanged P2 sender retries 503 within its existing three-send/wall-clock
bounds; it **does not honor Retry-After**, using the existing 300/900 ms backoffs.
The same Blob/upload ID survives automatic and explicit exercise retries. Check-in
retries first reconcile the original revision; ambiguous pending evidence remains
untouched. Exhaustion retains audio for explicit Send again; it cannot guarantee
delivery under sustained contention. A replay after successful upload/assessment
uses existing idempotency/current-state logic, not a second provider call.

**Not all POSTs have automatic retries.** Other operations can surface the busy
error and require existing manual recovery. Optional ACK failure is caught by
`prepareAcknowledgment` without failing a completed assessment. A cap is not a
throughput improvement and has no fairness guarantee under a retry storm.

**JSON acknowledgment ordering (owner-approved follow-up):** for an admitted
request, retain a successful2xx `application/json` response only when it declares
Content-Length<=64KiB and supplies exactly one complete matching body. Keep its
existing messages by reference. Wait for the entire application to return, reset
request ownership, close the lease and return the permit, then send the headers/body.
This preserves late application work and does not unlock at response.start. Failed
cleanup/cancellation does not flush retained success. Already-committed attempts
remain authoritative and recover through unchanged idempotency/reconciliation.

Audio, streaming/chunked/oversized/undeclared-length/trailer/path-send responses
pass through with original backpressure and lease lifetime. Error responses also
keep their original behavior. This is deliberately not a blanket response buffer;
unsupported responses may retain the original fast-following race. The guarantee
applies to the small complete JSON acknowledgments above. Incomplete retained JSON
is not acknowledged as success. No queue, increased admission limit, extra DB
operation, altered upload ID or retry-policy change is introduced.

A real-loopback HTTP test with zero-delay authentication holds close behind a
barrier and asserts no200 headers are emitted while the permit is occupied; all
12 immediate following POSTs succeed. Additional tests cover cancellation/disconnect,
late/hung/failed cleanup, missing bodies, stream/audio pass-through, canonical upload
replay, rollback and identical database query/checkout/connect/rollback counts.
Reproduce paired before/after measurements with `scripts/bench_json_ack.py` and a
local72ms DB delay proxy; final measurements and CI are recorded in the PR body.

## Capacity with verified production lease pooling OFF

Main QueuePool 3 + separate accent-control QueuePool 2, both overflow zero.
With admission enabled, `P` workers and limit `K`:

`application connection ceiling = P × (5 + K)`

The pools fill lazily. At K=1: one worker 6; old/new guarded worker overlap 12.
Add other users/services of the same session pool, auditing and reconnect/release
allowance. The verified session pool backend quota is 15 and pooler client quota
is 200; PostgreSQL max_connections=60 is an independent ceiling, not spare
Supavisor capacity. Twelve is not a complete deployment budget until other use
and reserve fit too. The separate admission locks never replace the shared
account advisory lock across workers.

**First guarded deployment still overlaps an old unguarded worker.** Its lease
connections remain unbounded: `5×2 + admitted_new + outstanding_old + others`.
Likewise shipping this flag OFF bounds nothing. Do not call either overlap safe
based on 12. A future activation plan must quiesce/drain the old worker and verify
utilization before #110. There is no infrastructure or flag update in this PR.
If pooled leases are later approved, retained idle pool connections need a new
budget; this formula deliberately assumes verified `lease_pool=false`.

## Local evidence (mock providers only)

Reproduce with disposable **local** PostgreSQL:

```
python scripts/bench_lease_admission.py --admin-url postgresql://...@127.0.0.1:55439/postgres
```

Script refuses non-loopback hosts, creates/drops its own databases, returns only
numeric aggregates. PostgreSQL 17.10, Python 3.12.13, SQLAlchemy 2.1.6, psycopg
3.3.6 locally; production snapshot is Python 3.12.16. Auth 300 ms and STT 1100 ms
are mocks; DB RTT is local, per-learner locks enabled, ASGI transport, no Safari,
upload bandwidth, provider variation or production-network measurement.
Historical admission-only measurements before JSON ordering, in
`docs/benchmarks/AUD-92-lease-admission.json`:

| Workload | Admission off | Admission on (K=1) |
| --- | --- | --- |
| Six sequential uploads, accepted p50 | 1462.6 ms; 6/6 accepted | 1464.8 ms; 6/6 accepted |
| Four-account burst, one send | 4/4 accepted; peak 4 leases | 1/4 accepted; 3 rejected; peak 1 lease |
| Four-account burst, up to three sends | 4/4 accepted; 0 rejections | 2/4 accepted; 8 rejected sends; 2 recordings retained |
| Retry-burst accepted delivery p50 / max | 1646.8 / 1655.7 ms | 2383.4 / 3288.1 ms |

Single-stream difference +2.2 ms (~0.15%) is noise-scale, not a production
latency claim. Rejections cost about 308–310 ms including mocked authentication.
Across all cases accepted count equals STT calls and persisted attempts; rejected
sends invoke neither. These measurements demonstrate the availability tradeoff,
not a higher-throughput result or production acceptance of the ordering change.

Tests cover no-queue storms, configured/disabled behavior, read exemptions,
repeated cancellation during acquisition/holding/contender cleanup, failed cleanup
quarantine, query errors, rollback, verified account isolation, real PostgreSQL
same-upload/assessment replay, cross-worker advisory locks, identical complete
lesson rows with flag on/off, and JSON-after-cleanup ordering over real HTTP. Browser
library regressions cover retained IDs, exhausted retries/manual Send again and
check-in reconciliation; no transport implementation changes.

## Proposed monitoring and rollback (not executed)

`GET /api/health` adds numeric-only `lease_admission`: enabled, limit, in_use
(includes quarantined), quarantined and rejected (cumulative per worker).
Existing latency aggregates additionally record rejection events and admission
levels when enabled. Health remains usable during exhaustion. Multi-worker
monitoring must collect each worker; a load-balanced health read is not a census.

- Any quarantined permit, new pooler rejection/connection timeout, persistence
  failure, duplicate attempt/progression or unexpected learner failure: stop.
- Relevant backend occupancy: warn at 10/15; halt expansion at 12/15, retaining
  three slots. Clients: warn 140/200, halt 160/200. Observe both, not just total PG.
- Matched low-load requests: investigate any admission rejection; pause acceptance
  if ordinary sequential progression repeatedly requires manual recovery.
- Matched p95 latency >20% baseline for five minutes or repeated DB-attributed
  loop stalls >1 s: pause and investigate. Tiny samples cannot establish p95.
- Track actual sends, rejected sends, manual recoveries, exhaustion and canonical
  attempts; never learner IDs, transcript/audio or bearer/session values.

Before any deployment, review the scoped-response fallbacks, bounded-cleanup
quarantine/worker recovery and old-worker transition, verify current/peak pool usage
with reserve, and obtain owner approval. JSON ordering fixes normal fast-following
acknowledgments; it does not solve four-learner contention at admission limit one.
If #110 later regresses, roll back **#110 code to the reviewed safeguard-only
revision**, retaining approved admission/production flags. If the safeguard itself
regresses, stop the canary and follow an explicitly approved code rollback/drain
plan; disabling admission alone removes the capacity guarantee. Do not adjust
pooling, retry, provider or routing settings as an incidental rollback.
