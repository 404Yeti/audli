"""Account deletion (AUD-34): complete, ordered, isolated and inert unless enabled. No live providers or Auth server.

Learner-owned rows are counted with an independent SQL oracle (not the code under test). Every learner-owned table must be
seeded for both learners, so adding a table without extending this test fails here as well as in the inventory test.
"""
import asyncio
import base64
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.account import DeleteAccount, delete_account, erase_application_data, erase_plan, reauthenticate, require_live_session
from app.auth import SupabaseIdentity
from app.config import Settings
from app.lesson import Lesson
from app.main import create_app
from app.models import ConversationTurn, Followup, Transcription
from app.storage import schema as s
from app.storage import improvement as store
from app.storage.repository import utcnow, AccountUnavailable
from conftest import ASGIClient, FakeProvider
from app.conversation import final_evaluation, adapt_final
from app.models import Conversation
from test_conversation import assessment
from test_persistence import repository_factory  # noqa: F401
from test_auth import settings_for
from test_schema_ownership_inventory import LEARNER_OWNED

IN_EXERCISES = 'exercise_id IN (SELECT id FROM exercises WHERE user_id = :o)'
IN_ATTEMPTS = 'attempt_id IN (SELECT a.id FROM attempts a JOIN exercises e ON e.id = a.exercise_id WHERE e.user_id = :o)'
ORACLE = {
    'users': 'id = :o',
    **{name: 'user_id = :o' for name in ('learner_profiles', 'training_sessions', 'exercises', 'audio_assets', 'lesson_lifecycles',
        'lesson_exercises', 'learner_roadmaps', 'homework_assignments', 'homework_reflections', 'lesson_entitlements', 'review_improvements')},
    'attempts': IN_EXERCISES,
    **{name: IN_ATTEMPTS for name in ('evaluation_results', 'adaptation_events')},
    **{name: IN_EXERCISES for name in ('conversations', 'turn_assessments', 'conversation_followups', 'conversation_turns', 'assessment_evidence', 'coach_audio')},
}


def auth_schema(engine, owners):
    """A representative Supabase Auth/Storage surface for the tables deletion touches (PostgreSQL only)."""
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


def pg(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL Auth transaction integration')


def prepare(repo, exercise):
    """prepare_completion with a per-exercise audio name (audio names are globally unique across learners)."""
    exercise_id = str(uuid4())
    repo.save_exercise(exercise_id, exercise, repo.profile().difficulty, exercise_id + '.mp3', audio=b'generated-audio')
    attempt_id = repo.add_attempt(exercise_id, Transcription(text='The team restored the previous version.'))
    turn = ConversationTurn(attempt_id=attempt_id, text='The team restored the previous version.')
    evidence = assessment(exercise, [turn])
    conversation = Conversation(exercise_id=exercise_id, turns=[turn], assessment=evidence, state='GIVING_FEEDBACK')
    evaluation = final_evaluation(exercise, evidence)
    profile, event = adapt_final(repo.profile(), evaluation, Settings(_env_file=None))
    return attempt_id, evaluation, profile, event, conversation


def seed_everything(repo, exercise, shared_content, optional=True):
    """One realistic row (usually several) in every learner-owned table for this learner; returns the main exercise id."""
    attempt_id, evaluation, profile, event, conversation = prepare(repo, exercise)
    exercise_id = conversation.exercise_id
    followup = Followup(id=str(uuid4()), dimension='details', index=0, question='When does it start?')
    second = repo.add_attempt(exercise_id, Transcription(text='It starts on Friday.'))
    conversation.followups = [followup]
    conversation.turns = [conversation.turns[0], ConversationTurn(attempt_id=second, text='It starts on Friday.', followup=followup)]
    profile.name = 'Robin'
    repo.complete(attempt_id, 'The team restored the previous version.', evaluation, event, profile, conversation)
    repo.save_coach_audio(exercise_id, 'feedback-cue', f'coach-{exercise_id}.mp3', audio=b'coach audio')
    at = utcnow()
    lesson = Lesson(id=str(uuid4()), started_at=at, active_since=at, name='Robin', focus='details', phase='EXERCISE')
    repo.create_lesson(lesson)
    repo.attach_lesson_exercise(lesson, exercise_id)
    lesson.status, lesson.phase, lesson.exercises_completed = 'completed', 'COMPLETED', 1
    lesson.completed_at, lesson.active_since = at + timedelta(seconds=1), None
    repo.save_lesson(lesson, lesson.revision)
    if optional:
        with repo.transaction() as db:
            db.execute(s.roadmaps.insert().values(user_id=repo.learner_id, revision=0, data={'plan': 'private roadmap'}))
            db.execute(s.lesson_entitlements.insert().values(user_id=repo.learner_id, last_started_at=at, next_eligible_at=at + timedelta(hours=24),
                last_lesson_id=lesson.id, first_speech_failures=0, grace_actions=0, updated_at=at))
            assignment = str(uuid4())
            db.execute(s.homework.insert().values(id=assignment, user_id=repo.learner_id, lesson_id=lesson.id, content_id=shared_content,
                status='assigned', objective='private objective', attempts=0, created_at=at, updated_at=at))
            db.execute(s.reflections.insert().values(id=str(uuid4()), user_id=repo.learner_id, assignment_id=assignment, created_at=at,
                data={'text': 'private reflection'}, grounding='unverified_self_report'))
            db.execute(s.review_improvements.insert().values(id=str(uuid4()), user_id=repo.learner_id, exercise_id=exercise_id, attempt_id=attempt_id,
                status='ready', source_hash='h', improved_text='private improved text', notes=[], prompt_version='v1', claimed_at=at))
    return exercise_id


def shared_content(repo):
    content_id = str(uuid4())
    with repo.transaction() as db:
        db.execute(s.listening_content.insert().values(id=content_id, provider='test', provider_id=content_id[:8], data={'title': 'shared'}, validated_at=utcnow()))
    return content_id


def counts(repo, owner, names=None):
    with repo.engine.connect() as db:
        return {name: db.execute(text(f'SELECT count(*) FROM {name} WHERE {ORACLE[name]}'), {'o': owner}).scalar_one()
                for name in (names or sorted(LEARNER_OWNED)) if inspect_has(db, name)}


def totals(repo, names=None):
    with repo.engine.connect() as db:
        return {name: db.execute(text(f'SELECT count(*) FROM {name}')).scalar_one() for name in (names or sorted(LEARNER_OWNED)) if inspect_has(db, name)}


def inspect_has(db, name):
    from sqlalchemy import inspect
    return inspect(db).has_table(name)


def two_learners(repository_factory, exercise):
    a, b = repository_factory(), repository_factory(str(uuid4()))
    content = shared_content(a)
    seed_everything(a, exercise, content)
    seed_everything(b, exercise, content)
    return a, b, content


# ------------------------------------------------------------------------------------------ complete, isolated erasure
def test_every_learner_owned_table_is_seeded_for_both_learners(repository_factory, exercise):
    a, b, _ = two_learners(repository_factory, exercise)
    for repo in (a, b):
        assert all(count > 0 for count in counts(repo, repo.learner_id).values()), counts(repo, repo.learner_id)
    assert set(counts(a, a.learner_id)) == LEARNER_OWNED


def test_erasing_one_learner_removes_every_row_of_theirs_and_none_of_the_others(repository_factory, exercise):
    a, b, content = two_learners(repository_factory, exercise)
    before_b, before_totals = counts(b, b.learner_id), totals(a)
    before_a = counts(a, a.learner_id)
    with a.transaction() as db:
        erase_application_data(db, a.learner_id)
    assert all(count == 0 for count in counts(a, a.learner_id).values())          # complete removal
    assert counts(b, b.learner_id) == before_b                                    # ownership isolation
    after = totals(a)
    assert after == {name: before_totals[name] - before_a[name] for name in before_totals}, 'orphans or over-deletion'
    with a.engine.connect() as db:                                                 # shared and aggregate rows are not learner data
        assert db.execute(select(s.listening_content.c.id)).scalar_one() == content
    assert b.profile().name == 'Robin' and b.lesson(completed=True) is not None


def test_a_failure_part_way_rolls_back_every_deletion(repository_factory, exercise):
    a, b, _ = two_learners(repository_factory, exercise)
    before = counts(a, a.learner_id)
    with pytest.raises(RuntimeError):
        with a.transaction() as db:
            erase_application_data(db, a.learner_id)
            raise RuntimeError('simulated failure after the last statement')
    assert counts(a, a.learner_id) == before


def test_erasure_tolerates_tables_whose_migration_was_never_applied(repository_factory, exercise):
    """Schema 3-6 databases have no review_improvements (007), roadmaps (004), homework (005) or entitlements (006)."""
    repo = repository_factory()
    seed_everything(repo, exercise, None, optional=False)
    with repo.engine.begin() as db:
        for table in (s.review_improvements, s.lesson_entitlements, *reversed(s.HOMEWORK_TABLES), s.roadmaps):
            table.drop(db, checkfirst=True)
    with repo.transaction() as db:
        erase_application_data(db, repo.learner_id)
    assert all(count == 0 for count in counts(repo, repo.learner_id).values())


# ------------------------------------------------------------------------------------------ ordering and constraints
def test_deletion_order_puts_every_child_before_its_parent():
    owner = 'owner'
    order = [table.name for table, _ in erase_plan(owner)]
    position = {name: index for index, name in enumerate(order)}
    assert set(order) == LEARNER_OWNED and order[-1] == 'users'
    for table in s.metadata.sorted_tables:
        if table.name not in position:
            continue
        for key in table.foreign_keys:
            parent = key.column.table.name
            if parent in position and parent != table.name:
                assert position[table.name] < position[parent], f'{table.name} must be erased before {parent}'


def test_the_foreign_keys_really_do_forbid_the_naive_order(repository_factory, exercise):
    """Why ordering matters: erasing a parent first fails, and review_improvements blocks exercises, attempts and users."""
    repo = repository_factory(); pg(repo)
    seed_everything(repo, exercise, shared_content(repo))
    for parent in ('exercises', 'attempts', 'users', 'lesson_lifecycles', 'homework_assignments'):
        with pytest.raises(IntegrityError):
            with repo.transaction() as db:
                db.execute(text(f'DELETE FROM {parent}' + (' WHERE id = :o' if parent == 'users' else '')), {'o': repo.learner_id} if parent == 'users' else {})
    with repo.transaction() as db:                     # and the planned order succeeds from the same state
        erase_application_data(db, repo.learner_id)
    assert all(count == 0 for count in counts(repo, repo.learner_id).values())


def test_a_table_with_no_ownership_path_is_a_loud_error_not_silent_data_left_behind(monkeypatch):
    from sqlalchemy import Column, MetaData, String, Table
    from app.storage.schema import identifier
    stray = Table('stray_learner_data', s.metadata, Column('id', identifier, primary_key=True), Column('note', String(10)))
    try:
        with pytest.raises(RuntimeError, match='stray_learner_data has no ownership path'):
            erase_plan('owner')
    finally:
        s.metadata.remove(stray)


# ------------------------------------------------------------------------------------------ Auth-aware deletion (PostgreSQL)
def seed_auth_rows(repo, owners):
    with repo.engine.begin() as db:
        for owner in owners:
            for table in ('sessions', 'identities', 'flow_state', 'scim_users'):
                db.execute(text(f'INSERT INTO auth.{table} VALUES (:id, :owner)'), {'id': str(uuid4()), 'owner': owner})


def test_postgres_deletion_removes_application_and_auth_rows_and_only_the_owners(repository_factory, exercise, tmp_path):
    a, b, _ = two_learners(repository_factory, exercise); pg(a)
    auth_schema(a.engine, [a.learner_id, b.learner_id]); seed_auth_rows(a, [a.learner_id, b.learner_id])
    cache = tmp_path / 'cache'; cache.mkdir()
    for name in (f'coach-{i}.mp3' for i in range(1)):
        (cache / name).write_bytes(b'cache')
    with a.engine.connect() as db:
        names = db.execute(select(s.exercises.c.audio_name).where(s.exercises.c.user_id == a.learner_id)).scalars().all()
    for name in names:
        (cache / name).write_bytes(b'cache')
    before_b = counts(b, b.learner_id)
    assert delete_account(a, cache) == {'deleted': True}
    assert all(count == 0 for count in counts(a, a.learner_id).values()) and counts(b, b.learner_id) == before_b
    with a.engine.connect() as db:
        for table in ('users', 'sessions', 'identities', 'flow_state', 'scim_users'):
            column = 'id' if table == 'users' else 'user_id'
            assert db.execute(text(f'SELECT count(*) FROM auth.{table} WHERE {column} = :o'), {'o': a.learner_id}).scalar_one() == 0
            assert db.execute(text(f'SELECT count(*) FROM auth.{table} WHERE {column} = :o'), {'o': b.learner_id}).scalar_one() >= 1
    assert not [path for path in cache.iterdir() if path.name in names]


def test_postgres_deletion_rolls_back_on_any_failure_and_refuses_unreviewed_storage(repository_factory, exercise, tmp_path):
    repo = repository_factory(); pg(repo)
    other = str(uuid4()); auth_schema(repo.engine, [repo.learner_id, other])
    seed_everything(repo, exercise, shared_content(repo)); seed_auth_rows(repo, [repo.learner_id, other])
    with repo.engine.begin() as db:
        db.execute(text('CREATE TABLE auth.blocker(user_id uuid REFERENCES auth.users)'))
        db.execute(text('INSERT INTO auth.blocker VALUES (:owner)'), {'owner': repo.learner_id})
    cache = tmp_path / 'cache'; cache.mkdir()
    before = counts(repo, repo.learner_id)
    with pytest.raises(Exception):
        delete_account(repo, cache)                      # an Auth-side foreign key failure at the very end
    assert counts(repo, repo.learner_id) == before and repo.profile().name == 'Robin'
    with repo.engine.begin() as db:
        db.execute(text('DELETE FROM auth.blocker'))
        db.execute(text("INSERT INTO storage.buckets VALUES ('new-unreviewed-storage')"))
    with pytest.raises(HTTPException) as error:
        delete_account(repo, cache)
    assert error.value.status_code == 503 and counts(repo, repo.learner_id) == before
    with repo.engine.begin() as db:
        db.execute(text('DELETE FROM storage.buckets'))
    assert delete_account(repo, cache) == {'deleted': True}


def test_postgres_deletion_refuses_shared_audio_names_and_unreadable_caches(repository_factory, exercise, tmp_path, monkeypatch):
    repo = repository_factory(); pg(repo)
    other = repository_factory(str(uuid4())); auth_schema(repo.engine, [repo.learner_id, other.learner_id])
    content = shared_content(repo)
    own_id = seed_everything(repo, exercise, content); other_id = seed_everything(other, exercise, content)
    with repo.engine.connect() as db:
        own_name = db.execute(select(s.exercises.c.audio_name).where(s.exercises.c.id == own_id)).scalar_one()
    cache = tmp_path / 'cache'; cache.mkdir(); (cache / own_name).write_bytes(b'cache')
    with repo.engine.begin() as db:
        db.execute(s.exercises.update().where(s.exercises.c.id == other_id).values(audio_name=own_name))
    with pytest.raises(HTTPException) as error:
        delete_account(repo, cache)
    assert error.value.status_code == 503 and (cache / own_name).read_bytes() == b'cache'
    with repo.engine.begin() as db:
        db.execute(s.exercises.update().where(s.exercises.c.id == other_id).values(audio_name='other-clip.mp3'))

    def refuse_unlink(path, *args, **kwargs):
        raise PermissionError('simulated unreadable cache')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', refuse_unlink)
        with pytest.raises(PermissionError):
            delete_account(repo, cache)
    assert repo.profile().name == other.profile().name == 'Robin'
    with repo.engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM auth.users')).scalar_one() == 2


def test_an_improvement_in_flight_cannot_survive_or_outlive_the_deletion(repository_factory, exercise, tmp_path):
    """The improvement route skips the account lease; foreign keys make that safe: its rows are erased and a late claim fails."""
    repo = repository_factory(); pg(repo)
    auth_schema(repo.engine, [repo.learner_id])
    exercise_id = seed_everything(repo, exercise, shared_content(repo), optional=False)
    root = store.evaluated_root(repo, exercise_id)
    claim = store.claim(repo, exercise_id, root['root_attempt'], 'The team restored it', 'v1', 10, utcnow())
    assert claim.kind == 'claimed'
    cache = tmp_path / 'cache'; cache.mkdir()
    assert delete_account(repo, cache) == {'deleted': True}
    assert store.finish(repo, claim.id, utcnow(), 'ready', improved_text='late', notes=[]) is False
    with pytest.raises(LookupError):
        store.claim(repo, exercise_id, root['root_attempt'], 'The team restored it', 'v1', 10, utcnow())
    with repo.engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM review_improvements')).scalar_one() == 0


# ------------------------------------------------------------------------------------------ the request path
def token_for(session_id):
    payload = base64.urlsafe_b64encode(json.dumps({'session_id': session_id}).encode()).decode().rstrip('=')
    return 'Bearer header.' + payload + '.verified-by-auth-fixture'


def deletion_app(tmp_path, repo, owner, **extra):
    def respond(request):
        if request.url.path.endswith('/logout'):
            return httpx.Response(204)
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'temporary', 'user': {'id': owner}})
        # Keep answering "valid", even after deletion: a request verified just before commit, or an unexpired JWT replay.
        return httpx.Response(200, json={'id': owner, 'email': 'test@example.com', 'email_confirmed_at': 'yes'})
    auth = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    settings = settings_for(tmp_path, persistence='postgres', database_url=repo.engine.url.render_as_string(hide_password=False),
                            account_deletion_enabled=True, **extra)
    return create_app(settings, FakeProvider(), repository=repo, auth_client=auth), auth


def test_postgres_deleted_account_cannot_be_resurrected_by_late_requests_or_replayed_tokens(repository_factory, exercise, tmp_path):
    repo = repository_factory(); pg(repo)
    owner = repo.learner_id; auth_schema(repo.engine, [owner])
    seed_everything(repo, exercise, shared_content(repo))
    session = str(uuid4())
    with repo.engine.begin() as db:
        db.execute(text('INSERT INTO auth.sessions VALUES (:id, :owner)'), {'id': session, 'owner': owner})
    app, auth = deletion_app(tmp_path, repo, owner)
    headers = {'Authorization': token_for(session)}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver', headers=headers) as client:
            saving, finish = asyncio.Event(), asyncio.Event()

            @app.post('/api/test-save')
            async def save():
                saving.set(); await finish.wait()
                profile = repo.profile(); profile.name = 'Saved before deletion'; repo.save_profile(profile)
                return {'saved': True}
            task = asyncio.create_task(client.post('/api/test-save'))
            await saving.wait()
            deletion = asyncio.create_task(client.post('/api/account/delete', json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'private'}))
            await asyncio.sleep(.2)
            assert not deletion.done()                      # the in-flight save holds the account lease; deletion waits for it
            finish.set(); assert (await task).status_code == 200
            response = await deletion
            assert response.status_code == 200 and response.json() == {'deleted': True}
            # Replays after deletion: reads and writes alike are refused and recreate nothing.
            for call in (client.get('/api/profile'), client.get('/api/review'), client.get('/api/history'),
                         client.post('/api/account/delete', json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'private'})):
                assert (await call).status_code == 401
    try:
        asyncio.run(run())
    finally:
        asyncio.run(auth.aclose())
    with repo.engine.connect() as db:
        assert db.execute(select(s.users).where(s.users.c.id == owner)).first() is None
        assert db.execute(select(s.profiles).where(s.profiles.c.user_id == owner)).first() is None
    assert all(count == 0 for count in counts(repo, owner).values())


def test_initialization_requires_the_live_auth_user_only_when_deletion_is_enabled(repository_factory):
    repo = repository_factory(None); pg(repo)
    auth_schema(repo.engine, [])
    ghost = str(uuid4())
    assert repo.for_learner(ghost).profile().name == 'Listener'          # default: unchanged behavior, no Auth lookup
    repo.require_auth_user = True
    with pytest.raises(AccountUnavailable):
        repo.for_learner(str(uuid4()))
    with repo.engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM users')).scalar_one() == 1


# ------------------------------------------------------------------------------------------ inert unless enabled
def test_deletion_is_inert_when_not_enabled(tmp_path):
    provider = FakeProvider()
    app = create_app(settings_for(tmp_path).model_copy(update={'auth_mode': 'local'}), provider)
    client = ASGIClient(app, provider)
    try:
        assert client.get('/api/account/controls').json() == {'delete_available': False}
        assert client.post('/api/account/delete', json={'confirmation': 'DELETE MY ACCOUNT', 'password': 'x'}).status_code == 404
    finally:
        app.state.repository.close()
    assert Settings(_env_file=None).account_deletion_enabled is False


def test_enabling_deletion_requires_supabase_auth_and_postgres():
    with pytest.raises(ValueError, match='requires AUDLI_AUTH_MODE=supabase and AUDLI_PERSISTENCE=postgres'):
        Settings(account_deletion_enabled=True, _env_file=None)


def test_deletion_request_validation_never_echoes_passwords():
    for body in ({'confirmation': 'DELETE MY ACCOUNT', 'password': ''}, {'confirmation': 'delete', 'password': 'x'},
                 {'confirmation': 'DELETE MY ACCOUNT', 'password': 'x', 'user_id': str(uuid4())}):
        with pytest.raises(ValidationError):
            DeleteAccount.model_validate(body)


# ------------------------------------------------------------------------------------------ reauthentication
def identity_for(tmp_path, respond):
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return SupabaseIdentity(settings_for(tmp_path), client), client


@pytest.mark.parametrize('status,matched,expected', [(200, True, None), (200, False, 403), (400, True, 403), (503, True, 503)])
def test_password_reauthentication_is_bound_to_the_owner(tmp_path, status, matched, expected):
    owner = str(uuid4()); calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/user'):
            return httpx.Response(200, json={'id': owner, 'email': 'test@example.com'})
        if request.url.path.endswith('/logout'):
            return httpx.Response(204)
        return httpx.Response(status, json={'access_token': 'temporary', 'user': {'id': owner if matched else str(uuid4())}})

    async def run():
        identity, client = identity_for(tmp_path, respond)
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


@pytest.mark.parametrize('stage', ['original', 'password'])
def test_mfa_accounts_are_refused_without_lowering_assurance(tmp_path, stage):
    owner = str(uuid4()); calls = []

    def respond(request):
        calls.append(request.url.path)
        user = {'id': owner, 'email': 'test@example.com'}
        if (request.url.path.endswith('/user') and stage == 'original') or (request.url.path.endswith('/token') and stage == 'password'):
            user['factors'] = [{'status': 'verified', 'factor_type': 'totp'}]
        if request.url.path.endswith('/user'):
            return httpx.Response(200, json=user)
        if request.url.path.endswith('/logout'):
            return httpx.Response(204)
        return httpx.Response(200, json={'access_token': 'temporary', 'user': user})

    async def run():
        identity, client = identity_for(tmp_path, respond)
        try:
            with pytest.raises(HTTPException) as error:
                await reauthenticate(identity, Request({'type': 'http', 'headers': [(b'authorization', b'Bearer original')]}), SecretStr('password'), owner)
            assert error.value.status_code == 403 and 'Keep MFA enabled' in error.value.detail
        finally:
            await client.aclose()
    asyncio.run(run())
    assert calls == ['/auth/v1/user'] if stage == 'original' else calls[-1].endswith('/logout')


def test_a_malformed_reauthentication_token_is_refused_safely(tmp_path):
    owner = str(uuid4())

    def respond(request):
        if request.url.path.endswith('/user'):
            return httpx.Response(200, json={'id': owner, 'email': 'test@example.com'})
        assert not request.url.path.endswith('/logout')
        return httpx.Response(200, json={'access_token': 123, 'user': {'id': owner}})

    async def run():
        identity, client = identity_for(tmp_path, respond)
        try:
            with pytest.raises(HTTPException) as error:
                await reauthenticate(identity, Request({'type': 'http', 'headers': [(b'authorization', b'Bearer original')]}), SecretStr('password'), owner)
            assert error.value.status_code == 403
        finally:
            await client.aclose()
    asyncio.run(run())


def test_deletion_requires_a_live_session_owned_by_the_requester(repository_factory):
    repo = repository_factory(); pg(repo)
    other = str(uuid4()); auth_schema(repo.engine, [repo.learner_id, other])
    own, foreign, revoked = (str(uuid4()) for _ in range(3))
    with repo.engine.begin() as db:
        for session, owner in ((own, repo.learner_id), (foreign, other)):
            db.execute(text('INSERT INTO auth.sessions VALUES (:id, :owner)'), {'id': session, 'owner': owner})
    for session in (own, foreign, revoked, None):
        request = Request({'type': 'http', 'headers': [(b'authorization', token_for(session).encode())]})
        if session == own:
            require_live_session(repo, request)
        else:
            with pytest.raises(HTTPException) as error:
                require_live_session(repo, request)
            assert error.value.status_code == 401
