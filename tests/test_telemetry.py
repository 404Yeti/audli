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
    assert agg.snapshot() == {'version': 1, 'buckets_ms': list(BUCKETS_MS), 'stages': {}, 'lock_queue_depth_at_arrival': {}}


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
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith('Latency summary '))
    payload = json.loads(line.removeprefix('Latency summary '))
    names = set(payload['stages'])
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
