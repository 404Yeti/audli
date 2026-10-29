"""Numeric-only telemetry: aggregation, bounds, privacy and the client report endpoint."""
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.telemetry import BUCKETS_MS, LatencyAggregator, MAX_STAGES, emit_summary, aggregator
from conftest import ASGIClient, FakeProvider
from test_auth import AccountClient, auth_response, settings_for  # noqa: F401
from test_persistence import repository_factory  # noqa: F401


def test_histogram_counts_errors_and_upper_bound_quantiles():
    agg = LatencyAggregator()
    for ms in (10, 10, 10, 10, 10, 10, 10, 10, 10, 4000):
        agg.record('stage', ms)
    agg.record('stage', 70000, ok=False)
    stage = agg.snapshot()['stages']['stage']
    assert stage['count'] == 11 and stage['errors'] == 1
    assert stage['p50_ms_upper'] == 25            # bucket upper bound, not an exact sample
    assert stage['p99_ms_upper'] == 70000.0       # overflow bucket reports the observed peak
    assert stage['max_ms'] == 70000.0
    assert len(BUCKETS_MS) + 1 == len(agg._stages['stage'].buckets)


@pytest.mark.parametrize('bad', [float('nan'), -1.0, float('inf')])
def test_invalid_durations_are_dropped(bad):
    agg = LatencyAggregator(); agg.record('stage', bad)
    assert agg.snapshot()['stages'] == {}


def test_cardinality_and_names_are_bounded():
    agg = LatencyAggregator()
    for i in range(MAX_STAGES + 50):
        agg.record(f'stage.{i}', 1)
    agg.record('learner e@x.com said hello?', 1)   # not a code constant: never becomes a key
    agg.record('x' * 500, 1)
    stages = agg.snapshot()['stages']
    assert len(stages) <= MAX_STAGES + 1 and 'other' in stages
    assert not any('@' in name or len(name) > 96 for name in stages)


def test_snapshot_reset_starts_a_new_window():
    agg = LatencyAggregator(); agg.record('a', 5); agg.record_queue_depth(3)
    assert agg.snapshot(reset=True)['lock_queue_depth_at_arrival'] == {'3': 1}
    assert agg.snapshot() == {'version': 1, 'buckets_ms': list(BUCKETS_MS), 'stages': {}, 'gauges': {}, 'routes': {}, 'lock_queue_depth_at_arrival': {}}


def test_aggregation_is_off_by_default(tmp_path):
    aggregator.reset()
    app = create_app(Settings(data_dir=tmp_path, provider='demo', _env_file=None), FakeProvider())
    client = ASGIClient(app, FakeProvider())
    assert client.get('/api/profile').status_code == 200
    assert aggregator.snapshot()['stages'] == {}


def test_summary_log_contains_route_templates_but_no_ids_text_or_credentials(tmp_path, caplog, audio_bytes):
    aggregator.reset()
    sentinel_user = 'f3c1a9d2-5b7e-4c1a-9d2e-aa00bb11cc22'
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path, provider='openai', latency_aggregates=True, _env_file=None), provider)
    client = ASGIClient(app, provider)
    exercise = client.post('/api/exercises').json()
    client.post(f"/api/exercises/{exercise['id']}/conversation/listened")
    attempt = client.post(f"/api/exercises/{exercise['id']}/attempts",
                          files={'audio': ('answer.wav', audio_bytes, 'audio/wav')}, headers={'Authorization': 'Bearer secret-token-xyz'}).json()
    client.post(f"/api/attempts/{attempt['id']}/assess", json={'text': attempt['transcription']['text'], 'confirmed': True, 'followup_id': None})
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        emit_summary()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith('Latency summary ')]
    parts = [json.loads(line.removeprefix('Latency summary ')) for line in lines]
    assert [p['part'] for p in parts] == list(range(1, len(parts) + 1)) and all(p['parts'] == len(parts) for p in parts)
    assert len({p['window'] for p in parts}) == 1
    names = {name for p in parts for name in p['stages']}
    line = '\n'.join(lines)
    assert 'http.POST /api/exercises/{exercise_id}/attempts' in names
    assert 'turn.upload.lock_wait' in names and 'turn.upload.lock_hold' in names
    for forbidden in (exercise['id'], attempt['id'], 'rolled back', 'secret-token-xyz', sentinel_user, 'Bearer'):
        assert forbidden not in line
    assert aggregator.snapshot()['stages'] == {}   # emitting starts a new window


def test_client_report_requires_authentication_and_strict_numeric_schema(tmp_path, repository_factory):
    import asyncio
    aggregator.reset()
    provider = FakeProvider()
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, latency_aggregates=True), provider, repository=repository_factory(None), auth_client=http)
    client = AccountClient(app, provider, 'valid-a')
    anonymous = ASGIClient(app, provider)
    assert anonymous.post('/api/telemetry/turn', json={'stages': {'assessment': 5}}).status_code == 401
    assert client.post('/api/telemetry/turn', json={'stages': {'assessment': 1234, 'turn_to_playback': 8000}}).status_code == 204
    for body in ({'stages': {}}, {'stages': {'free text': 5}}, {'stages': {'assessment': 'The learner said'}},
                 {'stages': {'assessment': 5}, 'learner': 'x'}, {'stages': {'assessment': 5.5}}, {'stages': {'assessment': -1}}):
        assert client.post('/api/telemetry/turn', json=body).status_code == 422, body
    stages = aggregator.snapshot()['stages']
    assert stages['client.assessment']['count'] == 1 and stages['client.turn_to_playback']['count'] == 1
    app.state.repository.close(); asyncio.run(http.aclose())


def test_recording_overhead_is_microseconds_and_memory_is_constant():
    import time, tracemalloc
    agg = LatencyAggregator()
    for i in range(500):
        agg.record(f'warm.{i % 50}', 3)
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    started = time.perf_counter()
    for i in range(50_000):
        agg.record(f'stage.{i % 40}', (i % 97) * 3.0)
    per_call_us = (time.perf_counter() - started) / 50_000 * 1e6
    growth = sum(s.size_diff for s in tracemalloc.take_snapshot().compare_to(before, 'filename') if 'telemetry.py' in str(s.traceback))
    tracemalloc.stop()
    assert per_call_us < 100, per_call_us          # generous CI bound; typical is a few microseconds
    assert growth < 64 * 1024, growth              # 50k samples add no per-sample memory


def test_summary_loop_survives_a_failing_window(monkeypatch, caplog):
    import asyncio
    from app import telemetry
    calls = []
    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError('boom')
    monkeypatch.setattr(telemetry, 'emit_summary', flaky)
    async def run():
        task = asyncio.create_task(telemetry.summary_loop(0.01))
        await asyncio.sleep(0.12)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
    assert len(calls) >= 3          # kept reporting after the failure, plus the shutdown flush


def test_telemetry_report_never_waits_for_the_account_lease(tmp_path, repository_factory, monkeypatch):
    """Postgres only: a held account lease blocks real writes but not the (state-free) timing report."""
    import asyncio
    from hashlib import sha256
    from sqlalchemy import text
    from test_auth import USER_A
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')
    aggregator.reset()
    provider = FakeProvider()
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, latency_aggregates=True), provider, repository=repo, auth_client=http)
    key = int.from_bytes(sha256(('audli-account:' + USER_A).encode()).digest()[:8], 'big', signed=True)
    headers = {'Authorization': 'Bearer valid-a'}
    from app.auth import LearnerAuthentication
    app.middleware_stack = app.build_middleware_stack()
    auth = app.middleware_stack
    while not isinstance(auth, LearnerAuthentication):
        auth = auth.app
    attempts = []
    original = auth._take_lease
    async def run():
        contended = asyncio.Event()
        loop = asyncio.get_running_loop()
        def observe(key):
            candidate, acquired = original(key)
            attempts.append(acquired)
            loop.call_soon_threadsafe(contended.set)
            return candidate, acquired
        monkeypatch.setattr(auth, '_take_lease', observe)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            blocker = repo.engine.connect(); transaction = blocker.begin()
            try:
                blocker.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})   # another worker is saving this account
                report = await client.post('/api/telemetry/turn', json={'stages': {'assessment': 5}}, headers=headers)
                # Observe the isolation property directly: authentication/profile
                # startup can take >500ms on a busy runner without lease polling.
                assert report.status_code == 204 and attempts == []
                mutation = asyncio.create_task(client.put('/api/profile', json={'name': 'A', 'goal': 'B'}, headers=headers))
                try:
                    await asyncio.wait_for(contended.wait(), .6)
                    assert attempts and not any(attempts)
                    assert not mutation.done()  # the real writer is still excluded
                finally:
                    mutation.cancel()
                    await asyncio.gather(mutation, return_exceptions=True)
            finally:
                transaction.rollback(); blocker.close()
            return report.status_code
    assert asyncio.run(run()) == 204
    app.state.repository.close(); asyncio.run(http.aclose())


def test_summary_is_split_into_small_consistent_lines(caplog):
    from app import telemetry
    agg_names = [f'http.POST /api/route/{i}/{{id}}' for i in range(80)] + [f'stage.{i}.lock_wait' for i in range(60)]
    aggregator.reset()
    for name in agg_names:
        aggregator.record(name, 1234.5)
    aggregator.record_queue_depth(2)
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        emit_summary()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith('Latency summary ')]
    parts = [json.loads(line.removeprefix('Latency summary ')) for line in lines]
    assert len(parts) == -(-len(agg_names) // telemetry.MAX_LINE_STAGES) and len(parts) > 1
    assert max(len(line) for line in lines) < 8000                       # far below any log-line limit
    assert {n for p in parts for n in p['stages']} == set(agg_names)       # nothing lost or duplicated
    assert sum(len(p['stages']) for p in parts) == len(agg_names)
    assert 'buckets_ms' in parts[0] and 'buckets_ms' not in parts[1]       # shared fields only once
    assert parts[0]['lock_queue_depth_at_arrival'] == {'2': 1}
    caplog.clear()
    emit_summary()                                                          # empty window: no log noise
    assert not [r for r in caplog.records if r.getMessage().startswith('Latency summary ')]


# AUD-82: request-overhead timing (authentication, pool, queries, lease).
def _timed_accounts(tmp_path, repository_factory, **extra):
    from test_auth import accounts_with
    return accounts_with(tmp_path, repository_factory, latency_aggregates=True, **extra)


def test_request_overhead_stages_are_recorded_for_a_read(tmp_path, repository_factory):
    aggregator.reset()
    gen = _timed_accounts(tmp_path, repository_factory)
    a, _, _ = next(gen)
    try:
        assert a.get('/api/profile').status_code == 200
        snap = aggregator.snapshot()
    finally:
        gen.close()
    stages = snap['stages']
    for name in ('auth.verify', 'auth.scope', 'db.query', 'db.checkout', 'req.read.database', 'req.read.auth_verify', 'req.read.unattributed'):
        assert name in stages and stages[name]['count'] >= 1, name
    assert snap['gauges']['req.read.db_queries'] and snap['gauges']['req.read.db_checkouts']
    assert 'req.write.database' not in stages
    # Reads take no account lease, so no lease stages exist.
    assert not any(name.startswith('auth.lease') for name in stages)


def test_timing_is_inert_when_aggregation_is_off(tmp_path, repository_factory):
    from test_auth import accounts_with
    aggregator.reset()
    gen = accounts_with(tmp_path, repository_factory)
    a, _, _ = next(gen)
    try:
        assert a.get('/api/profile').status_code == 200
    finally:
        gen.close()
    assert aggregator.snapshot()['stages'] == {}


def test_write_requests_are_grouped_separately_and_stay_private(tmp_path, repository_factory, caplog):
    aggregator.reset()
    gen = _timed_accounts(tmp_path, repository_factory)
    a, _, _ = next(gen)
    try:
        a.post('/api/lessons/start', json={})
        caplog.set_level(logging.INFO, logger='uvicorn.error')
        emit_summary()
        snap_text = caplog.text
    finally:
        gen.close()
    assert 'req.write.database' in snap_text
    for forbidden in ('valid-a', 'Bearer', 'SELECT', 'INSERT', 'sb_publishable', 'project.supabase.co', '@'):
        assert forbidden not in snap_text, forbidden
    import re
    assert not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-', snap_text)


def test_lease_stages_with_a_stubbed_engine_including_contention():
    """The lease branch needs Postgres; a stub proves the stage names, retry counting and unchanged control flow."""
    import asyncio
    from types import SimpleNamespace
    from app.auth import LearnerAuthentication, RequestRepository

    class Result:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value

    class Connection:
        def __init__(self, engine): self.engine, self.closed = engine, False
        def execute(self, statement, params=None):
            if 'pg_try_advisory_xact_lock' in str(statement):
                self.engine.attempts += 1
                return Result(self.engine.attempts > 2)   # busy twice, then acquired
            return Result(None)
        def close(self): self.closed = True

    class Engine:
        attempts = 0
        def connect(self): return Connection(self)

    async def identity_verify(headers): return '00000000-0000-4000-8000-000000000001'
    repository = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='sqlite')),
        for_learner=lambda learner: SimpleNamespace(learner=learner))
    request_repository = RequestRepository()
    seen = []
    async def app(scope, receive, send): seen.append(request_repository.current.get())
    middleware = LearnerAuthentication(app, SimpleNamespace(verify=identity_verify), repository, request_repository, timing=True)
    middleware.lease_engine = Engine()
    aggregator.reset()
    asyncio.run(middleware({'type': 'http', 'method': 'POST', 'path': '/api/lessons/start', 'headers': []}, None, None))
    snap = aggregator.snapshot()
    assert seen and snap['stages']['auth.lease']['count'] == 1
    assert snap['stages']['auth.lease_connect']['count'] == 3 and snap['stages']['auth.lease_wait_sleep']['count'] == 2
    assert snap['stages']['auth.verify']['count'] == 1 and snap['stages']['auth.scope']['count'] == 1
    assert snap['gauges']['auth.lease_attempts'] == {'3': 1}


def test_postgres_connection_and_lease_stages(tmp_path, repository_factory):
    aggregator.reset()
    gen = _timed_accounts(tmp_path, repository_factory)
    a, _, _ = next(gen)
    try:
        if a.app.state.repository.engine.dialect.name != 'postgresql':
            pytest.skip('Postgres-only: physical connections, pool ping and the account lease')
        assert a.post('/api/lessons/start', json={}).status_code < 500
        assert a.get('/api/profile').status_code == 200 and a.get('/api/profile').status_code == 200  # reused pooled connection is pinged
        stages = aggregator.snapshot()['stages']
    finally:
        gen.close()
    # The shared pool opened its connections at startup, before timing was installed, so only the per-request
    # lease engine shows physical connects here; reused pooled connections are pinged on checkout.
    for name in ('auth.lease', 'auth.lease_connect', 'auth.lease_lock_query', 'lease.connect_physical', 'lease.query',
                 'db.pool_ping', 'db.checkout'):
        assert name in stages, name


def test_engine_instrumentation_sees_connects_pings_checkouts_and_queries(tmp_path):
    """A fresh pre-ping engine (SQLite stands in for Postgres): one physical connect, then pings on reuse."""
    from sqlalchemy import create_engine, text
    from app.dbtiming import instrument_engine
    engine = create_engine('sqlite:///' + str(tmp_path / 'timing.sqlite3'), pool_pre_ping=True)
    instrument_engine(engine, 'db')
    aggregator.reset()
    for _ in range(3):
        with engine.connect() as db:
            db.execute(text('SELECT 1'))
    stages = aggregator.snapshot()['stages']
    assert stages['db.connect_physical']['count'] == 1
    assert stages['db.checkout']['count'] == 3 and stages['db.query']['count'] == 3
    assert stages['db.pool_ping']['count'] == 2        # the two reused checkouts
    engine.dispose()


def test_pause_measurements_are_recorded_as_bounded_levels(tmp_path, repository_factory):
    import asyncio
    aggregator.reset()
    provider = FakeProvider()
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, latency_aggregates=True), provider, repository=repository_factory(None), auth_client=http)
    client = AccountClient(app, provider, 'valid-a')
    for pause, span in ((0, 3400), (1250, 6200), (1990, 9000), (250000, 120000)):
        assert client.post('/api/telemetry/turn', json={'stages': {'longest_pause': pause, 'speech_span': span}}).status_code == 204
    snap = aggregator.snapshot()
    assert snap['gauges']['client.longest_pause_100ms'] == {'0': 1, '12': 1, '19': 1, '32': 1}      # capped at 3.2 s
    assert snap['gauges']['client.speech_span_s'] == {'3': 1, '6': 1, '9': 1, '32': 1}
    assert 'client.longest_pause' not in snap['stages']                                            # not mixed into latency histograms
    assert client.post('/api/telemetry/turn', json={'stages': {'longest_pause': -1}}).status_code == 422
    assert client.post('/api/telemetry/turn', json={'stages': {'pause_text': 5}}).status_code == 422
    app.state.repository.close(); asyncio.run(http.aclose())
