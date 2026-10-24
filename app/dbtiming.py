"""AUD-82: numeric-only request-overhead timing (authentication, pool, queries, account lease).

Stage names are code constants. Nothing here records SQL text, bind parameters, tokens, user or learner
identifiers, URLs or exception messages. Everything is installed only when ``latency_aggregates`` is on.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

from sqlalchemy import event

from app.telemetry import aggregator

# Per request: one dict shared by every task/thread the request spawns (ContextVars copy the reference).
request_db: ContextVar[dict | None] = ContextVar('audli_request_db', default=None)

KEYS = ('queries', 'query_ms', 'checkouts', 'checkout_ms', 'auth_ms', 'lease_ms', 'transactions', 'txn_end_ms')


def new_request_accumulator() -> dict:
    return dict.fromkeys(KEYS, 0)


def _add(key_ms: str, key_count: str | None, elapsed_ms: float) -> None:
    accumulator = request_db.get()
    if accumulator is not None:
        accumulator[key_ms] += elapsed_ms
        if key_count:
            accumulator[key_count] += 1


@contextmanager
def span(name: str, accumulate: str | None = None):
    """Time a block into the aggregate (and optionally the per-request total), also when it raises."""
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = (perf_counter() - started) * 1000
        aggregator.record(name, elapsed)
        if accumulate:
            _add(accumulate, None, elapsed)


def instrument_engine(engine, kind: str) -> None:
    """Wrap one SQLAlchemy engine. ``kind`` is 'db' (the shared pool) or 'lease' (per-request connections)."""
    main = kind == 'db'

    @event.listens_for(engine, 'before_cursor_execute')
    def started(conn, cursor, statement, parameters, context, executemany):
        context._audli_started = perf_counter()

    @event.listens_for(engine, 'after_cursor_execute')
    def finished(conn, cursor, statement, parameters, context, executemany):
        began = getattr(context, '_audli_started', None)
        if began is None:
            return
        elapsed = (perf_counter() - began) * 1000
        aggregator.record(f'{kind}.query', elapsed)
        if main:
            _add('query_ms', 'queries', elapsed)

    @event.listens_for(engine, 'do_connect')
    def physical_connect(dialect, connection_record, cargs, cparams):
        # New TCP/TLS/authentication handshake; returning the connection keeps SQLAlchemy's behaviour.
        began = perf_counter()
        try:
            return dialect.connect(*cargs, **cparams)
        finally:
            aggregator.record(f'{kind}.connect_physical', (perf_counter() - began) * 1000)

    if main:
        @event.listens_for(engine, 'begin')
        def began(conn):
            accumulator = request_db.get()
            if accumulator is not None:
                accumulator['transactions'] += 1

    pool = engine.pool
    original_connect = pool.connect

    def timed_connect(*args, **kwargs):
        # Whole pool checkout: waiting for a free slot, the pre-ping and any new physical connection.
        began = perf_counter()
        try:
            return original_connect(*args, **kwargs)
        finally:
            elapsed = (perf_counter() - began) * 1000
            aggregator.record(f'{kind}.checkout', elapsed)
            if main:
                _add('checkout_ms', 'checkouts', elapsed)
    pool.connect = timed_connect

    dialect = engine.dialect

    def timed_end(name, original):
        # COMMIT or ROLLBACK reaching the server (including the reset-on-return rollback after a read) is a network
        # round trip; on an idle connection the driver returns at once, so counts show how many were real.
        def wrapper(dbapi_connection):
            began = perf_counter()
            try:
                return original(dbapi_connection)
            finally:
                elapsed = (perf_counter() - began) * 1000
                aggregator.record(f'{kind}.{name}', elapsed)
                if main:
                    _add('txn_end_ms', None, elapsed)
        return wrapper
    dialect.do_commit = timed_end('commit', dialect.do_commit)
    dialect.do_rollback = timed_end('rollback', dialect.do_rollback)

    original_ping = dialect.do_ping

    def timed_ping(dbapi_connection):
        began = perf_counter()
        try:
            return original_ping(dbapi_connection)
        finally:
            aggregator.record(f'{kind}.pool_ping', (perf_counter() - began) * 1000)
    dialect.do_ping = timed_ping


def finish_request(method: str, total_ms: float, route: str | None = None) -> None:
    """Fold the per-request totals into aggregates. Reads and writes are kept apart; 'unattributed' still
    contains provider calls, commits/rollbacks, serialization and event-loop waits."""
    accumulator = request_db.get()
    if accumulator is None:
        return
    group = 'read' if method in ('GET', 'HEAD', 'OPTIONS') else 'write'
    database = accumulator['query_ms'] + accumulator['checkout_ms']
    aggregator.record(f'req.{group}.database', database)
    aggregator.record(f'req.{group}.auth_verify', accumulator['auth_ms'])
    aggregator.record(f'req.{group}.lease', accumulator['lease_ms'])
    aggregator.record(f'req.{group}.txn_end', accumulator['txn_end_ms'])
    aggregator.record(f'req.{group}.unattributed', max(0.0, total_ms - database - accumulator['txn_end_ms']
                                                        - accumulator['auth_ms'] - accumulator['lease_ms']))
    # Route template only (e.g. POST /api/lessons/{lesson_id}/heard), never the concrete path.
    aggregator.record_route(f'{method} {route}' if route else 'unmatched', accumulator['queries'], accumulator['checkouts'],
                            accumulator['transactions'])
    aggregator.record_gauge(f'req.{group}.db_queries', accumulator['queries'])
    aggregator.record_gauge(f'req.{group}.db_checkouts', accumulator['checkouts'])
