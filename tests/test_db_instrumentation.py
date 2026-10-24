"""AUD-84: per-route database counts, transaction end timing and event-loop lag (numbers only)."""
import asyncio
import json
import logging
import re
import time

from sqlalchemy import create_engine, text

from app.config import Settings
from app.dbtiming import instrument_engine
from app.main import create_app
from app.telemetry import LatencyAggregator, MAX_ROUTES, aggregator, emit_summary, loop_lag_loop
from conftest import ASGIClient, FakeProvider
from test_db_budget import drive_lesson
from test_persistence import repository_factory  # noqa: F401  (fixture)


def test_route_counters_are_bounded_named_by_template_and_reset():
    agg = LatencyAggregator()
    agg.record_route('GET /api/profile', 1, 1, 1)
    agg.record_route('GET /api/profile', 3, 2, 2)
    agg.record_route('learner e@x.com asked something?', 9, 9, 9)           # not a code constant: lands in 'other'
    for i in range(MAX_ROUTES + 20):
        agg.record_route(f'POST /api/r{i}/{{id}}', 1, 1, 1)
    routes = agg.snapshot()['routes']
    assert routes['GET /api/profile'] == {'calls': 2, 'queries_mean': 2.0, 'queries_max': 3, 'checkouts_mean': 1.5, 'checkouts_max': 2,
                                          'transactions_mean': 1.5, 'transactions_max': 2}
    assert len(routes) <= MAX_ROUTES + 1 and 'other' in routes and not any('@' in name for name in routes)
    assert agg.snapshot(reset=True)['routes'] and agg.snapshot()['routes'] == {}


def test_a_request_records_database_work_against_the_route_template_only(repository_factory, tmp_path, audio_bytes, caplog):
    repo, client, _, snapshot = drive_lesson(repository_factory, tmp_path, audio_bytes)
    routes = snapshot['routes']
    assert routes['GET /api/profile']['queries_max'] == 1 and routes['GET /api/profile']['transactions_max'] == 1
    assert routes['GET /api/eligibility']['queries_max'] == 0
    assert any('{lesson_id}' in name or '{exercise_id}' in name for name in routes)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    emit_summary()
    text_out = caplog.text
    assert not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}', text_out) and 'SELECT' not in text_out and 'Bearer' not in text_out
    lines = [json.loads(line.split('Latency summary ', 1)[1]) for line in text_out.splitlines() if 'Latency summary' in line]
    assert any('routes' in part for part in lines) and max(len(json.dumps(part)) for part in lines) < 8000
    assert [part['part'] for part in lines] == list(range(1, len(lines) + 1)) and {part['parts'] for part in lines} == {len(lines)}


def test_transaction_end_timing_counts_commits_and_rollbacks(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'ends.sqlite3'))
    instrument_engine(engine, 'db')
    aggregator.reset()
    with engine.begin() as db:
        db.execute(text('CREATE TABLE t (a INTEGER)'))
        db.execute(text('INSERT INTO t VALUES (1)'))
    with engine.connect() as db:
        db.execute(text('SELECT a FROM t'))
    stages = aggregator.snapshot()['stages']
    assert stages['db.commit']['count'] == 1
    assert stages['db.rollback']['count'] >= 1          # the read's return-to-pool rollback
    engine.dispose()


def test_request_totals_include_transaction_end_and_database_counts(repository_factory, tmp_path, audio_bytes):
    _, _, _, snapshot = drive_lesson(repository_factory, tmp_path, audio_bytes)
    stages = snapshot['stages']
    for group in ('read', 'write'):
        assert stages[f'req.{group}.txn_end']['count'] >= 1 and f'req.{group}.unattributed' in stages
    assert stages['db.commit']['count'] >= 1


def test_nothing_is_recorded_when_aggregation_is_off(repository_factory, tmp_path, audio_bytes):
    aggregator.reset()
    repo = repository_factory()
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()
    client = ASGIClient(create_app(Settings(data_dir=tmp_path, _env_file=None), provider, repo), provider)
    assert client.get('/api/profile').status_code == 200
    snapshot = aggregator.snapshot()
    assert snapshot['routes'] == {} and snapshot['stages'] == {}


def test_loop_lag_sampler_sees_a_blocked_event_loop():
    aggregator.reset()

    async def run():
        task = asyncio.create_task(loop_lag_loop(0.02))
        await asyncio.sleep(0.1)
        time.sleep(0.25)                                # a synchronous call that blocks the loop, as a database round trip does
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
    lag = aggregator.snapshot()['stages']['loop.lag']
    assert lag['count'] >= 5 and lag['max_ms'] >= 200       # the stall is visible
    assert lag['p50_ms_upper'] <= 25                          # a quiet loop is not reported as lagging


def test_lifespan_runs_the_sampler_only_when_aggregation_is_on(tmp_path):
    for enabled in (True, False):
        aggregator.reset()
        provider = FakeProvider()
        app = create_app(Settings(data_dir=tmp_path / str(enabled), latency_aggregates=enabled, _env_file=None), provider)

        async def run():
            async with app.router.lifespan_context(app):
                await asyncio.sleep(0.35)
                return 'loop.lag' in aggregator.snapshot()['stages']      # read before shutdown flushes and resets the window
        assert asyncio.run(run()) is enabled
