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

## Reconciled comparison against PR118

The branch is based on deployed merge `5948590`. Only conversation audio,
repository preparation/persistence and same-transaction fence deduplication change
runtime behavior. Plan/homework changes from PR118 remain intact. `/heard`, check-in
offloading, providers, speech conflicts, schema, flags, routing and upload transport
are outside this PR. The separate offload working patch was preserved in the local
production-preparation evidence before being removed from this branch.

Disposable PostgreSQL17, schema008 and restricted LOGIN, 72ms RTT proxy, six synthetic
turns with identical fixed mocked STT/assessment/TTS delays. Unlike the original
benchmark, the final cue does not cross the closing deadline, matching the latest
production45–49-statement pattern. `--closing-grace` retains the earlier budget-path
experiment. Never load production/provider credentials.

| Measure | PR118 baseline | Reconciled PR117 |
| --- | ---: | ---: |
| Full HTTP turn, upload start→audio body |12.809s|11.823s|
| Coaching endpoint total |4.651s|3.655s|
| Coaching database statements |45–49|33–34|
| Coaching SQL execution/round-trip time |3.751s|2.763s|
| TTS provider span |0.439s|0.439s|
| Maximum sampled event-loop lag |15.89ms|17.98ms|
| Peak checked-out connections |1|1|
| Checked-out connections at provider entry |0|0|
| Connections/active claims after run |0/0|0/0|

Six attempts, two evaluations and two adaptations persisted in each run. SQL time
improved26.3%; the full HTTP path improved0.986s. Audio was already offloaded: small
loop-sampling variation is not an improvement claim. SQL time here includes transport
inside execute calls and excludes checkout/commit timing; do not equate it with server
SQL execution alone or the broader database budget.

The primary production baseline remains **22.66s speech-end→browser playing**.
Local HTTP turns omit browser VAD, recording/policy preparation, phone/Vercel transport
and playback. These results are not production22.66→11.82s, nor a promise of a five-second
voice interaction. After separately approved deployment, compare complete assessed
turns, median/tail, provider spans, DB counts/time, stalls, uncertainty/retries and
playback failures using existing telemetry.

Numeric results/source hashes: `aud21-audio-db-benchmark.json`. Full normalized SQL
profiles remain local; parameters and learner data are not exported.

Reproduce with `scripts/pg_delay_proxy.py` at36ms each way and a disposable
AUDLI_TEST_POSTGRES_URL, then `python scripts/benchmark_coach_audio_db.py --proxy-port
PORT --output FILE`. AUDLI_AUDIO_BENCH_ROOT selects the baseline application tree.

## Exact ordinary statement budget

Auth scope1 + claim9 + preparation7 + post-TTS checkpoint/atomic save12 + owned audio
load1 + receipt15 =45. Feedback adds4. The joined preparation removes5 ordinary or8
feedback reads, the atomic save removes1 duplicate owner check, and receipt finalization
removes6 same-transaction fencing statements: ordinary33, feedback34. The cue lookup
itself remains one owned SELECT; it is not an N+1 query. Cold restored files and closing
grace can add their existing authorized reads/budget transaction.

## Reconciled validation

Targeted SQLite/PostgreSQL audio, ownership, lifecycle checkpoint, offload, query-budget,
transaction-proof and packaged claims regressions: **181 passed,1 skipped**. The
strengthened real-HTTP duplicate/stale-checkpoint case passed again separately. It
checks202 for the same in-flight operation,409 for a competing operation and completed
stream receipt, provider-free explicit replay, unchanged lesson revision, stale revision
rejection and released ownership.

Transaction tests cover commit/rollback/savepoint boundaries, changed owner/context,
expiry during a previously fenced transaction, live receipt UPDATE expiry and invalidated
connection cleanup without reconnecting. Existing pre/post-TTS checkpoint tests reject
stale audio while retaining assessment evidence. No transactions are split and no
provider wait holds a request connection.

Required remote CI must run on the reconciled head; older green checks are insufficient.
No merge or production deployment is authorized by this PR.
