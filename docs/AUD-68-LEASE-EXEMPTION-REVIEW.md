# AUD-68 — security review of the account-lease exemption

Scope: `app/auth.py` `LEASE_EXEMPT_PATHS = {'/api/telemetry/turn'}` (commit "exempt the state-free telemetry report from the per-account save lease").
Verdict: **the exemption is exact, narrow, authenticated and cannot reach learner state.** Evidence: `tests/test_auth_lease_exemption.py`
(65 cases; the Postgres-only ones ran against a disposable PostgreSQL 17 locally and run in CI's PostgreSQL job).

## What the lease is
For PostgreSQL, `LearnerAuthentication` takes a per-account advisory lock for the whole of every non-GET/HEAD/OPTIONS `/api` request, so two requests
for one account (e.g. a save and a reset, possibly on different workers) cannot interleave across provider awaits. It is **not** the authentication
step; identity is verified against Supabase first, and the scoped repository is set afterwards.

## What changed
Only the lease acquisition is skipped, and only when `scope['path']` is *exactly* `/api/telemetry/turn`. Identity verification, the origin/body-size
guard (outermost middleware), routing, schema validation and the scoped-repository binding are unchanged.

## Claims and proof
| Claim | Proof |
|---|---|
| The set is exactly one literal path, immutable | `test_exactly_one_exact_path_is_exempt` (frozenset equality; widening needs a deliberate test edit) |
| It resolves to exactly one route: `POST`, handler `turn_report`; no other route pattern matches it | `test_exempt_path_is_one_post_route…` |
| The handler cannot reach learner state | Same test, by AST: module imports ⊆ {typing, fastapi, pydantic, app.models, app.telemetry}; handler body references only the body model, `settings.latency_aggregates`, `aggregator.record` and `Response`. No repository, lock, DB, lesson or profile access. `test_telemetry_handler_only_writes_the_in_memory_aggregate`: profile and history unchanged after a report. |
| Authentication is not skipped | Missing, invalid, anonymous, unconfirmed tokens → 401; Supabase outage → fail-closed; duplicate/malformed `Authorization` → 401; rejected requests record nothing (`test_authentication_is_never_skipped`, `…duplicate_or_malformed…`) |
| Outer protections still run | Foreign `Origin` → 403, body > 40 000 B → 413, unknown fields / wrong types / negatives → 422 (`test_origin_guard_and_body_limit…`) |
| Other methods on the path do nothing | GET/PUT/PATCH/DELETE → 405, nothing recorded |
| Near-miss paths are not exempt | 14 raw-ASGI variants (trailing slash, case, double slash, encoded slash, dot-segments, `;param`, NUL, space, sibling names) never reach the handler; on PostgreSQL every `/api…` variant **queues behind a held lease** |
| Percent-encoded forms of the *exact* path are the same route, not a bypass | `/api/telemetry/%74urn`, `/api%2Ftelemetry/turn` decode to the exact path; the router matches the same decoded path |
| Every other mutating route still takes the lease | PostgreSQL: all 40+ POST/PUT/PATCH/DELETE routes (full route table, not a sample) wait while the lease is held; the exempt route answers 204; a real write proceeds after release |
| Reads never took the lease | `test_read_only_requests_never_took_the_lease_and_still_do_not` |
| Every route lives under the authenticated prefix | `test_every_route_lives_under_the_authenticated_prefix` |

Mutation checks (each must fail the suite): prefix match instead of equality → fails; widening the set to `/api/profile` → fails (2 tests);
case-insensitive compare → fails; removing the exemption → the telemetry POST gets **409 after ~5 s** (the original risk).

## Residual observations (not caused by this change)
1. **Path-prefix gating.** The auth middleware only wraps paths starting with lowercase `/api`; everything else is passed straight to the router, which is
   case-sensitive and has no non-`/api` routes, so `/API/...` is a plain 404 with no authentication. Safe today **because** all routes are under `/api`
   (now guarded by a test). Defense in depth would be to reject (not pass through) unauthenticated non-`/api` traffic.
2. **Lease polling blocks the event loop** (AUD-72). Each attempt is a synchronous connect on the loop: ~17–18 ms on loopback; loop lag p50 31 ms with 5 waiters,
   118 ms with 10, 290 ms with 20 (`docs/AUD-68-LEASE-LOOP-LAG.json`). Production connects over a network, so it is worse. This is exactly why telemetry must not wait for the lease.
3. **Abuse.** An authenticated user could POST reports repeatedly; cost is one Supabase identity check + a bounded in-memory update (≤160 stage names, fixed buckets, ≤300 000 per value). No log line per request. Not a new class of exposure (every authenticated route can be called repeatedly).
4. Each report adds one identity verification per meaningful turn (the browser sends one per turn, not per stage).
