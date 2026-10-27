"""AUD-92: bounded JSON acknowledgment ordering; no admission waiting or transport changes."""
import asyncio
import threading

import httpx
import pytest
from starlette.responses import JSONResponse

from app import lease_admission as admission_module
from app.lease_admission import JSONAcknowledgment, MAX_JSON_ACK_BYTES, CleanupUnconfirmed
from app.main import create_app
from conftest import FakeProvider
from test_auth import settings_for, auth_response
from test_auth_lease_exemption import HEADERS, client_for
from test_lease_admission import harness, Connection, actual_auth
from test_persistence import repository_factory  # noqa: F401


def test_small_json_is_held_until_explicit_release_without_copying_body():
    async def run():
        messages = []
        async def send(message): messages.append(message)
        gate = JSONAcknowledgment(send)
        start = {'type': 'http.response.start', 'status': 200,
                 'headers': [(b'Content-Type', b'Application/JSON; charset=utf-8'), (b'Content-Length', b'2')]}
        body = {'type': 'http.response.body', 'body': b'{}'}
        await gate(start); await gate(body)
        assert not messages and gate.body is not body and gate.body['body'] is body['body']
        await gate.release_to_client()
        assert messages == [start, body]
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['audio', 'stream', 'oversized', 'undeclared', 'error', 'trailers', 'pathsend'])
def test_unsupported_responses_keep_existing_streaming_and_lease_lifetime(kind):
    async def run():
        auth, request, _ = harness()
        sent = []; proceed = asyncio.Event(); started = asyncio.Event()
        size = MAX_JSON_ACK_BYTES + 1 if kind == 'oversized' else 2
        headers = [(b'content-type', b'audio/wav' if kind == 'audio' else b'application/json')]
        if kind != 'undeclared': headers.append((b'content-length', str(size).encode()))
        start = {'type': 'http.response.start', 'status': 503 if kind == 'error' else 200, 'headers': headers}
        if kind == 'trailers': start['trailers'] = True
        first = ({'type': 'http.response.pathsend', 'path': '/synthetic.json'} if kind == 'pathsend' else
                 {'type': 'http.response.body', 'body': b'x' * size, 'more_body': kind == 'stream'})
        async def app(scope, receive, send):
            await send(start); await send(first); started.set()
            await proceed.wait()
            if kind == 'stream': await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        auth.app = app
        async def send(message):
            assert auth.admission.in_use == 1
            sent.append(message)
        async def receive(): return {'type': 'http.disconnect'}
        task = asyncio.create_task(auth({'type': 'http', 'method': 'POST', 'path': '/api/test', 'headers': []}, receive, send))
        await started.wait()
        assert sent == [start, first] and auth.admission.in_use == 1
        proceed.set(); await task
        assert auth.admission.in_use == 0
    asyncio.run(asyncio.wait_for(run(), 5))


def test_json_waits_for_post_response_work_and_closes_only_once():
    async def run():
        auth, _, _ = harness()
        complete = False; closed = 0; messages = []
        def close():
            nonlocal closed
            assert complete
            closed += 1
        auth._take_lease = lambda key, permit=None: (Connection(close), True)
        async def app(scope, receive, send):
            nonlocal complete
            await JSONResponse({'ok': True})(scope, receive, send)
            assert not messages and auth.admission.in_use == 1
            await asyncio.sleep(0)
            complete = True
        auth.app = app
        async def send(message):
            assert complete and closed == 1 and auth.admission.in_use == 0
            messages.append(message)
        async def receive(): return {'type': 'http.disconnect'}
        await auth({'type': 'http', 'method': 'POST', 'path': '/api/test', 'headers': []}, receive, send)
        assert len(messages) == 2 and closed == 1
    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize('failure', ['close', 'handler', 'incomplete'])
def test_failure_never_flushes_buffered_success(failure):
    async def run():
        auth, _, _ = harness(); messages = []
        def close():
            if failure == 'close': raise RuntimeError('synthetic close failure')
        auth._take_lease = lambda key, permit=None: (Connection(close), True)
        async def app(scope, receive, send):
            if failure == 'incomplete':
                await send({'type': 'http.response.start', 'status': 200,
                            'headers': [(b'content-type', b'application/json'), (b'content-length', b'2')]})
            else:
                await JSONResponse({'ok': True})(scope, receive, send)
                if failure == 'handler': raise RuntimeError('synthetic post-response failure')
        auth.app = app
        async def send(message): messages.append(message)
        async def receive(): return {'type': 'http.disconnect'}
        with pytest.raises(RuntimeError):
            await auth({'type': 'http', 'method': 'POST', 'path': '/api/test', 'headers': []}, receive, send)
        assert not messages
        assert auth.admission.in_use == auth.admission.quarantined == int(failure == 'close')
    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize('cancelled', [False, True])
@pytest.mark.parametrize('offload', [False, True])
def test_stuck_cleanup_has_bounded_response_wait_and_keeps_quarantine(monkeypatch, cancelled, offload):
    now = [0.0]
    monkeypatch.setattr(admission_module, 'CLEANUP_TIMEOUT_SECONDS', 2.0 if cancelled else .02)
    if cancelled:
        monkeypatch.setattr(admission_module, '_cleanup_time', lambda: now[0])
    async def run():
        auth, _, _ = harness(offload=offload)
        entered, finish = threading.Event(), threading.Event(); messages = []
        def close(): entered.set(); assert finish.wait(5)
        auth._take_lease = lambda key, permit=None: (Connection(close), True)
        async def send(message): messages.append(message)
        async def receive(): return {'type': 'http.disconnect'}
        task = asyncio.create_task(auth({'type': 'http', 'method': 'POST', 'path': '/api/test', 'headers': []}, receive, send))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            if cancelled:
                task.cancel(); await asyncio.sleep(0)
                now[0] = 3.0  # deterministic deadline expiry; repeated cancel must not reset it
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancelled else CleanupUnconfirmed):
                await asyncio.wait_for(task, 2)
            assert not messages and auth.admission.in_use == auth.admission.quarantined == 1
            assert len(auth.admission.cleanup_tasks) == 1
        finally:
            finish.set()
            await asyncio.gather(*list(auth.admission.cleanup_tasks), return_exceptions=True)
            await asyncio.sleep(0)
        assert not auth.admission.cleanup_tasks
        assert auth.admission.in_use == 1  # late successful close cannot silently reset quarantine
    asyncio.run(asyncio.wait_for(run(), 5))


def test_cancelled_acquisition_returns_promptly_and_late_connection_is_closed(monkeypatch):
    monkeypatch.setattr(admission_module, 'CLEANUP_TIMEOUT_SECONDS', .02)
    async def run():
        auth, request, _ = harness()
        entered, finish = threading.Event(), threading.Event(); connection = Connection()
        def take(key, permit=None): entered.set(); assert finish.wait(5); return connection, True
        auth._take_lease = take
        task = asyncio.create_task(request())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 2)
            assert auth.admission.in_use == auth.admission.quarantined == 1 and not connection.closed
        finally:
            finish.set()
            await asyncio.gather(*list(auth.admission.cleanup_tasks), return_exceptions=True)
            await asyncio.sleep(0)
        assert connection.closed and not auth.admission.cleanup_tasks
        assert auth.admission.in_use == 1
    asyncio.run(asyncio.wait_for(run(), 5))


def test_production_app_returns_503_on_cleanup_deadline_and_health_stays_available(tmp_path, repository_factory, monkeypatch):
    monkeypatch.setattr(admission_module, 'CLEANUP_TIMEOUT_SECONDS', .02)
    async def run():
        repo = repository_factory(None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            app = create_app(settings_for(tmp_path, lease_admission=True, db_offload=True), FakeProvider(), repo, auth_client=auth_http)
            auth = actual_auth(app); auth.lease_engine = object()
            finish = threading.Event()
            def close(): assert finish.wait(5)
            auth._take_lease = lambda key, permit=None: (Connection(close), True)
            @app.post('/api/json-probe')
            async def probe(): return {'ok': True}
            try:
                async with client_for(app) as client:
                    response = await asyncio.wait_for(client.post('/api/json-probe', headers=HEADERS), 2)
                    assert response.status_code == 503 and response.json().get('ok') is None
                    assert (await client.get('/api/health')).json()['lease_admission']['quarantined'] == 1
                    assert (await client.post('/api/json-probe', headers=HEADERS)).status_code == 503
            finally:
                finish.set()
                await asyncio.gather(*list(auth.admission.cleanup_tasks), return_exceptions=True)
    asyncio.run(asyncio.wait_for(run(), 5))


def test_real_disconnect_during_cleanup_replays_canonical_attempt_without_second_stt(tmp_path, repository_factory, audio_bytes):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql': pytest.skip('Real advisory leases require PostgreSQL')
    import socket
    import uvicorn
    from test_upload_idempotency import attempts
    async def run():
        provider = FakeProvider(); calls = 0
        original_transcribe = provider.transcribe
        async def transcribe(audio, filename):
            nonlocal calls
            calls += 1
            return await original_transcribe(audio, filename)
        provider.transcribe = transcribe
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            app = create_app(settings_for(tmp_path, lease_admission=True, db_offload=True,
                                         upload_idempotency=True, upload_retry=True), provider, repo, auth_client=auth_http)
            auth = actual_auth(app)
            sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_config=None, log_level=None))
            server_task = asyncio.create_task(server.serve(sockets=[sock]))
            closing, finish, closed = threading.Event(), threading.Event(), threading.Event()
            try:
                while not server.started: await asyncio.sleep(.001)
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}') as client:
                    exercise = (await client.post('/api/exercises', headers=HEADERS)).json()['id']
                    assert (await client.post(f'/api/exercises/{exercise}/conversation/listened', headers=HEADERS)).status_code == 200
                    original_take = auth._take_lease; hold = True
                    class DelayedClose:
                        def __init__(self, connection): self.connection = connection
                        def close(self):
                            closing.set(); assert finish.wait(5)
                            self.connection.close(); closed.set()
                    def take(key, permit=None):
                        nonlocal hold
                        connection, acquired = original_take(key, permit)
                        if hold and acquired:
                            hold = False
                            return DelayedClose(connection), acquired
                        return connection, acquired
                    auth._take_lease = take
                    headers = {**HEADERS, 'X-Audli-Upload-Id': '0123456789abcdef'}
                    url = f'/api/exercises/{exercise}/attempts'
                    def upload(): return client.post(url, headers=headers, files={'audio': ('synthetic.wav', audio_bytes, 'audio/wav')})
                    active = asyncio.create_task(upload())
                    assert await asyncio.to_thread(closing.wait, 2)
                    assert not active.done() and calls == attempts(repo) == 1
                    active.cancel()
                    with pytest.raises(asyncio.CancelledError): await active
                    assert (await upload()).status_code == 503 and calls == 1
                    finish.set(); assert await asyncio.to_thread(closed.wait, 2)
                    while auth.admission.in_use: await asyncio.sleep(0)
                    replay = await upload()
                    assert replay.status_code == 200 and replay.headers['x-audli-replayed'] == 'true'
                    assert calls == attempts(repo) == 1 and auth.admission.quarantined == 0
                    assert not auth.admission.cleanup_tasks
            finally:
                finish.set(); server.should_exit = True; await server_task; sock.close()
    asyncio.run(asyncio.wait_for(run(), 20))


def test_ordering_changes_no_queries_checkouts_connections_or_rollbacks(tmp_path, repository_factory, monkeypatch):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql': pytest.skip('Real lease counts require PostgreSQL')
    from sqlalchemy import event
    from app import auth as auth_module
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            app = create_app(settings_for(tmp_path, lease_admission=True, db_offload=True), FakeProvider(), repo, auth_client=auth_http)
            auth = actual_auth(app)
            counts = {'queries': 0, 'checkouts': 0, 'connections': 0, 'rollbacks': 0}
            def count(name):
                def update(*args): counts[name] += 1
                return update
            for engine in [repo.engine, auth.lease_engine, app.state.accent_controls.engine]:
                for event_name, key in [('before_cursor_execute', 'queries'), ('checkout', 'checkouts'),
                                        ('connect', 'connections'), ('rollback', 'rollbacks')]:
                    event.listen(engine, event_name, count(key))
            class PassThrough:
                def __init__(self, send): self.send = send
                async def __call__(self, message): await self.send(message)
                async def release_to_client(self): pass
            async with client_for(app) as client:
                exercise = (await client.post('/api/exercises', headers=HEADERS)).json()['id']
                url = f'/api/exercises/{exercise}/conversation/listened'
                assert (await client.post(url, headers=HEADERS)).status_code == 200
                results = []
                for response_class in [PassThrough, JSONAcknowledgment]:
                    monkeypatch.setattr(auth_module, 'JSONAcknowledgment', response_class)
                    for key in counts: counts[key] = 0
                    for _ in range(3): assert (await client.post(url, headers=HEADERS)).status_code == 200
                    results.append(dict(counts))
                assert results[0] == results[1]
                assert results[1]['connections'] == 3  # one existing NullPool lease per request
                assert auth.admission.in_use == 0
    asyncio.run(asyncio.wait_for(run(), 20))


@pytest.mark.parametrize('rollback', [False, True])
def test_cancelled_transaction_finishes_before_lease_or_permit_release(tmp_path, repository_factory, rollback):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql': pytest.skip('Real transactions/advisory leases require PostgreSQL')
    from sqlalchemy import text
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            app = create_app(settings_for(tmp_path, lease_admission=True, db_offload=True), FakeProvider(), repo, auth_client=auth_http)
            auth = actual_auth(app)
            with repo.engine.begin() as db: db.execute(text('CREATE TABLE json_cancel_probe (owner text PRIMARY KEY)'))
            entered, finish = threading.Event(), threading.Event()
            @app.post('/api/json-cancel-probe')
            async def probe():
                owner = str(auth.request_repository.learner_id)
                def write():
                    with repo.engine.begin() as db:
                        db.execute(text('INSERT INTO json_cancel_probe VALUES (:owner)'), {'owner': owner})
                        entered.set(); assert finish.wait(5)
                        if rollback: raise ValueError('synthetic transaction rollback')
                await auth.offload.run(write)
                return {'ok': True}
            async with client_for(app) as client:
                task = asyncio.create_task(client.post('/api/json-cancel-probe', headers=HEADERS))
                try:
                    assert await asyncio.to_thread(entered.wait, 2)
                    task.cancel(); await asyncio.sleep(0)
                    assert auth.admission.in_use == 1
                    assert (await client.post('/api/json-cancel-probe', headers=HEADERS)).status_code == 503
                finally:
                    finish.set()
                with pytest.raises(asyncio.CancelledError): await task
                assert auth.admission.in_use == auth.admission.quarantined == 0
                with repo.engine.connect() as db:
                    assert db.execute(text('SELECT count(*) FROM json_cancel_probe')).scalar_one() == int(not rollback)
    asyncio.run(asyncio.wait_for(run(), 15))


def test_cancelled_json_close_can_finish_without_quarantine_or_success_headers():
    async def run():
        auth, _, _ = harness(); messages = []
        entered, finish = threading.Event(), threading.Event()
        def close(): entered.set(); assert finish.wait(5)
        auth._take_lease = lambda key, permit=None: (Connection(close), True)
        async def send(message): messages.append(message)
        async def receive(): return {'type': 'http.disconnect'}
        task = asyncio.create_task(auth({'type': 'http', 'method': 'POST', 'path': '/api/test', 'headers': []}, receive, send))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel(); await asyncio.sleep(0)
            assert not messages and auth.admission.in_use == 1
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert not messages and auth.admission.in_use == auth.admission.quarantined == 0
        assert not auth.admission.cleanup_tasks
    asyncio.run(asyncio.wait_for(run(), 5))



def test_buffered_message_envelopes_cannot_be_mutated_after_send_returns():
    async def run():
        messages = []
        async def send(message): messages.append(message)
        gate = JSONAcknowledgment(send)
        headers = [(b'content-type', b'application/json'), (b'content-length', b'2')]
        start = {'type': 'http.response.start', 'status': 200, 'headers': headers}
        body = {'type': 'http.response.body', 'body': b'{}'}
        await gate(start); await gate(body)
        start['status'] = 201; headers.append((b'x-late', b'mutation'))
        body['body'] = b'{"changed":true}'
        await gate.release_to_client()
        assert messages[0]['status'] == 200 and len(messages[0]['headers']) == 2
        assert messages[1]['body'] == b'{}'
    asyncio.run(run())
