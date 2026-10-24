# AUD-87 — Synthetic voice-turn benchmark

Purpose: replace one-production-class-per-experiment with a repeatable, no-production-data benchmark that exercises the real application.

## What is real and what is stubbed
Real: the FastAPI app (`app.main.create_app`) served by uvicorn in its own process, all routes and middleware, the Supabase-style authentication middleware, the per-account PostgreSQL advisory lease, the per-learner asyncio locks, the repository, SQLAlchemy pools, and a real PostgreSQL 17 database (fresh database per configuration, migrated from `docs/postgres*.sql`).

Stubbed with fixed production-like latencies (`scripts/bench_app.py`): the Supabase Auth HTTP call (300 ms), transcription (1.1 s), assessment (3.5 s), TTS (0.45 s) and exercise generation (2 s). The database link goes through `scripts/pg_delay_proxy.py`, which adds 72 ms of round trip (the measured Singapore to Tokyo figure). No production data, credentials or external providers are involved.

## The turn model
Taken from the browser code and the 2026-10-24 production logs: upload the recording; start the assessment at once; if it has not finished after 1.2 s request the acknowledgment cue; request the coaching audio when the assessment and the cue request have both returned. The reported "turn" time is from the start of the upload to the end of the coaching audio response, the server-observed part of speech-end-to-coaching (browser-side silence detection, token wait and network add to it).

## Scenarios per configuration
* Sequential: one learner, 20 turns (10 exercises of two turns each) after a 2-turn warm-up.
* Concurrent: 4 learners at the same time, 8 turns each (32 turns), against the same single server process.
A side probe requests `/api/health` ten times a second, so the black-box responsiveness of the event loop is measured as the clients see it.

## Running it
```
python scripts/bench_turns.py --admin-url postgresql://user:pw@127.0.0.1:5432/postgres \
  --config baseline:AUDLI_DB_READ_AUTOCOMMIT=true \
  --config offload:AUDLI_DB_READ_AUTOCOMMIT=true,AUDLI_DB_OFFLOAD=true \
  --app-root /path/to/checkout --out results.json
```
`--config NAME:FLAG=value,...` is repeatable; `--parallel N` runs configurations at the same time (each has its own database, proxy and server). The admin URL needs permission to create databases. Latencies can be changed with `AUDLI_BENCH_AUTH_MS`, `TRANSCRIBE_MS`, `ASSESS_MS`, `SPEAK_MS`, `GENERATE_MS`.

## What it reports
Per scenario: turn latency p50/p95, and p50 of the upload, assessment, acknowledgment-cue, coaching-gap (assessment end to coaching request) and coaching routes; the health-probe p95/max. Server side (from the final `Latency summary`): event-loop lag mean/max, account-lease time mean/max, how many lease acquisitions needed a retry, pool checkouts, statements, lease connections, and the database work of the assessment route (statements / checkouts).

## Caveats
* Single run per configuration, one local machine: differences under about 1 s are within noise; the large effects are not.
* Stubbed providers have fixed latencies and no jitter; concurrency is 4 learners, not production traffic.
* Production settings the benchmark assumes: `per_learner_locks` on, `db_read_autocommit` on (P2), lesson limits off, a pool of 3 database connections (the application default).
