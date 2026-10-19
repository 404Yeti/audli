"""Event-loop lag caused by waiting on the per-account lease (AUD-68 review). Numbers only; needs a disposable Postgres.

Replicates the exact polling in app/auth.py (new NullPool connection per attempt, SELECT pg_try_advisory_xact_lock,
sleep 50 ms) for N concurrent waiters on one held lock, while a ticker measures how late `asyncio.sleep(0.005)` wakes up.

    AUDLI_TEST_POSTGRES_URL=postgresql://user:pw@127.0.0.1:54329/postgres .venv/bin/python scripts/measure_lease_loop_lag.py
"""
import asyncio, json, os, statistics
from time import perf_counter
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

URL = os.environ['AUDLI_TEST_POSTGRES_URL'].replace('postgresql://', 'postgresql+psycopg://', 1)
KEY = 424242


async def poll(engine, stop, attempts):
    while not stop.is_set():
        started = perf_counter()
        candidate = engine.connect()
        try:
            candidate.execute(text("SELECT set_config('idle_in_transaction_session_timeout', '0', true)"))
            acquired = candidate.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': KEY}).scalar_one()
        finally:
            candidate.close()
        attempts.append((perf_counter() - started) * 1000)    # time the event loop was blocked by this attempt
        await asyncio.sleep(.05)


async def trial(engine, waiters, seconds=2.0):
    stop, lags, attempts = asyncio.Event(), [], []
    async def ticker():
        while not stop.is_set():
            started = perf_counter(); await asyncio.sleep(.005)
            lags.append((perf_counter() - started - .005) * 1000)
    tasks = [asyncio.create_task(ticker())] + [asyncio.create_task(poll(engine, stop, attempts)) for _ in range(waiters)]
    await asyncio.sleep(seconds); stop.set(); await asyncio.gather(*tasks)
    lags.sort()
    return {'waiters': waiters, 'blocking_ms_per_attempt_p50': round(statistics.median(attempts), 1) if attempts else None,
            'loop_lag_ms_p50': round(statistics.median(lags), 1), 'loop_lag_ms_p99': round(lags[int(.99 * (len(lags) - 1))], 1),
            'loop_lag_ms_max': round(lags[-1], 1), 'attempts': len(attempts)}


async def main():
    engine = create_engine(URL, poolclass=NullPool, connect_args={'connect_timeout': 5, 'prepare_threshold': None})
    blocker = create_engine(URL, poolclass=NullPool).connect(); tx = blocker.begin()
    blocker.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': KEY})      # another request holds the lease
    try:
        results = [await trial(engine, n) for n in (0, 1, 2, 5, 10, 20)]
    finally:
        tx.rollback(); blocker.close()
    print(json.dumps({'harness': 'local PostgreSQL 17 in Docker (loopback); production connects over a network, so each blocking attempt is longer',
                      'results': results}, indent=1))

asyncio.run(main())
