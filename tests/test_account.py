"""Account ownership, reset tokens, transactional erasure and Auth races; no live providers."""
import asyncio
import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.account import erase_application_data, reset_onboarding, delete_account, reauthenticate, require_live_session, RESET_FIELDS
from app.auth import SupabaseIdentity
from app.main import create_app
from app.models import LearnerProfile, Transcription
from app.storage import schema as s
from app.storage.repository import utcnow
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory
from test_auth import settings_for


def auth_schema(engine, owners):
    if engine.dialect.name != 'postgresql':
        return
    with engine.begin() as db:
        db.execute(text('CREATE SCHEMA IF NOT EXISTS auth; CREATE SCHEMA IF NOT EXISTS storage'))
        db.execute(text('CREATE TABLE IF NOT EXISTS auth.users(id uuid PRIMARY KEY)'))
        db.execute(text('CREATE TABLE IF NOT EXISTS auth.sessions(id uuid PRIMARY KEY, user_id uuid REFERENCES auth.users ON DELETE CASCADE)'))
        db.execute(text('CREATE TABLE IF NOT EXISTS auth.refresh_tokens(id uuid PRIMARY KEY, user_id text, session_id uuid REFERENCES auth.sessions ON DELETE CASCADE)'))
        for table in ('flow_state', 'scim_users'):
            db.execute(text(f'CREATE TABLE IF NOT EXISTS auth.{table}(id uuid PRIMARY KEY, user_id uuid)'))
        db.execute(text('CREATE TABLE IF NOT EXISTS auth.identities(id uuid PRIMARY KEY, user_id uuid REFERENCES auth.users ON DELETE CASCADE)'))
        db.execute(text('CREATE TABLE IF NOT EXISTS storage.buckets(id text PRIMARY KEY)'))
        for owner in owners:
            db.execute(text('INSERT INTO auth.users VALUES (:owner) ON CONFLICT DO NOTHING'), {'owner': owner})


def seed(repo, exercise):
    profile = repo.profile(); profile.name = 'Robin'; profile.onboarding_status = 'complete'
    profile.completed_attempts = 3; profile.onboarding.revision = 12
    repo.save_profile(profile)
    exercise_id = str(uuid4())
    repo.save_exercise(exercise_id, exercise, profile.difficulty, exercise_id + '.wav', audio=b'generated')
    repo.add_attempt(exercise_id, Transcription(text='private words', confidence=.9))
    repo.cache_lesson_audio(repo.learner_id + '-onboarding-cache.wav', b'private cache')
    return exercise_id


def test_complete_application_erasure_is_scoped_and_transactional(repository_factory, exercise):
    a = repository_factory(); b = repository_factory(str(uuid4()))
    seed(a, exercise); seed(b, exercise)
    with pytest.raises(RuntimeError):
        with a.transaction() as db:
            erase_application_data(db, a.learner_id)
            raise RuntimeError('simulated rollback')
    assert a.profile().name == 'Robin'
    with a.transaction() as db:
        erase_application_data(db, a.learner_id)
    with a.engine.connect() as db:
        for table in (s.users, s.profiles, s.sessions, s.exercises, s.attempts, s.audio_assets):
            assert len(db.execute(select(table)).all()) > 0  # Other owner remains.
        assert db.execute(select(s.users).where(s.users.c.id == a.learner_id)).first() is None
    assert b.profile().name == 'Robin'


def test_reset_preserves_learning_and_rejects_stale_tokens(repository_factory):
    repo = repository_factory()
    profile = repo.profile(); profile.name = 'Robin'; profile.onboarding_status = 'complete'
    profile.onboarding.revision = 12; profile.onboarding.typed_name = True
    profile.listening_profile.overall = .84; profile.completed_attempts = 5
    repo.save_profile(profile)
    before = repo.profile()
    assert reset_onboarding(repo, 12)['revision'] == 13
    after = repo.profile()
    assert after.name == 'Listener' and after.onboarding_status == 'not_started'
    assert after.onboarding.welcome == 'introduction' and not after.onboarding.typed_name
    assert after.difficulty == before.difficulty and after.listening_profile == before.listening_profile
    assert after.completed_attempts == 5
    assert after.model_dump(exclude=set(RESET_FIELDS)) == before.model_dump(exclude=set(RESET_FIELDS))
    with pytest.raises(HTTPException) as error:
        reset_onboarding(repo, 12)
    assert error.value.status_code == 409
    assert reset_onboarding(repo, 13)['revision'] == 14


def test_reset_rejects_unfinished_exercise(repository_factory, exercise):
    repo = repository_factory(); seed(repo, exercise)
    with pytest.raises(HTTPException) as error:
        reset_onboarding(repo, 12)
    assert error.value.status_code == 409
    assert repo.profile().name == 'Robin'


def test_local_delete_and_schema_rejection(tmp_path):
    provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider)
    client = ASGIClient(app, provider)
    try:
        assert client.post('/api/account/delete', json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'test'}).status_code == 403
        assert client.post('/api/account/restart-onboarding', json={'confirmation': 'yes', 'revision': 0}).status_code == 422
        assert client.post('/api/account/restart-onboarding', json={'confirmation': 'RESTART ONBOARDING', 'revision': 0, 'user_id': str(uuid4())}).status_code == 422
    finally:
        app.state.repository.close()


def test_production_reset_requires_operator_allowlist(tmp_path):
    provider = FakeProvider()
    settings = settings_for(tmp_path).model_copy(update={'auth_mode': 'local', 'environment': 'production'})
    app = create_app(settings, provider); client = ASGIClient(app, provider)
    try:
        assert client.get('/api/account/controls').json()['restart_available'] is False
        assert client.post('/api/account/restart-onboarding', json={'confirmation': 'RESTART ONBOARDING', 'revision': 0}).status_code == 403
    finally:
        app.state.repository.close()


@pytest.mark.parametrize('status,matched,expected', [(200, True, None), (200, False, 403), (400, True, 403), (503, True, 503)])
def test_password_reauthentication_is_bound_to_owner(tmp_path, status, matched, expected):
    from pydantic import SecretStr
    from starlette.requests import Request
    owner = str(uuid4()); calls = []
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/user'):
            return httpx.Response(200, json={'id': owner, 'email': 'test@example.com'})
        if request.url.path.endswith('/logout'):
            return httpx.Response(204)
        return httpx.Response(status, json={'access_token': 'temporary', 'user': {'id': owner if matched else str(uuid4())}})
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        identity = SupabaseIdentity(settings_for(tmp_path), client)
        request = Request({'type': 'http', 'headers': [(b'authorization', b'Bearer original')]})
        try:
            if expected:
                with pytest.raises(HTTPException) as error:
                    await reauthenticate(identity, request, SecretStr('private password'), owner)
                assert error.value.status_code == expected
            else:
                await reauthenticate(identity, request, SecretStr('private password'), owner)
        finally:
            await client.aclose()
    asyncio.run(run())
    assert not any('private password' in str(request.url) for request in calls)
    if status == 200:
        assert calls[-1].url.path.endswith('/logout')


def test_postgres_deletion_rolls_back_partial_failure_and_removes_auth(repository_factory, exercise, tmp_path):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres Auth transaction integration')
    other = str(uuid4()); auth_schema(repo.engine, [repo.learner_id, other])
    seed(repo, exercise)
    with repo.engine.begin() as db:
        for owner in (repo.learner_id, other):
            for table in ('sessions', 'identities', 'flow_state', 'scim_users'):
                db.execute(text(f'INSERT INTO auth.{table} VALUES (:id, :owner)'), {'id': str(uuid4()), 'owner': owner})
        db.execute(text('CREATE TABLE auth.blocker(user_id uuid REFERENCES auth.users)'))
        db.execute(text('INSERT INTO auth.blocker VALUES (:owner)'), {'owner': repo.learner_id})
    cache = tmp_path / 'cache'; cache.mkdir()
    (cache / (repo.learner_id + '-onboarding-cache.wav')).write_bytes(b'cache')
    with pytest.raises(Exception):
        delete_account(repo, cache)
    assert repo.profile().name == 'Robin'
    assert repo.audio_blob(repo.learner_id + '-onboarding-cache.wav') == b'private cache'
    with repo.engine.begin() as db:
        db.execute(text('DELETE FROM auth.blocker'))
        db.execute(text("INSERT INTO storage.buckets VALUES ('new-unreviewed-storage')"))
    with pytest.raises(HTTPException):
        delete_account(repo, cache)
    assert repo.profile().name == 'Robin'
    with repo.engine.begin() as db:
        db.execute(text('DELETE FROM storage.buckets'))
    assert delete_account(repo, cache) == {'deleted': True}
    with repo.engine.connect() as db:
        for table in ('users', 'sessions', 'identities', 'flow_state', 'scim_users'):
            assert db.execute(text(f'SELECT count(*) FROM auth.{table} WHERE ' + ('id' if table == 'users' else 'user_id') + ' = :owner'), {'owner': repo.learner_id}).scalar_one() == 0
            assert db.execute(text(f'SELECT count(*) FROM auth.{table}')).scalar_one() == 1
    assert not list(cache.iterdir())


def test_postgres_two_workers_serialize_reset_and_reject_deleted_tokens(repository_factory, tmp_path):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres cross-worker integration')
    owner = repo.learner_id
    auth_schema(repo.engine, [owner])
    session_id = str(uuid4())
    with repo.engine.begin() as db:
        db.execute(text('INSERT INTO auth.sessions VALUES (:id, :owner)'), {'id': session_id, 'owner': owner})
    payload = base64.urlsafe_b64encode(json.dumps({'session_id': session_id}).encode()).decode().rstrip('=')
    settings = settings_for(tmp_path, persistence='postgres', database_url=repo.engine.url.render_as_string(hide_password=False))
    def respond(request):
        if request.url.path.endswith('/logout'):
            return httpx.Response(204)
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'temporary', 'user': {'id': owner}})
        # Deliberately keep returning a valid user, even after deletion, to model
        # a request verified just before commit and unexpired JWT replay.
        return httpx.Response(200, json={'id': owner, 'email': 'test@example.com', 'email_confirmed_at': 'yes'})
    async def run():
        auth_clients = [httpx.AsyncClient(transport=httpx.MockTransport(respond)) for _ in range(2)]
        apps = [create_app(settings, FakeProvider(), repository=repo, auth_client=client) for client in auth_clients]
        headers = {'Authorization': 'Bearer header.' + payload + '.verified-by-auth-fixture'}
        clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver', headers=headers) for app in apps]
        try:
            responses = await asyncio.gather(*[client.post('/api/account/restart-onboarding',
                json={'confirmation': 'RESTART ONBOARDING', 'revision': 0}) for client in clients])
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert repo.profile().onboarding.revision == 1
            saving = asyncio.Event(); finish = asyncio.Event()
            @apps[0].post('/api/test-save')
            async def save():
                saving.set(); await finish.wait()
                profile = repo.profile(); profile.name = 'Saved before deletion'; repo.save_profile(profile)
                return {'saved': True}
            task = asyncio.create_task(clients[0].post('/api/test-save'))
            await saving.wait()
            deletion = asyncio.create_task(clients[1].post('/api/account/delete',
                json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'private'}))
            await asyncio.sleep(.15)
            assert not deletion.done() and repo.profile().name == 'Listener'
            finish.set(); assert (await task).status_code == 200
            response = await deletion
            assert response.status_code == 200 and response.json()['deleted'] is True
            for client in clients:
                assert (await client.get('/api/profile')).status_code == 401
                assert (await client.post('/api/account/delete', json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'private'})).status_code == 401
            with repo.engine.connect() as db:
                assert db.execute(select(s.users).where(s.users.c.id == owner)).first() is None
        finally:
            for client in clients + auth_clients:
                await client.aclose()
    asyncio.run(run())


def test_all_evidence_lesson_and_audio_children_are_erased(repository_factory, tmp_path, audio_bytes):
    from datetime import timedelta
    from hashlib import sha256
    from app.lesson import Lesson
    from test_conversation import begin, answer, scripted
    repo = repository_factory()
    provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider, repository=repo)
    client = ASGIClient(app, provider)
    async def speak(message, speech_rate=.9):
        return await provider.speech(None)
    provider.speak = speak
    scripted(client, [{'inference': 'insufficient_evidence'}, {}])
    exercise = begin(client)
    _, _, first = answer(client, exercise, audio_bytes)
    _, _, final = answer(client, exercise, audio_bytes, first.json()['active_followup'])
    assert final.status_code == 200 and final.json()['result'] is not None
    client.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': 'feedback'})
    at = utcnow()
    lesson = Lesson(id=str(uuid4()), started_at=at, active_since=at, name='Robin', focus='details', phase='EXERCISE')
    repo.create_lesson(lesson); repo.attach_lesson_exercise(lesson, exercise['id'])
    lesson.status = 'completed'; lesson.phase = 'COMPLETED'; lesson.exercises_completed = 1
    lesson.completed_at = at + timedelta(seconds=1); lesson.active_since = None
    repo.save_lesson(lesson, lesson.revision)
    scope = sha256(f'{repo.learner_id}:{lesson.id}:0'.encode()).hexdigest()
    with repo.transaction() as db:
        db.execute(s.accent_reservations.insert().values(scope=scope, token=str(uuid4()), budget_day=at.date().isoformat(),
            characters=40, flag_revision=0, state='success', created_at=at, lease_until=at))
        db.execute(s.accent_control.update().values(characters=40))
    tables = (s.users, s.profiles, s.sessions, s.exercises, s.attempts, s.evaluations, s.events,
              s.conversations, s.assessments, s.followups, s.turns, s.evidence, s.coach_audio,
              s.audio_assets, s.lessons, s.lesson_exercises, s.accent_reservations)
    with repo.engine.connect() as db:
        assert all(db.execute(select(table)).first() is not None for table in tables)
    history = repo.history(); before = repo.profile()
    reset_onboarding(repo, before.onboarding.revision)
    assert repo.history() == history and repo.profile().completed_attempts == before.completed_attempts
    assert repo.lesson(completed=True).id == lesson.id
    with repo.transaction() as db:
        erase_application_data(db, repo.learner_id)
    with repo.engine.connect() as db:
        assert all(db.execute(select(table)).first() is None for table in tables)
        assert db.execute(select(s.accent_control.c.characters)).scalar_one() == 40


def test_reset_authorization_is_per_authenticated_account(repository_factory, tmp_path):
    from uuid import UUID
    repo = repository_factory(None); owner = str(uuid4()); other = str(uuid4())
    auth_schema(repo.engine, [owner, other])
    def response(request):
        selected = owner if request.headers['authorization'] == 'Bearer allowed' else other
        return httpx.Response(200, json={'id': selected, 'email_confirmed_at': 'yes'})
    auth = httpx.AsyncClient(transport=httpx.MockTransport(response))
    settings = settings_for(tmp_path).model_copy(update={'environment': 'production', 'onboarding_test_accounts': [UUID(owner)]})
    app = create_app(settings, FakeProvider(), repository=repo, auth_client=auth)
    client = ASGIClient(app, FakeProvider())
    try:
        assert client.get('/api/account/controls', headers={'Authorization': 'Bearer allowed'}).json()['restart_available'] is True
        assert client.get('/api/account/controls', headers={'Authorization': 'Bearer ordinary'}).json()['restart_available'] is False
        body = {'confirmation': 'RESTART ONBOARDING', 'revision': 0}
        assert client.post('/api/account/restart-onboarding', json=body, headers={'Authorization': 'Bearer ordinary'}).status_code == 403
        assert client.post('/api/account/restart-onboarding', json=body, headers={'Authorization': 'Bearer allowed'}).status_code == 200
        assert repo.for_learner(other).profile().onboarding.revision == 0
    finally:
        asyncio.run(auth.aclose())


def test_reset_can_repeat_typed_and_spoken_names_without_stale_answers(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider, repository=repo)
    client = ASGIClient(app, provider)
    def name_question():
        revision = repo.profile().onboarding.revision
        result = client.post('/api/account/restart-onboarding', json={'confirmation': 'RESTART ONBOARDING', 'revision': revision})
        assert result.status_code == 200
        state = client.post('/api/onboarding/start', json={'revision': result.json()['revision']}).json()
        while state['welcome']:
            state = client.post('/api/onboarding/heard', json={'revision': state['revision']}).json()
        assert state['name_entry_available'] is True
        return state
    typed = name_question()
    saved = client.post('/api/onboarding/name', json={'name': 'Typed Robin', 'question_revision': typed['name_question_revision']})
    assert saved.status_code == 200 and repo.profile().name == 'Typed Robin'
    spoken = name_question()
    assert client.post('/api/onboarding/name', json={'name': 'Stale Name', 'question_revision': typed['name_question_revision']}).status_code == 409
    upload = client.post('/api/onboarding/attempts', data={'revision': spoken['revision']}, files={'audio': ('response.wav', audio_bytes, 'audio/wav')})
    assert upload.status_code == 200
    state = upload.json()
    answered = client.post('/api/onboarding/answer', json={'revision': state['revision'], 'text': 'Spoken Robin, I want to learn English', 'confirmed': True, 'hands_free': False})
    assert answered.status_code == 200 and repo.profile().name == 'Spoken Robin'
    assert not repo.profile().onboarding.typed_name
    name_question()


def test_deletion_validation_never_echoes_passwords(tmp_path):
    owner = str(uuid4())
    auth = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'id': owner, 'email_confirmed_at': 'yes'})))
    app = create_app(settings_for(tmp_path), FakeProvider(), auth_client=auth)
    client = ASGIClient(app, FakeProvider())
    try:
        response = client.post('/api/account/delete', headers={'Authorization': 'Bearer valid'},
            json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'secret-value-' * 100})
        assert response.status_code == 422 and 'secret-value' not in response.text
    finally:
        app.state.repository.close(); asyncio.run(auth.aclose())


@pytest.mark.parametrize('factor_stage', ['original', 'password'])
def test_mfa_deletion_refused_without_lowering_assurance(tmp_path, factor_stage):
    from pydantic import SecretStr
    from starlette.requests import Request
    owner = str(uuid4()); calls = []
    def respond(request):
        calls.append(request.url.path)
        user = {'id': owner, 'email': 'test@example.com'}
        if (request.url.path.endswith('/user') and factor_stage == 'original') or (
                request.url.path.endswith('/token') and factor_stage == 'password'):
            user['factors'] = [{'status': 'verified', 'factor_type': 'totp'}]
        if request.url.path.endswith('/user'):
            return httpx.Response(200, json=user)
        if request.url.path.endswith('/logout'):
            return httpx.Response(204)
        return httpx.Response(200, json={'access_token': 'temporary', 'user': user})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            request = Request({'type': 'http', 'headers': [(b'authorization', b'Bearer original')]})
            with pytest.raises(HTTPException) as error:
                await reauthenticate(SupabaseIdentity(settings_for(tmp_path), client), request, SecretStr('password'), owner)
            assert error.value.status_code == 403
            assert 'robbie@audli.ai' in error.value.detail and 'Keep MFA enabled' in error.value.detail
    asyncio.run(run())
    if factor_stage == 'original':
        assert calls == ['/auth/v1/user']
    else:
        assert calls[-1].endswith('/logout')


def test_malformed_reauthentication_token_is_refused_safely(tmp_path):
    from pydantic import SecretStr
    from starlette.requests import Request
    owner = str(uuid4())
    def respond(request):
        if request.url.path.endswith('/user'):
            return httpx.Response(200, json={'id': owner, 'email': 'test@example.com'})
        assert not request.url.path.endswith('/logout')
        return httpx.Response(200, json={'access_token': 123, 'user': {'id': owner}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(HTTPException) as error:
                await reauthenticate(SupabaseIdentity(settings_for(tmp_path), client),
                    Request({'type': 'http', 'headers': [(b'authorization', b'Bearer original')]}), SecretStr('password'), owner)
            assert error.value.status_code == 403
    asyncio.run(run())


def test_deletion_requires_live_owned_session(repository_factory):
    from starlette.requests import Request
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres session ownership check')
    other = str(uuid4()); auth_schema(repo.engine, [repo.learner_id, other])
    own_session, foreign_session, revoked_session = (str(uuid4()) for _ in range(3))
    with repo.engine.begin() as db:
        for session, owner in ((own_session, repo.learner_id), (foreign_session, other)):
            db.execute(text('INSERT INTO auth.sessions VALUES (:id, :owner)'), {'id': session, 'owner': owner})
    for session in (own_session, foreign_session, revoked_session, None):
        payload = base64.urlsafe_b64encode(json.dumps({'session_id': session}).encode()).decode().rstrip('=')
        request = Request({'type': 'http', 'headers': [(b'authorization', ('Bearer header.' + payload + '.verified').encode())]})
        if session == own_session:
            require_live_session(repo, request)
        else:
            with pytest.raises(HTTPException) as error:
                require_live_session(repo, request)
            assert error.value.status_code == 401


def test_deletion_refuses_foreign_audio_reference_and_file_failure(repository_factory, exercise, tmp_path, monkeypatch):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres deletion cache integration')
    other = repository_factory(str(uuid4()))
    auth_schema(repo.engine, [repo.learner_id, other.learner_id])
    own_id = seed(repo, exercise); other_id = seed(other, exercise)
    own_name = own_id + '.wav'
    cache = tmp_path / 'cache'; cache.mkdir(); (cache / own_name).write_bytes(b'cache')
    with repo.engine.begin() as db:
        db.execute(s.exercises.update().where(s.exercises.c.id == other_id).values(audio_name=own_name))
    with pytest.raises(HTTPException) as error:
        delete_account(repo, cache)
    assert error.value.status_code == 503 and (cache / own_name).read_bytes() == b'cache'
    assert repo.profile().name == other.profile().name == 'Robin'
    with repo.engine.begin() as db:
        db.execute(s.exercises.update().where(s.exercises.c.id == other_id).values(audio_name=other_id + '.wav'))
    def refuse_unlink(path, *args, **kwargs):
        raise PermissionError('simulated unreadable cache')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', refuse_unlink)
        with pytest.raises(PermissionError):
            delete_account(repo, cache)
    assert repo.profile().name == other.profile().name == 'Robin'
    with repo.engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM auth.users')).scalar_one() == 2
