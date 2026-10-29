# AUD-94 integrated release candidate

This is the single deployment/rollback authority for the integrated candidate.
Earlier PoC, readiness and startup reports are historical evidence, not operational
instructions. Integration review and offline rehearsal are authorized; production
migration, deployment, permission/flag changes and paid providers are not.

## Review map and consolidation

The candidate targets main732e420 and incorporates the approved assessment,
recovery, P0 migration and417cbee startup work. PR110 and111 are not prerequisites
and remain unmerged. PR112/113 remain historical review sources; use this combined
candidate for the complete diff. The runtime is `app/claims`, installed explicitly
by `app.main.create_app`; Docker already copies `app`. `off` is the default.

- Authoritative state: `app/claims/assessment_claims/store.py`, lifecycle and SQL
  functions in reviewed migration008. Atomic final assessment/adaptation/progression,
  verified owner, stable key and fencing checks remain together.
- Admission/recovery: packaged bridge/routes/integration, backend operation status,
  recording replacement tombstones and `web/lib/recording-delivery.ts` / API handling.
- Startup/permissions: `app/claims/runtime.py`, `privileges.py`,
  `scripts/print_runtime_grants.py`, `docs/aud94-startup-evidence/PRIVILEGE-MATRIX.md`.
- Schema: existing `docs/postgres-006.sql`,007 and008. No schema rewrite or new
  migration is introduced in consolidation.
- Canonical runtime duplicates removed: experiments' assessment store, routes,
  lifecycle, bridge, installer and account erasure now import the packaged modules.
  Shared stage metadata also comes from the runtime. ContextVar, exception and
  monkeypatch identity are shared, preventing two subtly different test protocols.
- Kept deliberately: local-only schema installer/recovery tooling; original upload
  PoC/current/combined benchmark implementations and raw evidence. These are not
  imported by the API or copied into its Docker image. Historical benchmark
  comparisons must use their recorded source revisions; they are not current
  runtime alternatives. No new admission queue, lease-pool activation or one-request
  limiter is introduced. The bridge is still needed for generic non-atomic routes
  and code rollback; its quarantine cannot be removed until those routes migrate.

The initial integrated CI exposed a machine-speed-sensitive telemetry test: HTTP204 completed in905ms but failed an unrelated500ms assertion. Its regression now directly asserts zero lease acquisition calls for telemetry while a real profile mutation is observed contending on a held PostgreSQL lock. Runtime behavior and timeouts are unchanged; the existing600ms contention-observation guard is retained.

Raw synthetic benchmark JSON is retained for reproducibility. Review runtime and
migration SQL first; then follow recorded source hashes and samples in historical
results. Do not treat mocked provider evidence as actual AI-quality acceptance.

## Operating-mode contract

| AUDLI_DURABLE_OPERATIONS | Preconditions and behavior |
| --- | --- |
| off (default) | Existing workflow on001..007. Refuses008. Does not install claims or initialize its provider. |
| bridge | Authenticated008 + restricted role; retains advisory admission, durable fencing, receipts and quarantine. Guard-aware rollback target. |
| claims | Same requirements; additionally AUDLI_DURABLE_FLEET_DRAINED=true after independent fleet verification. AI waits hold no request-owned DB connection on native paths. |
| paused | Guard-aware operation-status reads continue; other API work is rejected503 before authentication initialization. Preserves outer CORS/privacy handling. |

The fleet assertion does not detect old workers. Pausing takes effect per worker
on configuration rollout; it is not an instantaneous global traffic barrier and
cannot cancel earlier provider calls. Use a separately approved external admission
stop and drain for schema changes or a multi-worker transition.

Startup checks schema/guards, authenticated identity mode, stable upload
idempotency, function/table/column privileges and dangerous role capabilities.
No runtime DDL or privilege provisioning. No production settings are changed by
shipping this branch. Existing retry policy/timeouts, three audio-send cap,
providers, codecs, CORS/routing and DB flags remain independent and unchanged.

## One proposed deployment and rollback procedure

All production steps below require separate explicit approval. Rehearse them on a
disposable database and mocked providers first. Never embed credentials in shell
arguments, logs, PR comments or this repository; use an authorized Shell and a
reviewed credential/service configuration. Verify SSH host keys normally.

1. **Freeze inputs.** Record candidate SHA, all required CI jobs,006 source catalog,
   actual runtime role/memberships, effective flags/process count, pooler mode and
   per-user/database limits/utilization. Confirm current data backup and restore
   procedure and approved erasure/retention policy. Missing inputs block production.
2. **Prove least privilege.** Rehearse fresh001..007→008 and production006→007→008
   with the real intended privileges, not owner startup followed by SET ROLE.
   Review creator defaults and inherited non-DML grants. Browser roles receive no
   new access. Managed Auth/Storage grants are separate and require policy review.
   Review-only server SQL can be printed with
   `python -m scripts.print_runtime_grants --role reviewed_backend` (no connection).
   Verify both main and separate shared-control pools with genuine LOGIN credentials.
3. **Prepare compatible code.** Candidate in off mode can start on006/007. It cannot
   start in bridge/claims on006; this is not a zero-downtime migration promise.
   Do not activate claims on an unaware fleet or assume a config flag fences it.
4. **Stop admission externally and drain.** Verify zero unaware live workers and
   in-flight lesson/provider owners. Resolve unknown outcomes using authorized
   operator inspection and canonical state, never expiry alone. Prove the stop
   remains in force throughout migration/deployment overlap. Read-only snapshots
   alone do not maintain a barrier.
5. **Apply approved schema/grants.** Verify exactly006; apply reviewed007 dependency
   then008, or the validated combined006→007→008 transaction. The normal migration
   CLI stops at007 and must not be mistaken for008 activation.008 atomically creates
   guards and revokes browser/client access, including inherited TRUNCATE grants.
   Apply reviewed restricted-role grants separately only while admission is stopped;
   forced RLS and revocations prevent an interim browser access window. Use
   fail-on-error transactions and verify ledger, objects, privileges and indexes.
   Interrupted007/008 must be assessed from the ledger/objects, not assumed applied.
6. **Boot bridge and verify.** Start only this008-aware binary in bridge with the
   restricted role. Check startup, health, flags, pool headroom, account isolation,
   synthetic recovery, persistence and deletion. No provider-backed tests without
   separate approval. Native and generic routes must share one ownership slot.
7. **Controlled activation.** Only after approval, confirm every worker is
   guard-aware/drained, set the fleet assertion and claims mode, and replace/drain
   bridge workers under the admission barrier. Preserve schema and all unrelated
   flags. Release admission only to the separately approved bounded population.
8. **Observe.** Monitor connection pressure, pool checkout waits, event-loop lag,
   request/turn latency, operation age/quarantine, canonical attempts/evaluations/
   progression and provider counters. Use opaque operation/upload IDs and timings;
   no audio, transcripts, tokens, account IDs or unredacted HAR files.
9. **Rollback on a trigger.** Stop new admission, start paused workers and drain
   earlier active workers. Keep008, receipts, tombstones and fencing counters. An
   expired owner is not proof the provider stopped. Recover/reconcile quarantines
   explicitly with operator authorization, checkpoint and fence checks. Roll code
   only to this008-aware bridge/paused binary. Never use off,732e420, destructive
   downgrade, TRUNCATE or automatic quarantine clearing. Empty-schema rollback is
   a disposable rehearsal path only, not a populated production recovery plan.
10. **Verify recovery before re-admission.** Recheck role/flags/schema, no stale
    commits/duplicate progression, canonical learner state and capacity. Obtain
    separate approval if rollback needs changes outside the approved code scope.

### Monitoring gates (proposal, pending actual capacity)

If the verified per-user backend pool remains15, reserve at least3 slots for other
consumers/operations. Pause expansion at12 occupied backend slots for60s; any
connection exhaustion is an immediate stop. Recalculate for role-specific pools,
other services and old/new worker overlap; the200-client limit is not15 backend
slots and free PostgreSQL server slots are not Supavisor headroom. Two one-process
instances have a projected application-pool ceiling of10 (3 primary+2 shared each),
plus other clients and any remaining bridge lease connections. It is not a measured
capacity guarantee. Bridge lease contenders remain a risk; do not use that simple
pool sum as an upper bound while long-lived lease ownership is active.

Stop/rollback immediately for duplicate canonical attempts/evaluations/progression,
stale-worker commit, cross-account access, lost evidence or inconsistent checkpoints.
Stop for persistence errors or connection exhaustion. Proposed warning/stop criteria:
pool-waitp95>500ms or requestp95>20% above comparable baseline for5min; investigate
expired running operations after2min. Establish actual numeric baselines before
production. Provider duplication must be distinguished from transport replays:
exactly-once persisted state is guaranteed by fencing/transactions, but provider
execution cannot be exactly-once across every crash without provider deduplication.

## Required external evidence and owners

| Gate | Owner | Pass criterion / current status |
| --- | --- | --- |
| Restricted startup/imports/schema chain | Engineering | Genuine LOGIN from first query, image-like Uvicorn startup/shutdown, fresh+006 chain; local tests and remote CI required on final SHA |
| Full backend/PostgreSQL/browser checks | Engineering | All mandatory remote jobs and complete Chromium/WebKit suites pass on final SHA |
| Actual runtime identity/settings | Owner supplies Shell snapshot; Engineering reviews | Effective/session role, memberships and capabilities, actual worker count, provider/package versions and relevant flags; pending |
| Managed Auth/Storage permissions | Engineering after role snapshot | Actual policies/FKs/role access and storage emptiness; synthetic deletion tests alone are insufficient; pending |
| Supavisor capacity | Owner dashboard snapshot; Engineering budget | Session mode, per-role/db backend size, client limit, active/idle/waiting counts and24h peaks, other consumers and overlap headroom; pending |
| Retention / operational admission stop | Owner + Engineering | Approve retention policy, backup/restore deletion propagation, stop/drain method, authorized production operator tooling; pending |
| Physical iPhone | Owner | Execute exact cases below against the authorized candidate; mocked browser suites do not establish Safari radio/lifecycle behavior; pending |
| Final candidate performance | Engineering | Existing benchmark baseline retained; if needed for production, rerun final packaged/mode/role variant with identical methodology; earlier PoC timings are not measurements of this final candidate |
| Production rehearsal/activation | Owner | Separate explicit approval; NO-GO until required environmental, operational and device gates pass |

### Exact snapshot additions

Use the previously prepared whitelisted Render role/flags Shell blocks. Provide
JSON only, never credentials or full environment. Confirm effective/session role,
INHERIT/memberships, BYPASSRLS/superuser/CREATEROLE/CREATEDB/REPLICATION, public CREATE
and ownership, process/instance count, schema ledger and all relevant DB/upload/
tracing/locking/optional-feature flags. After the role is known, inspect the actual
Auth/Storage policies/grants read-only. Supabase dashboard must show mode, backend
pool size per user/database, client limit, current active/idle/waiting clients,
backend usage and24h peak with timestamps and other consumers. If a metric is
unavailable, mark it unavailable; do not infer it from seven observed server sessions.

### Exact physical iPhone Safari actions (requires separately authorized execution)

Record iPhone/iOS/Safari version, network, UTC start/end, opaque upload/operation
IDs and numeric timings/outcomes only. Do not share recordings, transcripts, JWTs,
unredacted HAR exports or learner identifiers.

1. Complete a normal Wi-Fi lesson: opening check-in, short closed-choice answer,
   substantive answer/follow-up, assessment, closing and persisted completion.
2. Repeat on cellular; verify no unexplained greeting/question replay.
3. Interrupt an upload with connectivity loss, restore it, and confirm the same
   recording/ID is retained, at most3 actual audio sends and a truthful result.
4. After a visible failure, tap Send again twice quickly: one operation in flight,
   status reconciliation before ambiguous resend, no fresh recording or TTS replay.
5. Choose Record again explicitly: old ambiguous recording is fenced/tombstoned,
   capture starts only after the action and late delivery cannot replace new evidence.
6. Choose Replay question explicitly: replay is intentional and uses cached audio;
   failure feedback itself generates no TTS.
7. Background Safari for15–30s during upload and assessment, then return. Repeat
   with lock/unlock. Verify bounded recovery and no automatic recording/discard.
8. Reload during sending/processing: disclose lost in-memory audio, recover only
   canonical server state, and never claim an unconfirmed answer was received.
9. Test ambiguous check-in/lost acknowledgment only with separately approved fault
   injection; unrelated revision changes must not prove delivery. If it cannot be
   induced, mark this case untested instead of inferring a pass.
10. Reopen history after completion; Engineering correlates counters to verify one
    canonical attempt/assessment/progression, no avoidable duplicate STT/TTS and
    preserved evidence. A smooth phone experience alone cannot prove exactly-once.
