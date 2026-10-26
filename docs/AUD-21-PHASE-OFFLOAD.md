# AUD-21 — offload lesson phase database work

The existing `AUDLI_DB_OFFLOAD` switch now also covers exercise preparation reads, generated-file persistence/cleanup, the complete existing `save_exercise` transaction and final projection; lesson checkpoint reads/writes and public response projection; failed lease-candidate rollback; and cancellation-result cleanup. The switch and all other defaults are unchanged. No new engines or pool settings are introduced.

Database sections remain sequential within each request and inherit the verified account ContextVars. A running synchronous section finishes before cancellation releases the application lock/account lease. Provider awaits remain outside repository transactions. Generated-file cleanup removes failed/partially written files; it does not remove audio after a successful commit when the HTTP request is cancelled. Normal responses, uncertainty gates, lesson revisions, STT, upload IDs and retry policy are unchanged.

`tests/test_phase_offload.py` injects failure after related rows are inserted, checks rollback and checkpoint/file cleanup, cancels while a real PostgreSQL transaction holds the account lease (both commit and rollback outcomes), probes health during blocked response projection and failed-candidate rollback, exercises repeated cancellation during cleanup, and creates phases concurrently under separate verified owners. Existing offload full-lesson state equivalence and idempotency tests remain required.

## Repeatable local measurement

Use a disposable loopback PostgreSQL server only, with a password-bearing local URL. The benchmark uses mocked providers/authentication and no production credentials. `AUDLI_CI_OFFLINE=1` with `PYTHONPATH=ci:.` blocks external networking; the child server inherits the guard. `--cached-ack` models the normal warm acknowledgment path without the older benchmark's extra cue request. `--structured` includes opening check-ins, lesson phase projection/persistence and advancement. Upload start to coaching audio body completion is a proxy, not physical speech-end-to-audible latency.

Run this from the modified checkout, first with `--app-root` set to a clean deployed-source checkout, then the modified checkout, then repeat in reverse order. Keep pooled leases false in every configuration. Repeat with `--structured` to exercise the lesson routes; omitted `--structured` gives an exercise-path comparison.

```sh
AUDLI_CI_OFFLINE=1 AUDLI_LEASE_POOL=false AUDLI_DB_OFFLOAD=true \
AUDLI_DB_LAZY_PING=true AUDLI_DB_STATE_ONLY_TRANSITIONS=true \
AUDLI_DB_STATE_ONLY_CHECKPOINTS=false AUDLI_UPLOAD_IDEMPOTENCY=true \
AUDLI_UPLOAD_RETRY=true PYTHONPATH=ci:. \
python scripts/bench_turns.py \
  --admin-url postgresql://audli_bench:local@127.0.0.1:55439/postgres \
  --app-root /path/to/checkout --config sample:AUDLI_LEASE_POOL=false \
  --cached-ack --structured --parallel 1 --proxy-ms 72 \
  --sequential-turns 4 --learners 4 --turns-per-learner 2 \
  --out /tmp/audli-phase-sample.json
```

Report event-loop lag/health-probe tails separately from per-route and turn latency. Removing a loop freeze does not remove that request's database work. Check query/checkout counts, connection peaks, HTTP errors and persisted rows alongside timings. Small sample p95 values are descriptive; mocked-provider local results do not establish production p50/p95.

## Review and rollout

This change is active wherever the existing DB offload flag is already true; it is not a dormant feature. Production deployment therefore requires explicit approval. The unrelated focus fix can roll out independently first. Keep pooled leases and all other settings unchanged. After an approved backend deployment, verify revision/health/flags with non-billable checks, then obtain separate approval for provider-backed acceptance if needed. Compare phase-route loop stalls, connection utilization and request/turn latency; watch rollback/cancellation failures, 409/5xx, duplicate attempt/assessment counts and checkpoint consistency. Roll back the code revision if these regress; do not adjust retry policy or enable another flag to mask a regression.

Runtime Python/SDK versions and lease overrides cannot be inferred from requirements or health. Await the authorized read-only runtime snapshot and Supavisor limits; do not change settings to obtain them.
