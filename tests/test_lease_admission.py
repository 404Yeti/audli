"""AUD-92: admission protects every lease contender until its last cleanup finishes."""
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.auth import LearnerAuthentication, RequestRepository
from app.config import Settings
from app.lease_admission import LeaseAdmission
from app.offload import Offload


class Connection:
    def __init__(self, close=None):
        self.closed = False
        self.callback = close

    def close(self):
        if self.callback:
            self.callback()
        self.closed = True


class Identity:
    async def verify(self, headers):
        return dict(headers).get(b'authorization', b'owner').decode()


def harness(handler=None, enabled=True, limit=1, offload=True):
    repo = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='sqlite')),
                           for_learner=lambda owner: SimpleNamespace(learner_id=owner))
    scoped = RequestRepository()

    async def app(scope, receive, send):
        if handler:
            await handler(scope, scoped)
        await JSONResponse({'ok': True})(scope, receive, send)

    auth = LearnerAuthentication(app, Identity(), repo, scoped, offload=Offload(offload),
                                 admission=enabled, admission_limit=limit)
    auth.lease_engine = object()  # enable PostgreSQL middleware path; no actual DB/provider
    connections = []

    def take(key, permit=None):
        connection = Connection()
        connections.append(connection)
        return connection, True
    auth._take_lease = take

    async def request(owner=b'owner', method='POST', path='/api/test'):
        messages = []
        async def send(message):
            messages.append(message)
        async def receive():
            raise AssertionError('Admission must not consume a rejected recording')
        await auth({'type': 'http', 'method': method, 'path': path,
                    'headers': [(b'authorization', owner)]}, receive, send)
        return messages[0]['status'], messages
    return auth, request, connections


def test_defaults_bounds_and_disabled_behavior(monkeypatch):
    assert Settings.model_fields['lease_admission'].default is False
    assert Settings.model_fields['lease_admission_limit'].default == 1
    for value in [0, 17, True, 1.5, "1.0", "true", "-1"]:
        with pytest.raises(ValidationError):
            Settings(lease_admission_limit=value, _env_file=None)
    monkeypatch.setenv("AUDLI_LEASE_ADMISSION_LIMIT", "1")
    assert Settings(_env_file=None).lease_admission_limit == 1
    admission = LeaseAdmission(False, 1)
    assert admission.try_acquire() is None and admission.in_use == 0


def test_storm_rejected_before_connections_or_body_and_read_routes_still_work():
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        owners = []
        async def handler(scope, repo):
            owners.append(repo.learner_id)
            if scope['method'] == 'POST' and scope['path'] == '/api/test':
                entered.set()
                await finish.wait()
        auth, request, connections = harness(handler)
        holder = asyncio.create_task(request())
        await entered.wait()
        rejected = await asyncio.gather(*(request(str(i).encode()) for i in range(100)))
        assert all(status == 503 for status, _ in rejected)
        assert all((b'retry-after', b'1') in messages[0]['headers'] for _, messages in rejected)
        assert len(connections) == 1 and owners == ['owner']
        assert (await request(b'other', method='GET'))[0] == 200
        assert (await request(b'other', path='/api/telemetry/turn'))[0] == 200
        finish.set()
        assert (await holder)[0] == 200
        assert auth.admission.snapshot() == dict(enabled=True, limit=1, in_use=0, quarantined=0, rejected=100)
        assert all(c.closed for c in connections)
        assert (await request(b'next'))[0] == 200  # no starvation after release
    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize('enabled,limit,expected', [(False, 1, 4), (True, 2, 2)])
def test_disabled_legacy_and_configured_limit(enabled, limit, expected):
    async def run():
        finish = asyncio.Event()
        async def handler(scope, repo):
            await finish.wait()
        auth, request, connections = harness(handler, enabled, limit)
        tasks = [asyncio.create_task(request(str(i).encode())) for i in range(4)]
        while len(connections) < expected:
            await asyncio.sleep(0)
        finish.set()
        responses = await asyncio.gather(*tasks)
        assert sum(status == 200 for status, _ in responses) == expected
        assert len(connections) == expected
        assert auth.admission.in_use == 0
    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize('phase', ['acquire', 'holder', 'contender'])
def test_repeated_cancellation_keeps_permit_through_cleanup(phase):
    async def run():
        gate, closing = threading.Event(), threading.Event()
        entered = asyncio.Event()
        auth, request, connections = harness()
        def close():
            closing.set()
            assert gate.wait(5)
        def take(key, permit=None):
            connection = Connection(close)
            connections.append(connection)
            if phase == 'acquire':
                closing.set()
                assert gate.wait(5)
            return connection, phase != 'contender'
        auth._take_lease = take
        async def handler(scope, repo):
            entered.set()
            await asyncio.Event().wait()
        handler_app = auth.app
        if phase == 'holder':
            async def held(scope, receive, send):
                await handler(scope, None)
            auth.app = held
        task = asyncio.create_task(request())
        try:
            if phase == 'holder':
                await entered.wait()
                task.cancel()
            await asyncio.to_thread(closing.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert (await request(b'other'))[0] == 503
            assert auth.admission.in_use == 1 and not task.done()
        finally:
            gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert connections[0].closed and auth.admission.in_use == 0
        # No permit orphaned after acquisition/holder/failed-candidate cleanup.
        auth._take_lease = lambda key, permit=None: (Connection(), True)
        auth.app = handler_app
        assert (await request())[0] == 200
    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize('contended', [False, True])
def test_cleanup_failure_quarantines_capacity(contended, caplog):
    async def run():
        auth, request, connections = harness()
        def fail():
            raise RuntimeError('private connection detail')
        auth._take_lease = lambda key, permit=None: (Connection(fail), not contended)
        with pytest.raises(RuntimeError):
            await request()
        assert auth.admission.in_use == auth.admission.quarantined == 1
        assert (await request())[0] == 503
        assert 'private connection detail' not in caplog.text
    asyncio.run(asyncio.wait_for(run(), 10))


def test_two_workers_have_independent_caps_not_a_global_cap():
    async def run():
        finish = asyncio.Event()
        async def handler(scope, repo):
            await finish.wait()
        workers = [harness(handler) for _ in range(2)]
        holders = [asyncio.create_task(request()) for _, request, _ in workers]
        while not all(connections for _, _, connections in workers):
            await asyncio.sleep(0)
        for auth, request, connections in workers:
            assert (await request(b'third'))[0] == 503
            assert auth.admission.in_use == len(connections) == 1
        finish.set()
        assert [r[0] for r in await asyncio.gather(*holders)] == [200, 200]
    asyncio.run(asyncio.wait_for(run(), 10))

# Real PostgreSQL: HTTP rejection, account isolation, stable IDs and persisted state.
from app.main import create_app
from app.models import Transcription
from conftest import FakeProvider
from test_auth import settings_for, auth_response, USER_A
from test_auth_lease_exemption import client_for, HEADERS, hold_lease
from test_persistence import repository_factory  # noqa: F401
from test_upload_idempotency import attempts


def actual_auth(app):
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    node = app.middleware_stack
    while not isinstance(node, LearnerAuthentication):
        node = node.app
    return node


def test_postgres_rejection_replay_and_ownership(tmp_path, repository_factory, audio_bytes):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Real account leases require PostgreSQL')

    class Provider(FakeProvider):
        transcriptions = 0
        async def transcribe(self, audio, filename):
            self.transcriptions += 1
            entered.set()
            await finish.wait()
            return Transcription(text='The team rolled back the update.', confidence=.99)

    async def run():
        nonlocal entered, finish
        entered, finish = asyncio.Event(), asyncio.Event()
        provider = Provider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            app = create_app(settings_for(tmp_path, lease_admission=True, db_offload=True,
                                         upload_idempotency=True, upload_retry=True), provider, repo, auth_client=auth_http)
            auth = actual_auth(app)
            async with client_for(app) as client:
                exercise = (await client.post('/api/exercises', headers=HEADERS)).json()['id']
                await client.post(f'/api/exercises/{exercise}/conversation/listened', headers=HEADERS)
                url = f'/api/exercises/{exercise}/attempts'
                headers = {**HEADERS, 'X-Audli-Upload-Id': '0123456789abcdef'}
                async def upload(extra=headers):
                    return await client.post(url, headers=extra, files={'audio': ('response.wav', audio_bytes, 'audio/wav')})
                first = asyncio.create_task(upload())
                await entered.wait()
                rejected = await asyncio.gather(*(upload() for _ in range(20)))
                assert all(r.status_code == 503 for r in rejected)
                assert attempts(repo) == 0 and provider.transcriptions == 1 and provider.evaluations == 0
                finish.set()
                accepted = await first
                assert accepted.status_code == 200
                replay = await upload()
                assert replay.status_code == 200 and replay.headers['x-audli-replayed'] == 'true'
                assert replay.json()['id'] == accepted.json()['id']
                assert attempts(repo) == provider.transcriptions == 1 and provider.evaluations == 0
                assert repo.for_learner(USER_A).conversation(exercise).pending_attempt_id == accepted.json()['id']
                other = await upload({**headers, 'Authorization': 'Bearer valid-b'})
                assert other.status_code == 404 and attempts(repo) == 1 and provider.transcriptions == 1
                # The same guard also covers assessment/progression; a replay is read-back.
                from test_conversation import assessment
                assessing, assessed = asyncio.Event(), asyncio.Event()
                calls = 0
                async def assess(exercise, turns):
                    nonlocal calls
                    calls += 1
                    assessing.set()
                    await assessed.wait()
                    return assessment(exercise, turns)
                provider.assess = assess
                assess_url = f"/api/attempts/{accepted.json()['id']}/assess"
                body = {'text': accepted.json()['transcription']['text'], 'confirmed': True, 'followup_id': None}
                active = asyncio.create_task(client.post(assess_url, json=body, headers=HEADERS))
                await assessing.wait()
                refused = await client.post(assess_url, json=body, headers=HEADERS)
                assert refused.status_code == 503 and calls == 1
                assessed.set()
                result = await active
                assert result.status_code == 200
                again = await client.post(assess_url, json=body, headers=HEADERS)
                assert again.json() == result.json() and calls == 1
                assert len((await client.get('/api/history', headers=HEADERS)).json()) == 1
                health = (await client.get('/api/health')).json()['lease_admission']
                assert health == dict(enabled=True, limit=1, in_use=0, quarantined=0, rejected=21)
                assert auth.admission.in_use == 0
    entered = finish = None
    asyncio.run(asyncio.wait_for(run(), 20))


def test_external_lock_contender_consumes_permit_and_cancellation_releases_it(tmp_path, repository_factory, audio_bytes):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Real account leases require PostgreSQL')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            provider = FakeProvider()
            async def speak(text, speech_rate=.9):
                return audio_bytes
            provider.speak = speak
            app = create_app(settings_for(tmp_path, lease_admission=True, db_offload=True), provider, repo, auth_client=auth_http)
            auth = actual_auth(app)
            blocker, transaction = hold_lease(repo)
            contended = threading.Event()
            original = auth._take_lease
            count = 0
            def observed(key, permit=None):
                nonlocal count
                count += 1
                result = original(key, permit)
                if not result[1]:
                    contended.set()
                return result
            auth._take_lease = observed
            async with client_for(app) as client:
                task = asyncio.create_task(client.post('/api/recognition/retry-audio', headers=HEADERS))
                try:
                    assert await asyncio.to_thread(contended.wait, 5)
                    rejected = await client.post('/api/recognition/retry-audio', headers=HEADERS)
                    assert rejected.status_code == 503
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert auth.admission.in_use == 0
                finally:
                    transaction.rollback()
                    blocker.close()
                assert (await client.post('/api/recognition/retry-audio', headers=HEADERS)).status_code == 200
                assert auth.admission.in_use == 0
    asyncio.run(asyncio.wait_for(run(), 20))


def test_transaction_failure_rolls_back_before_permit_reuse(tmp_path, repository_factory):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Real account leases require PostgreSQL')
    from sqlalchemy import text
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            app = create_app(settings_for(tmp_path, lease_admission=True, db_offload=True), FakeProvider(), repo, auth_client=auth_http)
            with repo.engine.begin() as db:
                db.execute(text('CREATE TABLE admission_probe (owner text PRIMARY KEY)'))
            auth = actual_auth(app)
            fail = True
            @app.post('/api/admission-probe')
            async def probe():
                owner = str(auth.request_repository.learner_id)
                def write():
                    with repo.engine.begin() as db:
                        db.execute(text('INSERT INTO admission_probe VALUES (:owner)'), {'owner': owner})
                        if fail:
                            raise ValueError('synthetic transaction failure')
                await auth.offload.run(write)
                return {'ok': True}
            async with client_for(app) as client:
                assert (await client.post('/api/admission-probe', headers=HEADERS)).status_code == 503
                assert auth.admission.in_use == 0
                with repo.engine.connect() as db:
                    assert db.execute(text('SELECT count(*) FROM admission_probe')).scalar_one() == 0
                fail = False
                assert (await client.post('/api/admission-probe', headers=HEADERS)).status_code == 200
                with repo.engine.connect() as db:
                    assert db.execute(text('SELECT owner FROM admission_probe')).scalar_one() == USER_A
                assert auth.admission.in_use == 0
    asyncio.run(asyncio.wait_for(run(), 20))


def test_complete_lesson_persisted_state_matches_disabled(tmp_path, make_repo, audio_bytes, monkeypatch):
    from conftest import ASGIClient
    from lesson_flow import run_lesson
    from test_state_only_ready import deterministic_ids, dump, normalise
    class AuthClient(ASGIClient):
        def request(self, method, path, **kwargs):
            kwargs['headers'] = {**HEADERS, **kwargs.get('headers', {})}
            return super().request(method, path, **kwargs)
    results = []
    for enabled in (False, True):
        deterministic_ids(monkeypatch)
        repo = make_repo(str(enabled))
        profile = repo.for_learner(USER_A).profile()
        profile.name, profile.onboarding_status = 'Maya', 'complete'
        repo.for_learner(USER_A).save_profile(profile)
        provider = FakeProvider()
        async def speak(text, speech_rate=.9):
            return audio_bytes
        provider.speak = speak
        auth_http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
        app = create_app(settings_for(tmp_path / str(enabled), lease_admission=enabled, db_offload=True,
                                     upload_idempotency=True, upload_retry=True), provider, repo, auth_client=auth_http)
        client = AuthClient(app, provider)
        ready = run_lesson(client, audio_bytes)
        results.append((normalise(ready), dump(repo)))
        assert client.get('/api/health').json()['lease_admission']['in_use'] == 0
        asyncio.run(auth_http.aclose())
    assert results[0] == results[1]


from test_state_only_ready import make_repo  # noqa: F401


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_acquisition_query_failure_only_recycles_confirmed_cleanup(cleanup_fails):
    async def run():
        auth, request, _ = harness()
        def close():
            if cleanup_fails:
                raise RuntimeError('synthetic close failure')
        class Broken(Connection):
            def execute(self, *args):
                raise ValueError('synthetic lock query failure')
        connection = Broken(close)
        auth.lease_engine = SimpleNamespace(connect=lambda: connection)
        auth._take_lease = LearnerAuthentication._take_lease.__get__(auth)
        with pytest.raises((ValueError, RuntimeError)):
            await request()
        assert auth.admission.in_use == auth.admission.quarantined == int(cleanup_fails)
        assert connection.closed is not cleanup_fails
    asyncio.run(asyncio.wait_for(run(), 10))


def test_http_response_can_precede_cleanup_and_next_send_is_safely_rejected():
    """A real socket catches the response/cleanup race hidden by ASGITransport."""
    import socket
    import uvicorn
    async def run():
        auth, _, connections = harness()
        closing, finish = threading.Event(), threading.Event()
        def close():
            closing.set()
            assert finish.wait(5)
        auth._take_lease = lambda key, permit=None: (Connection(close), True)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(auth, lifespan='off', log_config=None, log_level=None))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            while not server.started:
                await asyncio.sleep(.001)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}') as client:
                assert (await client.post('/api/test')).status_code == 200
                assert await asyncio.to_thread(closing.wait, 5)
                assert (await client.post('/api/test')).status_code == 503
                assert auth.admission.in_use == 1
                finish.set()
                # Wait for confirmed cleanup rather than a timing assumption.
                while auth.admission.in_use:
                    await asyncio.sleep(0)
                auth._take_lease = lambda key, permit=None: (Connection(), True)
                assert (await client.post('/api/test')).status_code == 200
        finally:
            finish.set()
            server.should_exit = True
            await server_task
            sock.close()
    asyncio.run(asyncio.wait_for(run(), 15))


def test_real_advisory_lock_across_two_admitted_workers(tmp_path, repository_factory, audio_bytes):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Real account leases require PostgreSQL')
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        contended = threading.Event()
        provider = FakeProvider()
        calls = 0
        original_transcribe = provider.transcribe
        async def transcribe(audio, filename):
            nonlocal calls
            calls += 1
            entered.set()
            await finish.wait()
            return await original_transcribe(audio, filename)
        provider.transcribe = transcribe
        async with httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) as auth_http:
            apps = [create_app(settings_for(tmp_path / str(i), lease_admission=True, db_offload=True,
                                            upload_idempotency=True), provider, repository_factory(None), auth_client=auth_http)
                    for i in range(2)]
            guards = [actual_auth(app) for app in apps]
            original = guards[1]._take_lease
            def observed(key, permit=None):
                result = original(key, permit)
                if not result[1]:
                    contended.set()
                return result
            guards[1]._take_lease = observed
            async with client_for(apps[0]) as a, client_for(apps[1]) as b:
                exercise = (await a.post('/api/exercises', headers=HEADERS)).json()['id']
                await a.post(f'/api/exercises/{exercise}/conversation/listened', headers=HEADERS)
                async def upload(client):
                    return await client.post(f'/api/exercises/{exercise}/attempts', headers={**HEADERS, 'X-Audli-Upload-Id': '0123456789abcdef'},
                                             files={'audio': ('response.wav', audio_bytes, 'audio/wav')})
                first = asyncio.create_task(upload(a))
                await entered.wait()
                second = asyncio.create_task(upload(b))
                try:
                    assert await asyncio.to_thread(contended.wait, 5)
                    assert all(g.admission.in_use == 1 for g in guards)
                    assert (await upload(a)).status_code == (await upload(b)).status_code == 503
                finally:
                    finish.set()
                one, two = await asyncio.gather(first, second)
                assert one.status_code == two.status_code == 200
                assert one.json()['id'] == two.json()['id'] and two.headers['x-audli-replayed'] == 'true'
                assert calls == attempts(repo) == 1
                assert all(g.admission.in_use == 0 for g in guards)
    asyncio.run(asyncio.wait_for(run(), 20))



def test_cancelled_acquisition_without_cleanup_confirmation_does_not_return_capacity():
    async def run():
        auth, request, _ = harness()
        class InterruptedOffload:
            async def run(self, *args, **kwargs):
                # An underlying to_thread task cancelled during shutdown cannot
                # confirm whether its still-running thread acquired a connection.
                raise asyncio.CancelledError
        auth.offload = InterruptedOffload()
        with pytest.raises(asyncio.CancelledError):
            await request()
        assert auth.admission.in_use == auth.admission.quarantined == 1
        assert (await request())[0] == 503
    asyncio.run(asyncio.wait_for(run(), 10))
