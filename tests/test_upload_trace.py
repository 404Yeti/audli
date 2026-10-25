"""AUD-89 P0: upload correlation is off by default, passes bodies through untouched and unbuffered, records disconnects,
cancellation and failures, and never logs audio, text, tokens, addresses or account identifiers."""
import asyncio
import json
import logging
import re

import pytest

from app import turntrace
from app.config import Settings
from app.main import create_app
from app.repository import ProgressRepository
from app.telemetry import aggregator
from app.uploadtrace import UploadTrace
from conftest import ASGIClient, FakeProvider

OWNER = '00000000-0000-4000-8000-000000000001'
UPLOAD = '0123456789abcdef'
TURN = 'a1b2c3d4e5f6'
UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
PATH = '/api/exercises/11111111-2222-4333-8444-555555555555/attempts'


@pytest.fixture(autouse=True)
def clean_state():
    turntrace.enabled = False
    aggregator.reset()
    yield
    turntrace.enabled = False
    aggregator.reset()


def lines(caplog, prefix='Upload trace '):
    return [json.loads(r.getMessage()[len(prefix):]) for r in caplog.records if r.getMessage().startswith(prefix)]


def scope(path=PATH, method='POST', headers=()):
    return {'type': 'http', 'method': method, 'path': path, 'headers': [(k.lower().encode(), v.encode()) for k, v in headers]}


async def drive(app, chunks, *, headers=(), path=PATH, method='POST', after=None, complete=True):
    """Run ``app`` as the server would: deliver ``chunks`` (then ``after`` messages) and collect what it sends."""
    messages = [{'type': 'http.request', 'body': body, 'more_body': index < len(chunks) - (1 if complete else 0)} for index, body in enumerate(chunks)]
    messages += list(after or [])
    queue = list(messages)
    sent = []

    async def receive():
        if queue:
            return queue.pop(0)
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)
    await UploadTrace(app)(scope(path, method, headers), receive, send)
    return sent


async def read_all(receive):
    body = b''
    while True:
        message = await receive()
        if message['type'] != 'http.request':
            return body, message
        body += message.get('body', b'')
        if not message.get('more_body', False):
            return body, None


async def ok_app(scope_, receive, send):
    await read_all(receive)
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': b'{}'})


@pytest.fixture
def enabled():
    turntrace.enabled = True


def test_off_by_default_the_wrapper_is_not_in_the_way(caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    seen = {}

    async def inner(scope_, receive, send):
        seen['receive'] = receive
        await ok_app(scope_, receive, send)
    outer_receive_ids = {}

    async def run():
        async def receive():
            return {'type': 'http.request', 'body': b'x', 'more_body': False}
        outer_receive_ids['receive'] = receive

        async def send(message):
            pass
        await UploadTrace(inner)(scope(), receive, send)
    asyncio.run(run())
    assert seen['receive'] is outer_receive_ids['receive']          # no wrapping at all while the flag is off
    assert lines(caplog) == []


def test_only_post_attempts_routes_are_traced(enabled, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    for path, method in (('/api/profile', 'POST'), (PATH, 'GET'), ('/api/attempts/x/assess', 'POST')):
        asyncio.run(drive(ok_app, [b'a'], path=path, method=method))
    assert lines(caplog) == []
    asyncio.run(drive(ok_app, [b'a'], path='/api/lessons/11111111-2222-4333-8444-555555555555/attempts'))
    asyncio.run(drive(ok_app, [b'a'], path='/api/plan/checkin/attempts'))
    assert [line['route'] for line in lines(caplog)] == ['POST /api/lessons/{id}/attempts', 'POST /api/plan/checkin/attempts']


def test_a_normal_upload_is_counted_and_reports_the_proxy_visible_timings(enabled, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    sent = asyncio.run(drive(ok_app, [b'a' * 10, b'b' * 20, b'c' * 5], headers=[
        ('Content-Length', '35'), ('X-Audli-Upload-Id', UPLOAD), ('X-Audli-Turn', TURN), ('CF-Ray', 'a4ff7317ca85281c-SEA'),
        ('X-Vercel-Id', 'pdx1::zhzl7-1792912755245-c58bd453f099')]))
    assert [m['type'] for m in sent] == ['http.response.start', 'http.response.body']
    (line,) = lines(caplog)
    assert line['route'] == 'POST /api/exercises/{id}/attempts' and not UUID.search(json.dumps(line))
    assert (line['upload'], line['turn'], line['declared'], line['received'], line['status'], line['outcome']) == (UPLOAD, TURN, 35, 35, 200, 'completed')
    assert line['cf_ray'] == 'a4ff7317ca85281c-SEA' and line['vercel_id'].startswith('pdx1::')
    assert 0 <= line['first_ms'] <= line['body_ms'] <= line['total_ms'] and line['disconnect_ms'] is None


def test_the_body_is_never_read_ahead_or_buffered(enabled):
    """Each chunk reaches the handler as soon as it is delivered; a wrapper that read ahead would deadlock this exchange."""
    async def run():
        consumed = [asyncio.Event(), asyncio.Event()]
        delivered = []

        async def receive():
            index = len(delivered)
            if index:
                await asyncio.wait_for(consumed[index - 1].wait(), 2)    # the handler must have taken chunk `index - 1` first
            delivered.append(index)
            return {'type': 'http.request', 'body': b'chunk%d' % index, 'more_body': index < 2}

        async def handler(scope_, wrapped, send):
            received = []
            for index in range(3):
                message = await wrapped()
                received.append(message['body'])
                if index < 2:
                    consumed[index].set()
            assert received == [b'chunk0', b'chunk1', b'chunk2']
            await send({'type': 'http.response.start', 'status': 204, 'headers': []})
        sent = []

        async def send(message):
            sent.append(message)
        await UploadTrace(handler)(scope(), receive, send)
        assert sent and delivered == [0, 1, 2]
    asyncio.run(run())


def test_a_client_that_goes_away_mid_body_is_recorded_not_swallowed(enabled, caplog):
    """The body guard returns silently on a disconnect; before this nothing recorded that an upload was cut off."""
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    async def guard_like(scope_, receive, send):
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
    asyncio.run(drive(guard_like, [b'a' * 100], headers=[('Content-Length', '5000'), ('X-Audli-Upload-Id', UPLOAD)],
                      after=[{'type': 'http.disconnect'}], complete=False))
    (line,) = lines(caplog)
    assert line['outcome'] == 'disconnect_before_body' and line['received'] == 100 and line['declared'] == 5000
    assert line['status'] is None and line['body_ms'] is None and line['disconnect_ms'] is not None


def test_a_disconnect_after_the_body_but_before_the_response_is_distinguished(enabled, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    async def slow_handler(scope_, receive, send):
        await read_all(receive)
        message = await receive()
        assert message['type'] == 'http.disconnect'
    asyncio.run(drive(slow_handler, [b'abc'], after=[{'type': 'http.disconnect'}]))
    (line,) = lines(caplog)
    assert line['outcome'] == 'disconnect_before_response' and line['received'] == 3 and line['body_ms'] is not None


def test_a_response_sent_after_a_late_disconnect_is_still_a_completed_request(enabled, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    async def handler(scope_, receive, send):
        await read_all(receive)
        assert (await receive())['type'] == 'http.disconnect'
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    asyncio.run(drive(handler, [b'abc'], after=[{'type': 'http.disconnect'}]))
    (line,) = lines(caplog)
    assert line['outcome'] == 'completed' and line['disconnect_ms'] is not None


def test_a_cancelled_request_is_recorded_and_the_cancellation_still_propagates(enabled, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    async def run():
        started = asyncio.Event()

        async def hanging(scope_, receive, send):
            await read_all(receive)
            started.set()
            await asyncio.sleep(3600)
        task = asyncio.create_task(drive(hanging, [b'abc']))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    (line,) = lines(caplog)
    assert line['outcome'] == 'cancelled' and line['received'] == 3 and line['status'] is None


def test_a_handler_error_is_recorded_and_still_raised(enabled, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')

    async def broken(scope_, receive, send):
        await read_all(receive)
        raise RuntimeError('SECRET transcript text and sk-live-token')
    with pytest.raises(RuntimeError):
        asyncio.run(drive(broken, [b'abc']))
    (line,) = lines(caplog)
    assert line['outcome'] == 'error' and 'SECRET' not in json.dumps(line) and 'sk-live' not in json.dumps(line)


@pytest.mark.parametrize('upload', ['', 'ABCDEF0123456789', '0123456789abcde', '0123456789abcdef0', '0123456789abcdeg', 'x' * 500, '0123456789abcdef\r\nX: y'])
def test_malformed_ids_are_ignored(enabled, caplog, upload):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    try:
        asyncio.run(drive(ok_app, [b'a'], headers=[('X-Audli-Upload-Id', upload), ('CF-Ray', upload), ('X-Vercel-Id', upload)]))
    except Exception:
        pass                                                         # an unusable header value is not our concern here
    for line in lines(caplog):
        assert line['upload'] is None
        assert line['cf_ray'] in (None, upload) and (line['cf_ray'] is None or re.match(r'^[A-Za-z0-9:_.-]{1,64}$', line['cf_ray']))


def test_the_line_carries_no_secrets_addresses_or_user_agents(enabled, caplog):
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    asyncio.run(drive(ok_app, [b'RIFF-audio-bytes-and-a-spoken-transcript'], headers=[
        ('Authorization', 'Bearer secret-token-value'), ('X-Forwarded-For', '203.0.113.7'), ('User-Agent', 'Mozilla/5.0 iPhone'),
        ('X-Audli-Upload-Id', UPLOAD)]))
    raw = json.dumps(lines(caplog))
    for forbidden in ('secret-token-value', 'Bearer', '203.0.113.7', 'Mozilla', 'iPhone', 'RIFF', 'transcript', OWNER):
        assert forbidden not in raw


def app_for(tmp_path, **settings):
    repo = ProgressRepository(tmp_path / 'x.sqlite3', OWNER)
    app = create_app(Settings(data_dir=tmp_path, latency_aggregates=True, _env_file=None, **settings), FakeProvider(), repo)
    return app, repo


def test_through_the_real_app_the_upload_is_unchanged_and_one_line_is_logged(tmp_path, audio_bytes, caplog):
    app, repo = app_for(tmp_path, turn_trace=True)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    client = ASGIClient(app, FakeProvider())
    exercise = client.post('/api/exercises').json()
    response = client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('answer.wav', audio_bytes, 'audio/wav')},
                           headers={'X-Audli-Upload-Id': UPLOAD, 'X-Audli-Turn': TURN})
    assert response.status_code == 200 and response.json()['transcription']['text']
    (line,) = lines(caplog)
    assert line['route'] == 'POST /api/exercises/{id}/attempts' and line['status'] == 200 and line['outcome'] == 'completed'
    assert line['upload'] == UPLOAD and line['received'] >= len(audio_bytes) and line['declared'] == line['received']
    raw = json.dumps(line)
    assert not UUID.search(raw) and 'Team rolled back' not in raw
    repo.close()


def test_through_the_real_app_the_body_guard_still_refuses_an_oversized_upload(tmp_path, caplog):
    app, repo = app_for(tmp_path, turn_trace=True, max_audio_bytes=1000)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    client = ASGIClient(app, FakeProvider())
    exercise = client.post('/api/exercises').json()
    response = client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('answer.wav', b'0' * 200000, 'audio/wav')})
    assert response.status_code == 413
    (line,) = lines(caplog)
    assert line['status'] == 413 and line['outcome'] == 'completed' and line['declared'] > 1000
    repo.close()


def test_with_the_flag_off_the_real_app_logs_nothing(tmp_path, audio_bytes, caplog):
    app, repo = app_for(tmp_path)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    client = ASGIClient(app, FakeProvider())
    exercise = client.post('/api/exercises').json()
    assert client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('answer.wav', audio_bytes, 'audio/wav')}).status_code == 200
    assert lines(caplog) == []
    repo.close()


# ----------------------------------------------------------------------------------------- browser delivery reports
def send_record(**override):
    record = {'id': UPLOAD, 'n': 1, 'kind': 'exercise', 'outcome': 'ok', 'status': 200, 'ms': 4200, 'bytes': 81234, 'mime': 'mp4',
              'rec_ms': 7000, 'since_prev_ms': 15000, 'online': True, 'net': '4g', 'rtt': 100, 'downlink_kbps': 5000,
              'hidden': 0, 'hidden_ms': 0, 'pagehide': False, 'errname': '', 'err': ''}
    record.update(override)
    return record


def report(tmp_path, deliveries, caplog, **settings):
    app, repo = app_for(tmp_path, turn_trace=True, **settings)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    body = {'stages': {'upload_transcription': 4200}, 'turn': TURN, 'deliveries': deliveries}
    response = ASGIClient(app, FakeProvider()).post('/api/telemetry/turn', json=body)
    repo.close()
    return response


def test_a_delivery_report_is_logged_with_the_turn_and_counted_by_outcome(tmp_path, caplog):
    deliveries = [send_record(outcome='network', status=0, ms=12150, errname='TypeError', err='Load failed', hidden=1, hidden_ms=800),
                  send_record(n=2)]
    assert report(tmp_path, deliveries, caplog).status_code == 204
    (line,) = lines(caplog, 'Turn trace ')
    assert line['kind'] == 'client' and line['turn'] == TURN and [d['outcome'] for d in line['deliveries']] == ['network', 'ok']
    assert line['deliveries'][0]['err'] == 'Load failed' and line['deliveries'][0]['ms'] == 12150
    stages = aggregator.snapshot()['stages']
    assert stages['client.upload_send_network']['count'] == 1 and stages['client.upload_send_ok']['count'] == 1


def test_deliveries_are_ignored_by_the_log_unless_tracing_is_on(tmp_path, caplog):
    app, repo = app_for(tmp_path)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    body = {'stages': {'upload_transcription': 4200}, 'turn': TURN, 'deliveries': [send_record()]}
    assert ASGIClient(app, FakeProvider()).post('/api/telemetry/turn', json=body).status_code == 204
    assert lines(caplog, 'Turn trace ') == []
    repo.close()


@pytest.mark.parametrize('override', [
    {'id': 'ABCDEF0123456789'}, {'id': 'short'}, {'n': 0}, {'n': 10}, {'kind': 'other'}, {'outcome': 'unknown'}, {'status': 600},
    {'ms': 300001}, {'bytes': -1}, {'bytes': 17 * 1024 * 1024}, {'mime': 'application/x-evil'}, {'net': '5g'}, {'rtt': 10001},
    {'hidden': 21}, {'err': 'https://evil.example/x?token=abc'}, {'err': 'a' * 61}, {'err': 'Load failed 12345'}, {'errname': 'Type Error'},
    {'errname': 'T' * 41}, {'extra_field': 'learner@example.com'}, {'text': 'I heard an update'},
])
def test_malformed_or_content_bearing_delivery_fields_are_rejected(tmp_path, caplog, override):
    assert report(tmp_path, [send_record(**override)], caplog).status_code == 422
    assert lines(caplog, 'Turn trace ') == []


def test_at_most_eight_deliveries_per_report(tmp_path, caplog):
    assert report(tmp_path, [send_record(n=1) for _ in range(9)], caplog).status_code == 422
    assert report(tmp_path, [send_record(n=1) for _ in range(8)], caplog).status_code == 204
