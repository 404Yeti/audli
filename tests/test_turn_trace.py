"""AUD-85: per-turn request tracing is off by default, bounded and privacy-safe, and explains loop stalls."""
import asyncio
import json
import logging
import re
import time

import pytest

from app import turntrace
from app.config import Settings
from app.main import create_app
from app.repository import ProgressRepository
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory  # noqa: F401  (fixture)

OWNER = '00000000-0000-4000-8000-000000000001'
TURN = 'a1b2c3d4e5f6'
UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


@pytest.fixture(autouse=True)
def clean_trace_state():
    turntrace.enabled = False
    turntrace.reset()
    yield
    turntrace.enabled = False
    turntrace.reset()


def app_for(tmp_path, **settings):
    repo = ProgressRepository(tmp_path / 'x.sqlite3', OWNER)
    app = create_app(Settings(data_dir=tmp_path, latency_aggregates=True, _env_file=None, **settings), FakeProvider(), repo)
    return app, repo


def trace_lines(caplog):
    return [json.loads(record.getMessage()[len('Turn trace '):]) for record in caplog.records if record.getMessage().startswith('Turn trace ')]


def test_off_by_default():
    assert Settings(_env_file=None).turn_trace is False


def test_flag_off_ignores_the_header_and_logs_nothing(tmp_path, caplog):
    app, repo = app_for(tmp_path)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    assert ASGIClient(app, FakeProvider()).get('/api/profile', headers={'X-Audli-Turn': TURN}).status_code == 200
    assert trace_lines(caplog) == [] and turntrace.enabled is False
    repo.close()


def test_trace_needs_latency_aggregates(tmp_path, caplog):
    repo = ProgressRepository(tmp_path / 'x.sqlite3', OWNER)
    create_app(Settings(data_dir=tmp_path, turn_trace=True, _env_file=None), FakeProvider(), repo)
    assert turntrace.enabled is False
    repo.close()


def test_a_traced_request_logs_one_bounded_numeric_line(tmp_path, caplog):
    app, repo = app_for(tmp_path, turn_trace=True)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    response = ASGIClient(app, FakeProvider()).get('/api/profile', headers={'X-Audli-Turn': TURN, 'Authorization': 'Bearer secret-token-value'})
    assert response.status_code == 200
    (line,) = trace_lines(caplog)
    assert line['turn'] == TURN and line['kind'] == 'server' and line['route'] == 'GET /api/profile' and line['status'] == 200
    assert line['end_ms'] >= line['start_ms'] and line['total_ms'] >= 0 and line['loop_blocked_ms'] >= 0
    assert len(line['spans']) <= turntrace.MAX_SPANS and len(line['loop_blocks']) <= turntrace.MAX_BLOCKS
    raw = json.dumps(line)
    assert not UUID.search(raw) and 'secret-token-value' not in raw and 'Bearer' not in raw and OWNER not in raw
    assert not re.search(r'\b(select|insert|update|delete)\b', raw, re.I)
    repo.close()


@pytest.mark.parametrize('value', ['', 'A1B2C3D4E5F6', 'a1b2c3d4e5f', 'a1b2c3d4e5f67', 'a1b2c3d4e5g6', 'a1b2c3d4e5f6\r\nX: y', 'x' * 500])
def test_malformed_turn_ids_are_ignored(tmp_path, caplog, value):
    app, repo = app_for(tmp_path, turn_trace=True)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    try:
        ASGIClient(app, FakeProvider()).get('/api/profile', headers={'X-Audli-Turn': value})
    except Exception:
        pass        # an illegal header value may be rejected by the client itself; either way nothing is logged
    assert trace_lines(caplog) == []
    repo.close()


def test_requests_without_the_header_are_not_traced(tmp_path, caplog):
    app, repo = app_for(tmp_path, turn_trace=True)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    ASGIClient(app, FakeProvider()).get('/api/profile')
    assert trace_lines(caplog) == []
    repo.close()


def test_route_template_hides_concrete_ids():
    assert turntrace.template('/api/exercises/0efe4ebf-1ce8-489d-b1ef-52d822746224/attempts') == '/api/exercises/{id}/attempts'


# ----------------------------------------------------------------------------------------- loop-blocking attribution
def test_only_sections_on_the_loop_thread_count_as_blocking():
    turntrace.enabled = True
    trace = turntrace.Trace(TURN)
    token = turntrace.request_trace.set(trace)
    try:
        async def run():
            turntrace.sync_section('db.query', time.perf_counter(), 120.0)                  # on the loop: blocks it
            await asyncio.to_thread(turntrace.sync_section, 'db.query', time.perf_counter(), 900.0)   # worker thread: does not
            turntrace.sync_section('db.query', time.perf_counter(), 5.0)                    # below the threshold
        asyncio.run(run())
    finally:
        turntrace.request_trace.reset(token)
    assert trace.blocked_ms == 120.0 and [block[0] for block in trace.blocks] == ['db.query']


def test_sync_section_is_a_noop_when_tracing_is_off():
    turntrace.sync_section('db.query', time.perf_counter(), 500.0)
    assert not turntrace._ring


def test_stall_is_attributed_to_overlapping_sections_and_the_rest_is_unexplained(caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    turntrace.enabled = True
    now = turntrace.now_ms()
    turntrace._ring.extend([(now - 100, 600.0, 'lease.checkout', TURN), (now - 10, 200.0, 'db.query', None),
                            (now - 5000, 400.0, 'db.commit', 'ffffffffffff')])       # the last one is long before the stall
    turntrace.report_stall(1000.0)
    (record,) = [json.loads(r.getMessage()[len('Loop stall '):]) for r in caplog.records if r.getMessage().startswith('Loop stall ')]
    assert record['lag_ms'] == 1000 and record['by_kind'] == {'lease.checkout': 600, 'db.query': 200}
    assert record['explained_ms'] == 800 and record['unexplained_ms'] == 200
    assert record['by_turn'] == {TURN: 600, '-': 200}


def test_small_lag_is_not_reported(caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    turntrace.enabled = True
    turntrace.report_stall(50.0)
    assert not [r for r in caplog.records if r.getMessage().startswith('Loop stall')]


# ----------------------------------------------------------------------------------------- lease contention
def test_a_waiting_request_records_which_request_held_the_lease():
    turntrace.enabled = True
    holder, waiter = turntrace.Trace('111111111111'), turntrace.Trace(TURN)
    turntrace.lease_acquired(7, holder, 'POST', '/api/attempts/0efe4ebf-1ce8-489d-b1ef-52d822746224/assess')
    turntrace.lease_contended(7, waiter)
    waited = waiter.extra['waited_on']
    assert waited['turn'] == '111111111111' and waited['route'] == 'POST /api/attempts/{id}/assess' and waited['held_ms_at_first_wait'] >= 0
    turntrace.lease_released(7)
    turntrace.lease_contended(7, turntrace.Trace('222222222222'))     # released: nothing to report, no error
    assert 7 not in turntrace.lease_holders


# ----------------------------------------------------------------------------------------- browser report
def report(app, body, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    return ASGIClient(app, FakeProvider()).post('/api/telemetry/turn', json=body)


def test_browser_spans_are_logged_only_when_tracing(tmp_path, caplog):
    body = {'stages': {'upload_transcription': 10000}, 'turn': TURN, 'requests': [{'kind': 'upload', 'start': 1900, 'token': 4300, 'ttfb': 5200}]}
    app, repo = app_for(tmp_path)
    assert report(app, body, caplog).status_code == 204 and trace_lines(caplog) == []
    repo.close()
    app, repo = app_for(tmp_path, turn_trace=True)
    assert report(app, body, caplog).status_code == 204
    (line,) = trace_lines(caplog)
    assert line == {**line, 'kind': 'client', 'turn': TURN, 'requests': [{'kind': 'upload', 'start': 1900, 'token': 4300, 'ttfb': 5200}],
                    'stages': {'upload_transcription': 10000}}
    repo.close()


@pytest.mark.parametrize('extra', [{'turn': 'not-hex'}, {'turn': TURN.upper()}, {'requests': [{'kind': 'other', 'start': 0, 'token': 0, 'ttfb': 0}]},
                                   {'requests': [{'kind': 'upload', 'start': -1, 'token': 0, 'ttfb': 0}]},
                                   {'requests': [{'kind': 'upload', 'start': 0, 'token': 0, 'ttfb': 0, 'url': '/x'}]},
                                   {'requests': [{'kind': 'ack', 'start': 0, 'token': 0, 'ttfb': 0}] * 17}, {'user': 'x'}])
def test_browser_report_schema_rejects_anything_else(tmp_path, caplog, extra):
    app, repo = app_for(tmp_path, turn_trace=True)
    assert report(app, {'stages': {'assessment': 5}, **extra}, caplog).status_code == 422
    assert trace_lines(caplog) == []
    repo.close()


def test_old_browsers_without_trace_fields_still_work(tmp_path, caplog):
    app, repo = app_for(tmp_path, turn_trace=True)
    assert report(app, {'stages': {'assessment': 5}}, caplog).status_code == 204 and trace_lines(caplog) == []
    repo.close()


# ----------------------------------------------------------------------------------------- PostgreSQL: the real lease
def test_lease_polling_runs_on_the_event_loop_and_is_traced(tmp_path, repository_factory, monkeypatch, caplog):
    """The waiting request retries the account lease with a new connection each time, synchronously on the loop."""
    from test_auth_lease_exemption import build, client_for, hold_lease, HEADERS
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')
    monkeypatch.setattr(turntrace, 'BLOCK_MIN_MS', 0.0)
    monkeypatch.setattr(turntrace, 'MAX_BLOCKS', 500)          # the per-request cap keeps the LONGEST blocks; every kind must survive here
    from app.config import Settings as S
    import httpx
    from test_auth import auth_response, settings_for
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, latency_aggregates=True, turn_trace=True, db_offload=False), FakeProvider(), repository=repo, auth_client=http)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    async def run():
        async with client_for(app) as client:
            blocker, transaction = hold_lease(repo)
            try:
                pending = asyncio.create_task(client.post('/api/recognition/acknowledgment-audio', json={'cue': 'assessment'},
                                                          headers=HEADERS | {'X-Audli-Turn': TURN}))
                await asyncio.sleep(.4)
                assert not pending.done()                     # queued behind the lease
            finally:
                transaction.rollback(); blocker.close()
            return await pending
    asyncio.run(run())
    (line,) = [l for l in trace_lines(caplog) if l['route'].startswith('POST')]
    assert line['lease_attempts'] >= 3 and line['turn'] == TURN
    kinds = {block[0] for block in line['loop_blocks']}
    assert {'lease.checkout', 'lease.query'} <= kinds           # connection + lock query ran on the loop thread
    assert line['loop_blocked_ms'] > 0 and any(span[0] == 'auth.lease' for span in line['spans'])
    app.state.repository.close(); asyncio.run(http.aclose())


# ----------------------------------------------------------------------------------------- analysis script
def test_analysis_script_joins_browser_and_server_and_finds_the_lease_holder(capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('analyze_turn_trace', Path(__file__).resolve().parent.parent / 'scripts' / 'analyze_turn_trace.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    t = 'a1b2c3d4e5f6'
    def server(route, start, total, **extra):
        return 'INFO:     Turn trace ' + json.dumps({'v': 1, 'kind': 'server', 'turn': t, 'route': route, 'status': 200, 'start_ms': start, 'end_ms': start + total,
                                                      'total_ms': total, 'loop_blocked_ms': 0, 'spans': [], 'loop_blocks': [], **extra})
    lines = [
        server('POST /api/exercises/{exercise_id}/attempts', 100_000, 5000),
        server('POST /api/attempts/{attempt_id}/assess', 106_000, 9000, spans=[['auth.verify', 0, 300], ['auth.lease', 300, 600]], loop_blocked_ms=1200),
        server('POST /api/recognition/acknowledgment-audio', 108_500, 7000, lease_attempts=9,
               spans=[['auth.lease', 0, 6800]], waited_on={'turn': t, 'route': 'POST /api/attempts/{id}/assess', 'held_ms_at_first_wait': 2500}),
        'INFO:     Turn trace ' + json.dumps({'v': 1, 'kind': 'client', 'turn': t, 'end_ms': 130_000, 'stages': {'turn_to_playback': 27000}, 'requests': [
            {'kind': 'upload', 'start': 1800, 'token': 50, 'ttfb': 5200}, {'kind': 'assess', 'start': 5900, 'token': 100, 'ttfb': 9100},
            {'kind': 'ack', 'start': 8400, 'token': 1500, 'ttfb': 8400}]}),
        'INFO:     Loop stall ' + json.dumps({'v': 1, 'end_ms': 112_000, 'lag_ms': 3000, 'explained_ms': 2900, 'unexplained_ms': 100,
                                             'by_kind': {'lease.checkout': 2900}, 'by_turn': {t: 2900}}),
        'unrelated line',
    ]
    server_records, client_records, stalls = module.parse(lines)
    (turn,) = [module.analyse_turn(k, v, client_records.get(k), stalls) for k, v in server_records.items()]
    by_route = {r['route']: r for r in turn['requests']}
    ack = by_route['POST /api/recognition/acknowledgment-audio']
    assert ack['waited_on']['route'] == 'POST /api/attempts/{id}/assess' and ack['lease_wait_ms'] == 6800 and ack['lease_attempts'] == 9
    upload = by_route['POST /api/exercises/{exercise_id}/attempts']
    assert upload['browser']['kind'] == 'upload' and by_route['POST /api/attempts/{attempt_id}/assess']['browser']['kind'] == 'assess'
    assert min(r['pre_server_delay_ms'] for r in turn['requests']) == 0 and ack['pre_server_delay_ms'] > 0
    assert turn['stalls'][0]['by_kind'] == {'lease.checkout': 2900} and turn['loop_blocked_ms'] == 1200
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.log', delete=False) as handle:
        handle.write('\n'.join(lines))
    assert module.main([handle.name]) == 0
    printed = capsys.readouterr().out
    assert 'waited on POST /api/attempts/{id}/assess' in printed and 'loop stall 3000 ms' in printed and 'pre-server delay' in printed
