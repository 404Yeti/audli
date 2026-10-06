"""Auth-server plumbing, API ownership and durable account state; no live Supabase."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Conversation, ConversationTurn
from app.storage import schema as s
from conftest import ASGIClient, FakeProvider
from test_conversation import assessment
from test_persistence import repository_factory

USER_A, USER_B = str(uuid4()), str(uuid4())
TOKENS = {'valid-a': USER_A, 'valid-b': USER_B}


def settings_for(path, **kwargs):
    return Settings(data_dir=path, provider='demo', auth_mode='supabase',
        supabase_url='https://project.supabase.co', supabase_publishable_key='sb_publishable_test',
        allowed_origins='https://audli-seven.vercel.app', _env_file=None, **kwargs)


def auth_response(request):
    assert str(request.url) == 'https://project.supabase.co/auth/v1/user'
    assert request.headers['apikey'] == 'sb_publishable_test'
    token = request.headers['authorization'].removeprefix('Bearer ')
    if token in TOKENS:
        return httpx.Response(200, json={'id': TOKENS[token], 'email_confirmed_at': '2026-10-06T00:00:00Z', 'is_anonymous': False})
    if token == 'outage':
        return httpx.Response(503, json={'message': 'sensitive provider detail'})
    if token == 'network':
        raise httpx.ConnectError('sensitive token', request=request)
    if token == 'anonymous':
        return httpx.Response(200, json={'id': USER_A, 'email_confirmed_at': 'yes', 'is_anonymous': True})
    if token == 'unconfirmed':
        return httpx.Response(200, json={'id': USER_A})
    if token == 'malformed-user':
        return httpx.Response(200, json={'id': 'bad', 'email_confirmed_at': 'yes'})
    return httpx.Response(401, json={'message': 'invalid or expired JWT'})


class AccountClient(ASGIClient):
    def __init__(self, app, provider, token):
        super().__init__(app, provider)
        self.token = token

    def request(self, method, path, **kwargs):
        headers = {'Authorization': 'Bearer ' + self.token} | kwargs.pop('headers', {})
        return super().request(method, path, headers=headers, **kwargs)


@pytest.fixture
def accounts(tmp_path, repository_factory):
    provider = FakeProvider()
    async def speak(text, speech_rate=.9):
        return await provider.speech(None)
    provider.speak = speak
    calls = []
    async def assess(content, turns):
        calls.append(turns)
        return assessment(content, turns, {'inference': 'insufficient_evidence'} if len(turns) == 1 else {})
    provider.assess = assess
    client = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path), provider, repository=repository_factory(None), auth_client=client)
    yield AccountClient(app, provider, 'valid-a'), AccountClient(app, provider, 'valid-b'), calls
    app.state.repository.close()
    asyncio.run(client.aclose())


@pytest.mark.parametrize('path', ['/api/profile', '/api/exercises/current', '/api/history',
    '/api/exercises/unknown/audio', '/api/exercises/unknown/conversation', '/api/attempts/unknown'])
def test_unauthenticated_reads_are_rejected(accounts, path):
    a, _, _ = accounts
    response = ASGIClient(a.app, a.provider).get(path)
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('method,path,body', [
    ('PUT', '/api/profile', {'name': 'Intruder', 'goal': 'read data'}),
    ('POST', '/api/exercises', None),
    ('POST', '/api/attempts/unknown/assess', {'text': 'Answer', 'confirmed': True}),
])
def test_unauthenticated_writes_are_rejected(accounts, method, path, body):
    a, _, calls = accounts
    response = ASGIClient(a.app, a.provider).request(method, path, json=body)
    assert response.status_code == 401
    assert calls == []


@pytest.mark.parametrize('header,status', [
    ('Bearer invalid', 401), ('Bearer expired', 401), ('Bearer forged', 401),
    ('Basic valid-a', 401), ('Bearer ', 401), ('Bearer valid-a extra', 401),
    ('Bearer anonymous', 401), ('Bearer unconfirmed', 401), ('Bearer malformed-user', 401),
    ('Bearer outage', 503), ('Bearer network', 503),
])
def test_invalid_expired_and_unavailable_auth_never_initializes_learner(accounts, header, status):
    a, _, _ = accounts
    response = a.get('/api/profile', headers={'Authorization': header})
    assert response.status_code == status
    assert 'sensitive' not in response.text
    with a.app.state.repository.engine.connect() as db:
        assert db.execute(select(s.users)).all() == []


def test_duplicate_authorization_headers_are_rejected(accounts):
    a, _, _ = accounts
    response = ASGIClient(a.app, a.provider).get('/api/profile', headers=[('Authorization', 'Bearer valid-a'), ('Authorization', 'Bearer valid-b')])
    assert response.status_code == 401


def test_only_health_and_auth_mode_are_public(accounts):
    a, _, _ = accounts
    client = ASGIClient(a.app, a.provider)
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/auth/config').json() == {'mode': 'supabase'}
    assert client.get('/api/profile?access_token=valid-a&learner_id=' + USER_A).status_code == 401


def test_authentication_preserves_origin_and_request_body_guard(accounts):
    a, _, _ = accounts
    assert a.put('/api/profile', headers={'Origin': 'https://audli-seven.vercel.app.evil.example'},
        json={'name': 'Alice', 'goal': 'Meetings'}).status_code == 403
    assert a.put('/api/profile', content=b'x' * 40001).status_code == 413
    with a.app.state.repository.engine.connect() as db:
        assert db.execute(select(s.users)).all() == []


def test_verified_identity_initializes_once_and_browser_uuid_is_ignored(accounts):
    a, b, _ = accounts
    assert a.get('/api/profile').json()['profile']['name'] == 'Listener'
    assert a.put('/api/profile', json={'name': 'Alice', 'goal': 'Meetings'}, headers={'X-Learner-Id': USER_B}).status_code == 200
    assert b.get('/api/profile').json()['profile']['name'] == 'Listener'
    assert a.get('/api/profile').json()['profile']['name'] == 'Alice'
    assert a.put('/api/profile', json={'name': 'Attack', 'goal': 'Meetings', 'learner_id': USER_B}).status_code == 422
    with a.app.state.repository.engine.connect() as db:
        assert set(db.execute(select(s.users.c.id)).scalars()) == {USER_A, USER_B}
    assert a.app.state.repository.learner_id is None


def start(client):
    response = client.post('/api/exercises')
    assert response.status_code == 200, response.text
    exercise = response.json()
    assert client.post(f"/api/exercises/{exercise['id']}/conversation/listened").status_code == 200
    return exercise


def respond(client, exercise, audio_bytes, question=None):
    response = client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('answer.wav', audio_bytes, 'audio/wav')})
    assert response.status_code == 200, response.text
    attempt = response.json()
    result = client.post(f"/api/attempts/{attempt['id']}/assess", json={
        'text': attempt['transcription']['text'], 'confirmed': True, 'followup_id': question['id'] if question else None})
    assert result.status_code == 200, result.text
    return attempt, result.json()


def test_cross_user_reads_writes_nested_resources_and_audio(accounts, audio_bytes):
    a, b, calls = accounts
    exercise = start(a)
    attempt, pending = respond(a, exercise, audio_bytes)
    question = pending['active_followup']
    before = a.get('/api/exercises/' + exercise['id'] + '/conversation').json()
    # Populate the shared filesystem cache before trying cross-user playback.
    assert a.get(exercise['audio_url']).status_code == 200
    cue = a.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': question['id']}).json()['audio_url']
    assert a.get(cue).status_code == 200
    for path in [exercise['audio_url'], cue, f"/api/exercises/{exercise['id']}/conversation", f"/api/exercises/{exercise['id']}/transcript", f"/api/attempts/{attempt['id']}"]:
        assert b.get(path).status_code == 404, path
    for suffix in ['conversation/listened', 'conversation/ready', 'coach-audio']:
        assert b.post(f"/api/exercises/{exercise['id']}/{suffix}", json={'cue_id': question['id']}).status_code == 404
    assert b.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('answer.wav', audio_bytes, 'audio/wav')}).status_code == 404
    for suffix in ['assess', 'evaluate']:
        assert b.post(f"/api/attempts/{attempt['id']}/{suffix}", json={'text': 'Guessing some meaning', 'confirmed': True}).status_code == 404
    assert len(calls) == 1
    assert b.get('/api/exercises/current').json() is None
    assert b.get('/api/history').json() == []
    assert a.get('/api/exercises/' + exercise['id'] + '/conversation').json() == before
    assert a.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 403
    second = start(b)
    b_attempt = b.post(f"/api/exercises/{second['id']}/attempts", files={'audio': ('answer.wav', audio_bytes, 'audio/wav')}).json()
    assert b.post(f"/api/attempts/{b_attempt['id']}/assess", json={'text': 'An answer', 'confirmed': True, 'followup_id': question['id']}).status_code == 409
    owner = a.app.state.repository.for_learner(USER_A)
    other = a.app.state.repository.for_learner(USER_B)
    assert other.session_exercises(owner.session_history()[0].id) == []
    assert other.audio_blob(owner.exercise(exercise['id'])['audio_name']) is None
    assert not other.owns_audio(owner.exercise(exercise['id'])['audio_name'])
    with pytest.raises(ValueError, match='another learner'):
        other.save_coach_audio(second['id'], 'summary', owner.exercise(exercise['id'])['audio_name'])
    with pytest.raises(ValueError, match='another learner'):
        with other.transaction() as db:
            other.save_audio(db, owner.exercise(exercise['id'])['audio_name'], b'overwrite')
    forged = Conversation(exercise_id=second['id'], pending_attempt_id=attempt['id'])
    with pytest.raises(ValueError, match='not found'):
        other.save_conversation(forged)
    forged = Conversation(exercise_id=second['id'], turns=[ConversationTurn(attempt_id=attempt['id'], text='Stolen')])
    with pytest.raises(ValueError, match='not found'):
        other.save_conversation(forged)
    forged = Conversation(exercise_id=second['id'], followups=owner.conversation(exercise['id']).followups)
    with pytest.raises(ValueError, match='another exercise'):
        other.save_conversation(forged)
    assert other.conversation(second['id']).turns == []


def test_authenticated_loop_followup_feedback_restart_and_adaptation(accounts, audio_bytes, tmp_path, repository_factory):
    a, b, _ = accounts
    assert a.put('/api/profile', json={'name': 'Alice', 'goal': 'Meetings'}).status_code == 200
    exercise = start(a)
    root, pending = respond(a, exercise, audio_bytes)
    assert pending['result'] is None
    assert a.get('/api/history').json() == []
    _, final = respond(a, exercise, audio_bytes, pending['active_followup'])
    assert final['result']['adaptation']['decision'] == 'harder'
    assert len(a.get('/api/history').json()) == 1
    assert b.get('/api/history').json() == []
    assert b.get(f"/api/attempts/{root['id']}").status_code == 404
    assert a.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 200
    cue = a.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': 'feedback'}).json()['audio_url']
    assert a.get(cue).status_code == 200 and b.get(cue).status_code == 404
    profile = a.get('/api/profile').json()['profile']
    audio = a.get(exercise['audio_url']).content
    history = a.get('/api/history').json()
    a.app.state.repository.close()
    # Same database, fresh application and filesystem cache, same verified UUID.
    repository = repository_factory(None)
    auth_client = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path / 'fresh-cache'), a.provider, repository=repository, auth_client=auth_client)
    try:
        resumed = AccountClient(app, a.provider, 'valid-a')
        other = AccountClient(app, a.provider, 'valid-b')
        assert resumed.get('/api/profile').json()['profile'] == profile
        assert resumed.get('/api/history').json() == history
        assert resumed.get('/api/exercises/' + exercise['id'] + '/conversation').json() == final
        assert resumed.get(exercise['audio_url']).content == audio
        assert resumed.get(cue).status_code == 200
        assert other.get(exercise['audio_url']).status_code == 404
        next_exercise = resumed.post('/api/exercises').json()
        assert next_exercise['difficulty'] == profile['difficulty']
        assert len(repository.for_learner(USER_A).session_history()) == 2
        assert other.get('/api/exercises/current').json() is None
    finally:
        repository.close()
        asyncio.run(auth_client.aclose())


def test_concurrent_authenticated_scopes_do_not_leak(accounts):
    a, b, _ = accounts
    a.put('/api/profile', json={'name': 'Alice', 'goal': 'Meetings'})
    b.put('/api/profile', json={'name': 'Bob', 'goal': 'Travel'})
    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a.app), base_url='http://testserver') as client:
            return await asyncio.gather(*[client.get('/api/profile', headers={'Authorization': 'Bearer ' + token}) for token in ['valid-a', 'valid-b'] * 10])
    results = asyncio.run(requests())
    assert [response.json()['profile']['name'] for response in results] == ['Alice', 'Bob'] * 10
    assert a.app.state.repository.learner_id is None


@pytest.mark.parametrize('kwargs', [
    {'environment': 'production', 'auth_mode': 'local'},
    {'learner_id': USER_A}, {'supabase_url': 'http://project.supabase.co'},
    {'supabase_url': 'https://project.supabase.co/path'}, {'supabase_url': 'https://user@project.supabase.co'},
    {'supabase_publishable_key': ''}, {'supabase_publishable_key': 'sb_secret_forbidden'},
])
def test_unsafe_auth_configuration_fails_closed(tmp_path, kwargs):
    values = dict(auth_mode='supabase', supabase_url='https://project.supabase.co', supabase_publishable_key='sb_publishable_test') | kwargs
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, _env_file=None, **values)


def test_production_uses_supabase_identity_without_shared_learner():
    settings = Settings(environment='production', persistence='postgres', auth_mode='supabase',
        database_url='postgresql://user:password@localhost/database?sslmode=require',
        supabase_url='https://project.supabase.co', supabase_publishable_key='sb_publishable_test', _env_file=None)
    assert settings.learner_id is None


def test_operator_transfer_preserves_ids_history_audio_and_refuses_overwrite(accounts, audio_bytes):
    from scripts.transfer_legacy_learner import transfer_legacy_learner
    a, b, _ = accounts
    exercise = start(a)
    _, pending = respond(a, exercise, audio_bytes)
    respond(a, exercise, audio_bytes, pending['active_followup'])
    root = a.app.state.repository
    owner = root.for_learner(USER_A)
    original_session = owner.session_history()[0]
    history = owner.history()
    current = owner.current_exercise()
    audio = owner.audio_blob(current['audio_name'])
    preview = transfer_legacy_learner(root, USER_A, USER_B)
    assert preview == {'sessions': 1, 'completed_attempts': 1, 'applied': False}
    assert a.get('/api/history').json() == history
    assert transfer_legacy_learner(root, USER_A, USER_B, apply=True)['applied']
    target = root.for_learner(USER_B)
    assert target.history() == history
    assert target.current_exercise()['id'] == exercise['id']
    assert target.session_history()[0].id == original_session.id
    assert target.audio_blob(current['audio_name']) == audio
    assert target.conversation(exercise['id']).turns
    assert a.get(exercise['audio_url']).status_code == 404
    assert b.get(exercise['audio_url']).status_code == 200
    before = b.get('/api/history').json()
    with pytest.raises(ValueError, match='not empty'):
        transfer_legacy_learner(root, USER_A, USER_B, apply=True)
    assert b.get('/api/history').json() == before
