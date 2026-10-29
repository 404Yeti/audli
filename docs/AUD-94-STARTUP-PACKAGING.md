# AUD-94 restricted startup and packaged ownership

> Historical stage evidence. Current integrated deployment and rollback authority: `docs/AUD-94-RELEASE-CANDIDATE.md`.

Local engineering candidate on `codex/AUD-94-startup-packaging`, based on PR113
8756788. Production activation remains **NO-GO**. No production grants, DDL,
settings, deployments or paid provider calls are part of this patch.

## Changes and startup contract

The additive migration rehearsal grants SELECT on `schema_migrations` with a
role-specific SELECT RLS policy. It also grants the shared-table operations in
PRIVILEGE-MATRIX.md. All grants remain inside the existing migration transaction.
The exact same pure manifest is used by the rehearsal and `python -m scripts.print_runtime_grants --role reviewed_backend`, which only prints review SQL and never connects. Its transaction refuses incompatible schema, elevated roles or pre-existing destructive grants; it does not create users or repair inheritance. No browser identity gains access. Shared accent configuration columns are not
writable by the runtime role; budget counters are. Erasure can delete or unlink
accent reservations without granting global control mutation.

The deployable API imports `app.claims.runtime.configure`. The route/store/
compatibility/lifecycle/erasure dependency closure is packaged under `app/claims`,
already included by Docker's `COPY app app`. It has no `experiments` imports or
benchmark SDK dependencies. Experimental migration installers and recovery
operator tools remain local-only. Runtime code never installs SQL or grants.
The old experimental harness is retained as a comparison baseline; it is not
implicitly activated or copied into the deployment image.

Controls are startup configuration, not HTTP administration endpoints:

| AUDLI_DURABLE_OPERATIONS | Behavior |
| --- | --- |
| `off` (default) | Existing workflow on schemas before008. No provider initialization by this module. Refuses schema008 rather than silently disabling ownership guards. |
| `bridge` | Guard-aware compatibility with legacy advisory admission retained. Native durable receipts and fencing remain in use; this is a compatible code rollback mode, not a return to unaware ownership. |
| `claims` | Durable ownership. Requires explicit `AUDLI_DURABLE_FLEET_DRAINED=true`; default false. This is an operator assertion, not automatic fleet discovery. |
| `paused` | Installs guard-aware claims/status handling but rejects API operations before authentication initialization. Only health, policy, capabilities and operation-status reads are allowed. Existing in-flight requests on older workers are not cancelled. |

Enabled modes require Supabase authentication, PostgreSQL schema008, upload
idempotency, complete schema/guard validation and a non-owner, non-BYPASSRLS,
non-superuser backend role. Startup checks required table/column/function grants,
rejects destructive public-table privileges and rejects public-schema CREATE.
Startup does not manufacture missing permissions or memberships. RLS policy
behavior and actual account scope are verified by real-role integration tests;
permission catalog checks alone cannot prove policy semantics.

**Do not switch any production setting from this document.** Existing P2 timeout,
three-send cap, STT/TTS providers, flags, routing and CORS remain unchanged.

## Migration, activation and rollback procedure (proposal only)

1. Obtain actual Render role/settings and current Supavisor client/backend counts,
   limits and peak utilization. Confirm intended restricted role and managed Auth
   permissions; review the separate grant artifact against that real role.
2. Pass remote CI and physical Safari acceptance on an authorized environment.
   Approve the operational maintenance/admission-stop mechanism separately.
3. Before008, deploy only a separately approved schema-compatible binary in `off`.
   Applying008 while old unaware workers serve is prohibited. Pause external
   admission, drain/terminate all unaware workers, verify no unresolved provider
   owners, then apply reviewed007→008 and role grants atomically as authorized.
4. Start every worker in `bridge` against008 with the restricted credentials;
   verify health, account isolation, recovery and capacity. The bridge requires008
   at startup; this patch does not provide a zero-downtime migration from006.
5. Keep admission paused while proving fleet membership/drain. Activate `claims`
   only after separate approval and external verification of zero unaware workers.
   Default-false fleet assertion must never be treated as a discovery mechanism.
6. Pause by starting `paused` workers and draining/replacing previous active workers;
   propagation requires configuration rollout and drain, not an instant switch.
   Live claims continue/fence/expire; never infer expiry proves remote AI stopped.
7. Roll back only to this schema008-aware `bridge`/`paused` binary. Keep008 tables,
   receipts, tombstones and fencing counters. Never return to732e420 or `off`
   after008, truncate receipts, automatically clear quarantine, or downgrade a
   populated schema. Recover quarantines with explicit operator authorization and
   checkpoint/fence verification before re-admission. Operator tooling remains a
   separately packaged/reviewed production gate.

## Evidence and limits

Tests use real PostgreSQL17.10, synthetic accounts, genuine NOSUPERUSER
NOBYPASSRLS NOINHERIT LOGIN roles from the first connection, and mocked Auth/AI.
Both the real fresh001..007 installer and reconstructed production006→007→008
are tested. The separate shared-control engine uses the same restricted URL.
Image-like subprocesses contain only `app` and a test shutdown probe: Uvicorn
imports the deployed entrypoint with restricted credentials, serves health, and
completes its actual lifespan cleanup. No owner-initialized repository is injected
into those processes. This validates the import closure, not a built Render image.

The Auth/Storage deletion surface is synthetic, including explicit RLS policies.
Actual managed Auth policies, inherited grants, role memberships, Storage use,
Supavisor behavior and platform backup retention remain production evidence gates.
No claim is made that the snapshot role already matches the proposed role.
The packaged runtime reuses reviewed domain logic; performance under final
packaging and remote Chromium/WebKit suites remain release gates.

## Release gate matrix

| Gate | Evidence / pass criterion | Owner | Status |
| --- | --- | --- | --- |
| Ledger restricted startup | Genuine LOGIN first-query ledger read; INSERT/UPDATE/DELETE/TRUNCATE denied | Engineering | Local PASS |
| Fresh and006 chain | Real fresh installer and reconstructed006→007→008, API lifecycle and replay | Engineering | Local PASS |
| Shared engine grants | Same LOGIN URL; control read, planner, reservation erasure; no config-column writes | Engineering | Local PASS |
| Deployable import closure | Uvicorn from app-only directory, healthy startup and completed shutdown | Engineering | Local PASS |
| Default-off / activation / pause | Default-off unit checks, schema008 off refusal, fleet-drain assertion, pre-auth pause | Engineering | Local PASS |
| Fencing / idempotency / recovery | Packaged deterministic cancellation, stale claims, duplicate finalizers, account isolation, check-in/closing/quarantine cases | Engineering | Local PASS |
| Erasure | Restricted LOGIN deletion against synthetic Auth RLS and other-account survival | Engineering | Local PASS; managed permissions pending |
| Actual Render role/settings | Whitelisted Shell snapshots; role memberships, schema grants, process count, effective flags | Owner + Engineering | Pending snapshots |
| Current pooler capacity | Actual session mode, per-role backend pools, client counts, waiting counts,24h peaks, overlap budget | Owner + Engineering | Pending snapshots |
| Managed Auth / Storage grants | Verify actual policies and intended role privileges; no blanket managed-schema grants | Engineering + Owner | Pending |
| service_role dangerous grants | Separate application-table/default-ACL review; explicit authorized change plan | Engineering + Owner | Proposal only |
| Final remote CI / browser matrix | All required checks on final packaged candidate | Engineering | Not yet run for this branch |
| Final-path performance | Final packaged candidate against approved baseline, identical methodology | Engineering | Pending |
| Physical iPhone / retention | Prior prepared device checklist and approved retention/deletion policy | Owner | Pending |
| Activation / maintenance / recovery tooling | Explicit approval, fleet/admission procedure and deployable operator tooling rehearsal | Owner + Engineering | Pending; NO-GO |

## Required snapshot additions

Return only the whitelisted JSON from the previously prepared role and flag
snapshot blocks. Do not send connection strings, keys, JWTs or environment dumps.
Also supply effective role memberships/INHERIT, public-schema CREATE/table
ownership, CREATEROLE/CREATEDB/REPLICATION, and auth/storage table grants/policies for the intended backend role
(read-only catalog inspection can follow after the role is known). Confirm actual
worker/process count and whether more than one service uses these credentials.
For Supavisor: mode, backend pool size **per database/user**, client limit, current
active/idle/waiting clients, current backend usage and timestamped24h peaks; list
other consumers. Unused PostgreSQL server slots do not establish pooler headroom.

## Test results

- Broad backend regression:2068 passed,79 skipped, one existing Starlette/httpx
  deprecation warning. This was run before the final additive role/CORS/grant
  checks; the final enabled-mode paths are covered by the targeted suites below.
- Combined PostgreSQL/startup/packaged/compatibility regression:78 passed.
- Final genuine-LOGIN/artifact/CORS/shutdown/erasure cases:7 passed after adding
  interruption rollback and allowed-origin pause assertions.
- Final packaged runtime, startup and control tests:33 passed.
- Genuine LOGIN cases rerun with offload/lazy-ping/read-autocommit/state-only
  transition settings enabled and checkpoints disabled:7 passed.
- Existing P2 sender/recovery Node tests:40 passed, no changes to transport policy.
- Logs are in `docs/aud94-startup-evidence`; no provider-backed tests were run.

The grant artifact is executed only on disposable databases. An injected failure
before COMMIT rolls back all ledger grants/policies; the successful transaction
then allows read-only ledger startup. The subprocess shutdown marker is emitted
after the application's lifespan cleanup, not merely inferred from SIGTERM exit.
The API pause is inserted before authentication while preserving outer CORS and
privacy middleware. Auth deletion assertions use explicit synthetic RLS policies.
