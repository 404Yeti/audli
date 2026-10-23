"""Reset authorization, preservation, replay and persistence races; no live providers."""
import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.onboarding_reset import reset_onboarding, RESET_FIELDS
from app.main import create_app
from app.models import Transcription, ExerciseContent
from app.storage import schema as s
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory
from test_auth import settings_for


def seed(repo, exercise):
    profile = repo.profile(); profile.name = 'Robin'; profile.onboarding_status = 'complete'
    profile.completed_attempts = 3; profile.onboarding.revision = 12
    repo.save_profile(profile)
    exercise_id = str(uuid4())
    repo.save_exercise(exercise_id, exercise, profile.difficulty, exercise_id + '.wav', audio=b'generated')
    repo.add_attempt(exercise_id, Transcription(text='private words', confidence=.9))
    repo.cache_lesson_audio(repo.learner_id + '-onboarding-cache.wav', b'private cache')
    return exercise_id


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
    assert reset_onboarding(repo, 12)['revision'] == 13
    assert repo.profile() == after
    assert reset_onboarding(repo, 13)['revision'] == 14


def test_reset_abandons_unfinished_exercise(repository_factory, exercise):
    repo = repository_factory(); seed(repo, exercise)
    reset_onboarding(repo, 12)
    assert repo.current_exercise() is None
    assert repo.profile().name == 'Listener'
    assert repo.profile().completed_attempts == 3
    assert not repo.history()


def test_production_reset_is_authenticated_and_per_owned_account(repository_factory, tmp_path):
    repo = repository_factory(None); owner = str(uuid4()); other = str(uuid4())
    def response(request):
        assert request.method == 'GET' and str(request.url).endswith('/auth/v1/user')
        if request.headers['authorization'] not in ('Bearer first', 'Bearer second'):
            return httpx.Response(401, json={'message': 'Invalid bearer'})
        selected = owner if request.headers['authorization'] == 'Bearer first' else other
        return httpx.Response(200, json={'id': selected, 'email_confirmed_at': 'yes'})
    auth = httpx.AsyncClient(transport=httpx.MockTransport(response))
    settings = settings_for(tmp_path, environment='production', persistence='postgres',
        database_url='postgresql://operator:fixture@localhost:55434/disposable?sslmode=require')
    app = create_app(settings, FakeProvider(), repository=repo, auth_client=auth)
    client = ASGIClient(app, FakeProvider())
    try:
        body = {'confirmation': 'RESTART ONBOARDING', 'revision': 0}
        assert client.get('/api/onboarding/reset-controls').status_code == 401
        assert client.post('/api/onboarding/restart', json=body).status_code == 401
        assert client.post('/api/onboarding/restart', json=body, headers={'Authorization': 'Bearer invalid'}).status_code == 401
        for bearer in ('first', 'second'):
            controls = client.get('/api/onboarding/reset-controls', headers={'Authorization': 'Bearer ' + bearer})
            assert controls.status_code == 200 and controls.json()['restart_available'] is True
        before_other = repo.for_learner(other).profile()
        # No body or URL owner selector exists. Attempts to supply another owner fail schema validation.
        assert client.post('/api/onboarding/restart', json={**body, 'user_id': other},
                           headers={'Authorization': 'Bearer first'}).status_code == 422
        assert client.post('/api/onboarding/restart/' + other, json=body,
                           headers={'Authorization': 'Bearer first'}).status_code == 404
        assert repo.for_learner(other).profile() == before_other
        assert client.post('/api/onboarding/restart', json=body, headers={'Authorization': 'Bearer first'}).status_code == 200
        assert repo.for_learner(owner).profile().onboarding.revision == 1
        assert repo.for_learner(other).profile() == before_other
        assert client.post('/api/onboarding/restart', json=body, headers={'Authorization': 'Bearer second'}).status_code == 200
        assert repo.for_learner(owner).profile().onboarding.revision == 1
        assert repo.for_learner(other).profile().onboarding.revision == 1
    finally:
        asyncio.run(auth.aclose())


def test_reset_can_repeat_typed_and_spoken_names_without_stale_answers(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider, repository=repo)
    client = ASGIClient(app, provider)
    def name_question():
        revision = repo.profile().onboarding.revision
        result = client.post('/api/onboarding/restart', json={'confirmation': 'RESTART ONBOARDING', 'revision': revision})
        assert result.status_code == 200
        assert repo.profile().name == 'Listener' and repo.profile().onboarding.welcome == 'introduction'
        state = client.post('/api/onboarding/start', json={'revision': result.json()['revision']}).json()
        assert state['welcome'] == 'introduction' and state['stage'] == 'identity'
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


def test_reset_and_account_erasure_routes_cannot_be_confused(tmp_path):
    owner = str(uuid4())
    auth = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={'id': owner, 'email_confirmed_at': 'yes'})))
    app = create_app(settings_for(tmp_path), FakeProvider(), auth_client=auth)
    client = ASGIClient(app, FakeProvider())
    try:
        routes = {route.path for route in app.routes}
        assert '/api/onboarding/restart' in routes
        assert '/api/account/delete' not in routes             # deletion is registered only when AUDLI_ACCOUNT_DELETION_ENABLED is on
        assert '/api/account/controls' in routes               # the read-only availability report is always present
        assert not any('delete' in path for path in routes)
        body = {'confirmation': 'RESTART ONBOARDING', 'revision': 0}
        assert client.post('/api/onboarding/restart', json=body).status_code == 401
        for payload in ({'confirmation': 'yes', 'revision': 0}, {**body, 'user_id': str(uuid4())},
                        {**body, 'password': 'irrelevant'}, {**body, 'revision': -1}):
            assert client.post('/api/onboarding/restart', json=payload, headers={'Authorization': 'Bearer valid'}).status_code == 422
        assert client.post('/api/account/delete', json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'unused'},
                           headers={'Authorization': 'Bearer valid'}).status_code == 404
        controls = client.get('/api/account/controls', headers={'Authorization': 'Bearer valid'})
        assert controls.status_code == 200 and controls.json().get('delete_available') is False
        assert client.post('/api/account/controls', json={}, headers={'Authorization': 'Bearer valid'}).status_code == 405
        assert app.state.repository.for_learner(owner).profile().onboarding.revision == 0
    finally:
        app.state.repository.close(); asyncio.run(auth.aclose())


def test_reset_failure_rolls_back_and_resume_preserves_new_checkpoint(repository_factory, tmp_path, monkeypatch, exercise):
    repo = repository_factory(); provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider, repository=repo)
    client = ASGIClient(app, provider)
    exercise_id = seed(repo, exercise)
    profile = repo.profile(); profile.name = 'Before'; profile.onboarding.revision = 12
    repo.save_profile(profile); before = repo.profile()
    from contextlib import contextmanager
    original_transaction = repo.transaction
    @contextmanager
    def fail_after_update():
        with original_transaction() as db:
            yield db
            raise RuntimeError('simulated failure after update, before commit')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'transaction', fail_after_update)
        with pytest.raises(RuntimeError):
            reset_onboarding(repo, 12)
    assert repo.profile() == before and repo.exercise(exercise_id) is not None
    assert client.post('/api/onboarding/restart', json={'confirmation': 'RESTART ONBOARDING', 'revision': 12}).status_code == 200
    state = client.post('/api/onboarding/start', json={'revision': 13}).json()
    heard = client.post('/api/onboarding/heard', json={'revision': state['revision']}).json()
    replay = client.get('/api/onboarding').json()
    assert replay['revision'] == heard['revision'] and replay['welcome'] == heard['welcome']
    assert repo.profile().name == 'Listener' and repo.profile().onboarding.revision == heard['revision']


@pytest.mark.parametrize('status', ['active', 'paused'])
def test_reset_abandons_open_lesson_even_without_current_exercise(repository_factory, status):
    from app.lesson import Lesson, now
    repo = repository_factory()
    lesson = Lesson(id=str(uuid4()), started_at=now(), active_since=now() if status == 'active' else None,
                    name='Robin', focus='details', phase='WELCOME', status=status)
    repo.create_lesson(lesson)
    before = repo.profile()
    reset_onboarding(repo, before.onboarding.revision)
    assert repo.lesson() is None and repo.lesson(lesson.id) is None
    assert repo.profile().completed_attempts == before.completed_attempts
    with pytest.raises(ValueError):
        repo.save_lesson(lesson, lesson.revision)


def test_postgres_workers_serialize_reset_and_lesson_saving(repository_factory, tmp_path):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres cross-worker transaction lease')
    owner = repo.learner_id
    settings = settings_for(tmp_path, persistence='postgres', database_url=repo.engine.url.render_as_string(hide_password=False))
    def respond(request):
        return httpx.Response(200, json={'id': owner, 'email_confirmed_at': 'yes'})
    async def run():
        auth_clients = [httpx.AsyncClient(transport=httpx.MockTransport(respond)) for _ in range(2)]
        apps = [create_app(settings, FakeProvider(), repository=repo, auth_client=client) for client in auth_clients]
        clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                                   headers={'Authorization': 'Bearer valid'}) for app in apps]
        try:
            # Concurrent retries acknowledge the same reset without incrementing twice.
            results = await asyncio.gather(*[client.post('/api/onboarding/restart',
                json={'confirmation': 'RESTART ONBOARDING', 'revision': 0}) for client in clients])
            assert sorted(response.status_code for response in results) == [200, 200]
            saving = asyncio.Event(); finish = asyncio.Event()
            @apps[0].post('/api/test-persistence')
            async def persist_lesson():
                from app.lesson import Lesson, now
                saving.set(); await finish.wait()
                repo.create_lesson(Lesson(id=str(uuid4()), started_at=now(), active_since=now(),
                                         name='Saved before reset', focus='details', phase='WELCOME'))
                return {'saved': True}
            save = asyncio.create_task(clients[0].post('/api/test-persistence'))
            await saving.wait()
            reset = asyncio.create_task(clients[1].post('/api/onboarding/restart',
                json={'confirmation': 'RESTART ONBOARDING', 'revision': 1}))
            await asyncio.sleep(.15)
            assert not reset.done() and repo.profile().onboarding.revision == 1
            finish.set(); assert (await save).status_code == 200
            assert (await reset).status_code == 200
            assert repo.lesson() is None and repo.profile().onboarding.revision == 2
            assert (await clients[0].get('/api/profile')).status_code == 200
        finally:
            for client in clients + auth_clients:
                await client.aclose()
    asyncio.run(run())

def test_reset_preserves_all_history_evidence_and_audio(repository_factory, tmp_path, audio_bytes):
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
    from app.lesson import now
    at = now()
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
    tables = tuple(table for table in s.metadata.sorted_tables if table is not s.profiles)
    def snapshot():
        with repo.engine.connect() as db:
            return {table.name: list(db.execute(select(table)).mappings()) for table in tables}
    other = repo.for_learner(str(uuid4()))
    other_profile = other.profile(); other_profile.name = 'Other learner'; other_profile.onboarding.revision = 7
    other.save_profile(other_profile)
    before = snapshot(); profile = repo.profile(); history = repo.history()
    reset_onboarding(repo, profile.onboarding.revision)
    assert snapshot() == before and other.profile() == other_profile
    assert repo.history() == history and repo.profile().completed_attempts == profile.completed_attempts
    assert repo.lesson(completed=True).id == lesson.id
    assert repo.profile().model_dump(exclude=set(RESET_FIELDS)) == profile.model_dump(exclude=set(RESET_FIELDS))




def test_postgres_owner_lease_releases_on_cancel_and_does_not_block_other_accounts(repository_factory, tmp_path):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres cross-worker transaction lease')
    owner = str(uuid4()); other = str(uuid4())
    settings = settings_for(tmp_path, persistence='postgres', database_url=repo.engine.url.render_as_string(hide_password=False))
    def verified(request):
        selected = owner if request.headers['authorization'] == 'Bearer owner' else other
        return httpx.Response(200, json={'id': selected, 'email_confirmed_at': 'yes'})
    async def run():
        auth = httpx.AsyncClient(transport=httpx.MockTransport(verified))
        app = create_app(settings, FakeProvider(), repository=repo, auth_client=auth)
        saving = asyncio.Event(); finish = asyncio.Event()
        @app.post('/api/test-hold-save')
        async def hold():
            saving.set(); await finish.wait()
            return {'saved': True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            held = asyncio.create_task(client.post('/api/test-hold-save', headers={'Authorization': 'Bearer owner'}))
            await saving.wait()
            body = {'confirmation': 'RESTART ONBOARDING', 'revision': 0}
            independent = await asyncio.wait_for(client.post('/api/onboarding/restart', json=body,
                headers={'Authorization': 'Bearer other'}), timeout=2)
            assert independent.status_code == 200
            held.cancel()
            with pytest.raises(asyncio.CancelledError):
                await held
            released = await asyncio.wait_for(client.post('/api/onboarding/restart', json=body,
                headers={'Authorization': 'Bearer owner'}), timeout=2)
            assert released.status_code == 200
        await auth.aclose()
    asyncio.run(run())


def test_reset_invalidates_stale_voice_and_completion_requests(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider, repository=repo)
    client = ASGIClient(app, provider)
    old = repo.profile().onboarding.revision
    assert client.post('/api/onboarding/restart', json={'confirmation': 'RESTART ONBOARDING', 'revision': old}).status_code == 200
    after = repo.profile()
    for path in ('start', 'heard', 'answer', 'complete'):
        body = {'revision': old}
        if path == 'answer':
            body.update(text='Stale response', confirmed=True, hands_free=False)
        response = client.post('/api/onboarding/' + path, json=body)
        assert response.status_code == 409, (path, response.text)
        assert repo.profile() == after
    stale = client.post('/api/onboarding/attempts', data={'revision': old},
                        files={'audio': ('response.wav', audio_bytes, 'audio/wav')})
    assert stale.status_code == 409 and repo.profile() == after


def test_restart_discards_pending_evidence_and_rejects_old_lesson_callbacks(repository_factory, tmp_path, audio_bytes):
    from app.lesson import Lesson, now
    from test_conversation import begin, answer, scripted
    repo = repository_factory(); provider = FakeProvider()
    client = ASGIClient(create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}),
                                  provider, repository=repo), provider)
    scripted(client, [{'inference': 'insufficient_evidence'}])
    exercise = begin(client)
    _, _, pending = answer(client, exercise, audio_bytes)
    assert pending.status_code == 200 and pending.json()['active_followup']
    at = now()
    lesson = Lesson(id=str(uuid4()), started_at=at, active_since=at, name='Robin', focus='details', phase='EXERCISE')
    repo.create_lesson(lesson); repo.attach_lesson_exercise(lesson, exercise['id'])
    other = repo.for_learner(str(uuid4()))
    seed(other, ExerciseContent.model_validate(json.loads(repo.exercise(exercise['id'])['content'])))
    other_before = other.current_exercise()
    before = repo.profile()
    body = {'confirmation': 'RESTART ONBOARDING', 'revision': before.onboarding.revision}
    result = client.post('/api/onboarding/restart', json=body)
    assert result.status_code == 200
    assert client.post('/api/onboarding/restart', json=body).json() == result.json()
    assert repo.lesson() is None and repo.current_exercise() is None
    assert other.current_exercise() == other_before
    assert repo.profile().completed_attempts == before.completed_attempts and repo.history() == []
    for path in ('heard', 'answer', 'advance', 'exercise', 'resume', 'end', 'audio'):
        response = client.post(f'/api/lessons/{lesson.id}/{path}', json={'revision': lesson.revision})
        assert response.status_code == 404, (path, response.text)
    for path in ('conversation', 'audio'):
        assert client.get(f"/api/exercises/{exercise['id']}/{path}").status_code == 404
    assert client.post('/api/exercises').status_code == 409
    assert client.post('/api/lessons/start').status_code == 409
    assert repo.profile().completed_attempts == before.completed_attempts
    assert client.get('/api/profile').json()['destination'] == 'onboarding'


def test_generation_started_before_restart_cannot_save_after_it(repository_factory, tmp_path):
    repo = repository_factory(); provider = FakeProvider()
    profile = repo.profile(); profile.onboarding_status = 'complete'; repo.save_profile(profile)
    async def run():
        entered = asyncio.Event(); release = asyncio.Event()
        original = provider.generate
        async def delayed(profile):
            entered.set(); await release.wait()
            return await original(profile)
        provider.generate = delayed
        app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider, repository=repo)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            generating = asyncio.create_task(client.post('/api/exercises'))
            await entered.wait()
            # Fault injection bypasses the request lease to exercise the final
            # persistence guard independently of API-worker serialization.
            reset_onboarding(repo, 0)
            after = repo.profile()
            release.set()
            assert (await generating).status_code != 200
            assert repo.current_exercise() is None and repo.profile() == after
    asyncio.run(run())


def test_delayed_assessment_after_restart_cannot_award_progress(repository_factory, tmp_path, audio_bytes):
    from test_conversation import begin, assessment
    repo = repository_factory(); provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider, repository=repo)
    client = ASGIClient(app, provider)
    exercise = begin(client)
    uploaded = client.post(f"/api/exercises/{exercise['id']}/attempts",
        files={'audio': ('response.wav', audio_bytes, 'audio/wav')}).json()
    async def run():
        entered = asyncio.Event(); release = asyncio.Event()
        async def delayed(content, turns):
            entered.set(); await release.wait()
            return assessment(content, turns)
        provider.assess = delayed
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            request = asyncio.create_task(client.post(f"/api/attempts/{uploaded['id']}/assess",
                json={'text': uploaded['transcription']['text'], 'confirmed': True}))
            await entered.wait()
            reset_onboarding(repo, repo.profile().onboarding.revision)
            after = repo.profile()
            release.set()
            assert (await request).status_code != 200
            assert repo.profile() == after and not repo.history() and not repo.session_history()
            assert repo.current_exercise() is None
    asyncio.run(run())
