# Audio database round trips (AUD-21)

Review-only optimization; no production deployment, migration, permission, provider,
transport or feature-flag change. Production remains a23977c.

## Production evidence

Six assessed turns averaged 22.897s from last detected speech to substantive audio.
Coaching responses issued 45/45/49/45/45/56 statements, averaging 3.926s in database
operations versus 0.438s in the TTS provider span. All six recorded zero loop-blocked
milliseconds; existing offload already covers preparation, persistence and loading.
The lesson's 2.476s event-loop stall belongs to other routes and is outside this fix.

A second owner-run lesson, on the same production revision, averaged 21.546s (median
21.127s, slowest25.024s). Audio database time remained3.906s. The largest improvement
was audio-body delivery (2.808→1.194s), not this undeployed patch. Both lessons persisted
two exercises and had no server5xx or pool exhaustion errors. Six optional acknowledgment
requests per lesson were rejected409; that separate behavior is not changed here.

Historical pg_stat_statements averages for ordinary owned SELECT families were
roughly0.3–0.9ms of PostgreSQL execution; application round trips are around70–80ms.
Those cumulative statistics include historical outliers and are not per-lesson timings.

## Root cause and change

The ordinary audio request performs a verified identity/profile lookup, account claim
admission, cue preparation, atomic cache persistence, owned audio loading and receipt
finalization. The profile reproduces45 statements before terminal feedback adds more.
Most ordinary statements take about76ms through the72ms RTT proxy. Statements that
start transactions also include the BEGIN round trip. No single query dominates.

- Read exercise, conversation, evaluated result, tutor voice and associated lesson in
  one account-scoped joined SELECT. Reuse it only inside this request's preparation.
  Recheck the live lesson checkpoint after TTS and before persistence.
- Check asset ownership once inside the existing atomic cache save. Asset and mapping
  still commit or roll back together.
- Receipt finalization previously repeated the begin hook's six-statement fence within
  the same transaction. Reuse its proof only for the exact connection, context and
  owner/id/fence tuple. Clear it on begin, commit, rollback and savepoint boundaries.
  Profile/slot locks stay held; database guards still check every authoritative write.
  Receipt UPDATE itself checks live status/fence/expiry, with no extra round trip.
  Connection invalidation cleanup must not reconnect just to clear local metadata.

No transaction is split; no authorization, deadline, RLS or ownership check is cached
across transactions or requests. No connection is retained through provider awaits.

## Controlled comparison

Disposable PostgreSQL17, actual schema008 and restricted LOGIN role, production flags,
72ms RTT proxy and six synthetic turns (two exercises, two follow-ups each). Both runs
use identical mocked STT and the six production assessment/TTS provider delay samples.
A test clock reproduces the final response's closing-grace budget transaction, yielding
the same baseline statement counts. No production learner data or providers are used.

| Measure | Before | After |
| --- | ---: | ---: |
| Full HTTP turn, upload start→audio body |12.935s|11.934s|
| Coaching endpoint total |4.771s|3.775s|
| Coaching database statements |45–56|33–41|
| Coaching database time |3.852s|2.868s|
| TTS provider span |0.439s|0.439s|
| Maximum sampled event-loop lag |18.28ms|21.42ms|
| Peak checked-out connections |1|1|
| Checked-out connections at provider entry |0|0|
| Connections/active claims after run |0/0|0/0|

Database time improved25.6%; full HTTP turns improved1.00s. Small event-loop sampling
variation is not an improvement claim: audio work was already offloaded. Local HTTP
turns exclude phone/Vercel transport, VAD and real audio download, so they must not be
presented as production22.9→11.9s. A production before/after still needs an approved
code deployment and a comparable iPhone lesson.

Results with samples and source hashes: `aud21-audio-db-benchmark.json`. Full normalized
SQL profiles remain local; no parameters or private audio/transcripts are exported.

Run the existing `scripts/pg_delay_proxy.py` against a disposable loopback PostgreSQL
server with36ms one-way delay. Set AUDLI_TEST_POSTGRES_URL to that disposable cluster,
then run `python scripts/benchmark_coach_audio_db.py --proxy-port PORT --output FILE`.
AUDLI_AUDIO_BENCH_ROOT can select the baseline checkout. Never load provider credentials.

## Validation

Targeted audio/offload/cache/authorization/lifecycle tests:179 passed,1 skipped.
PostgreSQL plus release-safety, restricted-role, cancellation, rollback, idempotency and
audio regressions:369 passed,2 skipped. Eight new transaction-proof lifecycle cases pass,
including live expiry, mutable context, savepoint rollback and connection invalidation.
Full backend:2090 passed,79 skipped. Existing query budgets are lowered, not waived.

## Administrative query distinction

COPY audio_assets ran as postgres and matches previous logical backup/export commands.
Eleven retained local backup archives predate these lessons. The fingerprint cursor
and canonical row-JSON hashing match our prior diagnostic execution (October14).
Per-execution timestamps are unavailable in pg_stat_statements (stats sinceOctober6),
and successful SELECT/COPY statements aren't recorded under current DDL-only logging.
Exact overlap/network contention therefore cannot be reconstructed or ruled out.

The full WITH gr query is WITH grand_total/base/query_results over pg_stat_statements,
pg_authid and index_advisor, explicitly marked source:dashboard and executed as
supabase_read_only_user. Catalog table metadata is administrative introspection.
These are not learner-handler SQL. The lesson windows have no PostgreSQL/Supavisor
error-severity events or Render pool errors. No exports, fingerprints or data deletion
were run during this optimization; only bounded read-only metadata/log inspection.
