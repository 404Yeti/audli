"""AUD-72 investigation PROTOTYPE. Not used by the application. Compares ways to wait for the per-account advisory lease.

Variants (all keep the same exclusion primitive: a transaction-scoped Postgres advisory lock on one key):
  v0_current        what app/auth.py does today: sync connect + try-lock on the event loop, sleep 50 ms, <=100 tries
  v1_thread_new     same SQL, each attempt in a worker thread (new connection per attempt)
  v2_thread_reuse   one connection per waiter, each try-lock attempt in a worker thread, rollback between tries
  v3_async_wait     native psycopg async, ONE connection, blocking pg_advisory_xact_lock bounded by a transaction-local
                    lock_timeout (the production Session pooler discards startup options, so set_config(..., true))

Measures, for N waiters on a held lock: event-loop lag, process CPU seconds per wall second, connections opened;
and correctness: hand-off latency, give-up time, cancellation cleanup, mutual exclusion, acquisition order.
Disposable local PostgreSQL only (AUDLI_TEST_POSTGRES_URL). Loopback has no TLS and ~0 RTT, so CPU and blocking are LOWER BOUNDS.

    AUDLI_TEST_POSTGRES_URL=postgresql://u:p@127.0.0.1:54329/postgres .venv/bin/python scripts/experiments/lease_polling_approaches.py
"""
import asyncio, json, os, statistics, sys
from time import perf_counter, process_time

import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

RAW = os.environ['AUDLI_TEST_POSTGRES_URL']
URL = make_url(RAW)
SA_URL = URL.set(drivername='postgresql+psycopg')
KEY = 777001
TIMEOUT_S = 5.0
opened = 0


def sa_engine():
    return create_engine(SA_URL, poolclass=NullPool, connect_args={'connect_timeout': 5, 'prepare_threshold': None})


class Gave_up(Exception):
    pass


# ----------------------------------------------------------------------------------------------- variants
async def v0_current(engine):
    global opened
    for _ in range(100):
        opened += 1
        candidate = engine.connect()
        try:
            candidate.execute(text("SELECT set_config('idle_in_transaction_session_timeout', '0', true)"))
            acquired = candidate.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': KEY}).scalar_one()
        except BaseException:
            candidate.close(); raise
        if acquired:
            return candidate
        candidate.close()
        await asyncio.sleep(.05)
    raise Gave_up()


def _attempt_new(engine):
    global opened
    opened += 1
    candidate = engine.connect()
    try:
        candidate.execute(text("SELECT set_config('idle_in_transaction_session_timeout', '0', true)"))
        if candidate.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': KEY}).scalar_one():
            return candidate
    except BaseException:
        candidate.close(); raise
    candidate.close()
    return None


async def _to_thread_safely(function, *args):
    """Cancellation-safe: if cancelled while the thread runs, let it finish and release whatever it acquired."""
    task = asyncio.ensure_future(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        async def cleanup():
            try:
                result = await task
                if result is not None:
                    await asyncio.to_thread(result.close)
            except Exception:
                pass
        asyncio.ensure_future(cleanup())
        raise


async def v1_thread_new(engine):
    for _ in range(100):
        candidate = await _to_thread_safely(_attempt_new, engine)
        if candidate is not None:
            return candidate
        await asyncio.sleep(.05)
    raise Gave_up()


async def v2_thread_reuse(engine):
    global opened
    opened += 1
    connection = await _to_thread_safely(lambda: _open_for_reuse(engine))
    try:
        for _ in range(100):
            got = await _to_thread_safely(_try_on, connection)
            if got is not None:
                return connection
            await asyncio.sleep(.05)
        raise Gave_up()
    except BaseException:
        await asyncio.shield(asyncio.to_thread(connection.close)); raise


def _open_for_reuse(engine):
    connection = engine.connect()
    connection.execute(text("SELECT set_config('idle_in_transaction_session_timeout', '0', true)"))
    connection.rollback()
    return connection


def _try_on(connection):
    if connection.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': KEY}).scalar_one():
        return connection
    connection.rollback()          # end the transaction so the next try starts a fresh one
    return None


async def v3_async_wait(_engine):
    global opened
    opened += 1
    connection = await psycopg.AsyncConnection.connect(host=URL.host, port=URL.port, user=URL.username, password=URL.password,
                                                       dbname=URL.database, connect_timeout=5, prepare_threshold=None, **URL.query)
    try:
        await connection.execute("SELECT set_config('idle_in_transaction_session_timeout', '0', true)")
        await connection.execute(f"SELECT set_config('lock_timeout', '{int(TIMEOUT_S * 1000)}ms', true)")
        await connection.execute('SELECT pg_advisory_xact_lock(%s)', (KEY,))
        return connection
    except psycopg.errors.LockNotAvailable:
        await connection.close(); raise Gave_up()
    except BaseException:
        await asyncio.shield(connection.close()); raise


VARIANTS = {'v0_current': v0_current, 'v1_thread_new': v1_thread_new, 'v2_thread_reuse': v2_thread_reuse, 'v3_async_wait': v3_async_wait}


async def release(lease):
    if isinstance(lease, psycopg.AsyncConnection):
        await lease.close()
    else:
        await asyncio.to_thread(lease.close)


# ----------------------------------------------------------------------------------------------- harness
def holder():
    connection = create_engine(SA_URL, poolclass=NullPool).connect()
    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': KEY})
    return connection


def server_connections():
    probe = create_engine(SA_URL, poolclass=NullPool).connect()
    try:
        return probe.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'")).scalar_one()
    finally:
        probe.close()


async def load(name, waiters, seconds=2.0):
    global opened
    engine, fn, lags = sa_engine(), VARIANTS[name], []
    held, stop, opened = holder(), asyncio.Event(), 0
    async def ticker():
        while not stop.is_set():
            started = perf_counter(); await asyncio.sleep(.005); lags.append((perf_counter() - started - .005) * 1000)
    tasks = [asyncio.create_task(ticker())] + [asyncio.create_task(fn(engine)) for _ in range(waiters)]
    cpu0, wall0 = process_time(), perf_counter()
    await asyncio.sleep(seconds)
    peak = server_connections()
    cpu, wall = process_time() - cpu0, perf_counter() - wall0
    stop.set()
    for task in tasks[1:]:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    held.rollback(); held.close()
    await asyncio.sleep(.3)
    leftover = server_connections()
    lags.sort()
    return {'waiters': waiters, 'loop_lag_ms_p50': round(statistics.median(lags), 1), 'loop_lag_ms_p99': round(lags[int(.99 * (len(lags) - 1))], 1),
            'loop_lag_ms_max': round(lags[-1], 1), 'cpu_s_per_wall_s': round(cpu / wall, 3), 'connections_opened_in_window': opened,
            'server_connections_during': peak, 'server_connections_after_cancel': leftover}


async def handoff(name):
    """Holder releases at 0.5 s; time from release to the waiter owning the lock."""
    engine, held = sa_engine(), holder()
    task = asyncio.create_task(VARIANTS[name](engine))
    await asyncio.sleep(.5)
    released = perf_counter(); held.rollback(); held.close()
    lease = await asyncio.wait_for(task, 10)
    latency = (perf_counter() - released) * 1000
    await release(lease)
    return round(latency, 1)


async def give_up(name):
    engine, held = sa_engine(), holder()
    started = perf_counter()
    try:
        lease = await VARIANTS[name](engine); await release(lease); outcome = 'ACQUIRED(!)'
    except Gave_up:
        outcome = 'gave_up'
    finally:
        held.rollback(); held.close()
    return outcome, round(perf_counter() - started, 2)


async def cancel_cleanup(name):
    """Cancel a waiter mid-wait: no leaked connection, no phantom lock holder."""
    engine, held = sa_engine(), holder()
    before = server_connections() - 1          # minus the holder
    task = asyncio.create_task(VARIANTS[name](engine))
    await asyncio.sleep(.4)
    task.cancel(); await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(.5)
    leaked = server_connections() - 1 - before
    held.rollback(); held.close()
    lease = await asyncio.wait_for(VARIANTS[name](engine), 3)    # a fresh waiter must acquire at once: nobody else holds the key
    await release(lease)
    await asyncio.sleep(.3)
    return {'leaked_connections': leaked, 'lock_free_after_release': True, 'connections_after': server_connections()}


async def exclusion_and_order(name, waiters=6):
    engine, held, order, active, overlap = sa_engine(), holder(), [], [0], [0]
    async def worker(i):
        lease = await VARIANTS[name](engine)
        active[0] += 1; overlap[0] = max(overlap[0], active[0]); order.append(i)
        await asyncio.sleep(.05); active[0] -= 1
        await release(lease)
    tasks = []
    for i in range(waiters):
        tasks.append(asyncio.create_task(worker(i))); await asyncio.sleep(.03)     # staggered arrival
    await asyncio.sleep(.2); held.rollback(); held.close()
    await asyncio.wait_for(asyncio.gather(*tasks), 30)
    return {'max_simultaneous_holders': overlap[0], 'acquisition_order': order, 'arrival_order_preserved': order == sorted(order)}


async def main():
    results = {'harness': 'disposable local PostgreSQL 17, loopback, no TLS: CPU/blocking are lower bounds', 'variants': {}}
    for name in VARIANTS:
        print('==', name, file=sys.stderr)
        entry = {'load': [await load(name, n) for n in (1, 5, 20)], 'handoff_ms_after_release': await handoff(name)}
        outcome, seconds = await give_up(name)
        entry['give_up'] = {'outcome': outcome, 'seconds': seconds}
        entry['cancellation'] = await cancel_cleanup(name)
        entry['exclusion_and_order'] = await exclusion_and_order(name)
        results['variants'][name] = entry
    print(json.dumps(results, indent=1))

asyncio.run(main())
