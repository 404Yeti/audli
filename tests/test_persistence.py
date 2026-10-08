"""Repository contract on SQLite and, when explicitly configured, isolated Postgres.

AUDLI_TEST_POSTGRES_URL must point to a disposable test cluster whose owner can
create databases. Each case creates and drops its own audli_test_* database.
Normal pytest needs no server or Supabase credentials.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app
from app.repository import ProgressRepository, PostgresRepository
from app.storage import schema as s
from app.storage.migrations import postgres_baseline, postgres_lesson_migration
from app.models import Conversation, ConversationTurn, LearnerProfile, Transcription
from app.conversation import final_evaluation, adapt_final
from conftest import ASGIClient, FakeProvider
from test_conversation import begin, answer, scripted, assessment


BACKENDS = ['sqlite'] + (['postgres'] if os.environ.get('AUDLI_TEST_POSTGRES_URL') else [])


@pytest.fixture(params=BACKENDS)
def repository_factory(request, tmp_path):
    learner_id = str(uuid4())
    opened = []
    if request.param == 'postgres':
        import psycopg
        from psycopg import sql
        from scripts.migrate_postgres import migrate
        admin_url = os.environ['AUDLI_TEST_POSTGRES_URL']
        name = 'audli_test_' + uuid4().hex
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        settings = Settings(persistence='postgres', database_url=url, learner_id=learner_id, _env_file=None)
        assert migrate(settings) is True
        assert migrate(settings) is False
        def create(owner=learner_id):
            repo = PostgresRepository(url, owner)
            opened.append(repo)
            return repo
    else:
        def create(owner=learner_id):
            repo = ProgressRepository(tmp_path / 'audli.sqlite3', owner)
            opened.append(repo)
            return repo
    try:
        yield create
    finally:
        for repo in opened:
            repo.close()
        if request.param == 'postgres':
            with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin:
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def test_learner_and_onboarding_readiness_survive_reconstruction(repository_factory):
    repo = repository_factory()
    profile = repo.profile()
    profile.name, profile.goal = 'Maya', 'Follow meetings'
    profile.interests = ['science', 'travel']
    profile.target_situations = ['Team meetings', 'Airport announcements']
    profile.onboarding_status = 'in_progress'
    profile.difficulty.speech_rate = .8
    profile.difficulty.duration_seconds = 60
    profile.difficulty.vocabulary_level = 'B2'
    profile.difficulty.information_density = 2
    profile.listening_profile.details = .55
    repo.save_profile(profile)
    repo.close()
    reopened = repository_factory()
    assert reopened.profile() == profile
    with reopened.engine.connect() as db:
        row = db.execute(select(s.profiles)).mappings().one()
        assert row['goal'] == profile.goal and row['details'] == .55
        assert row['target_situations'] == profile.target_situations
        assert row['onboarding_status'] == 'in_progress'
        assert row['speech_rate'] == .8 and row['speaker_count'] == 1


def test_full_evidence_loop_survives_restart_and_empty_audio_cache(repository_factory, tmp_path, audio_bytes):
    provider = FakeProvider()
    async def speak(message, speech_rate=.9):
        return audio_bytes
    provider.speak = speak
    settings = Settings(data_dir=tmp_path / 'first-cache', _env_file=None)
    repo = repository_factory()
    client = ASGIClient(create_app(settings, provider, repository=repo), provider)
    assert client.put('/api/profile', json={'name': 'Maya', 'goal': 'Understand meetings'}).status_code == 200
    calls = scripted(client, [{'inference': 'insufficient_evidence'}, {}])
    exercise = begin(client)
    prefix = f"/api/exercises/{exercise['id']}"
    assert client.post(prefix + '/coach-audio', json={'cue_id': 'summary'}).status_code == 200
    initial_attempt, _, initial = answer(client, exercise, audio_bytes)
    assert initial.status_code == 200
    checkpoint = initial.json()
    assert checkpoint['result'] is None and checkpoint['active_followup']
    assert repo.session_history()[0].status == 'active'
    repo.close()

    # New backend filesystem; only the database survives.
    reopened = repository_factory()
    resumed = ASGIClient(create_app(Settings(data_dir=tmp_path / 'second-cache', _env_file=None),
        provider, repository=reopened), provider)
    assert resumed.get(prefix + '/conversation').json() == checkpoint
    assert resumed.get(prefix + '/transcript').status_code == 403
    assert resumed.get(exercise['audio_url']).status_code == 200
    assert resumed.get(prefix + '/coach-audio/summary').status_code == 200
    assert reopened.attempt(initial_attempt['id'])['transcription']
    followup_attempt, body, final = answer(resumed, exercise, audio_bytes, checkpoint['active_followup'],
        text='They wanted to avoid another failure by testing first.')
    assert final.status_code == 200, final.text
    result = final.json()['result']
    assert result['adaptation']['new_difficulty']['speech_rate'] == .8
    profile = reopened.profile()
    assert profile.onboarding_status == 'profile_saved'
    assert profile.initial_listening_profile is not None
    assert profile.completed_attempts == 1
    reopened.close()

    final_repo = repository_factory()
    restored = ASGIClient(create_app(Settings(data_dir=tmp_path / 'third-cache', _env_file=None),
        provider, repository=final_repo), provider)
    assert restored.get(prefix + '/transcript').status_code == 200
    assert final_repo.result(initial_attempt['id']) == result
    assert final_repo.profile() == profile
    assert final_repo.conversation(exercise['id']).turns[-1].attempt_id == followup_attempt['id']
    assert restored.post(f"/api/attempts/{followup_attempt['id']}/assess", json=body).json()['result'] == result
    assert len(calls) == 2 and len(final_repo.history()) == 1
    session = final_repo.session_history()[0]
    assert session.status == 'completed' and session.completed_at.tzinfo is not None
    assert session.started_at.tzinfo is not None
    assert final_repo.session_exercises(session.id)[0]['id'] == exercise['id']
    assert final_repo.session_history(completed_since=session.completed_at, before=session.completed_at + timedelta(seconds=1)) == [session]
    assert final_repo.session_history(completed_since=session.completed_at + timedelta(seconds=1)) == []
    with pytest.raises(ValueError, match='timezone'):
        final_repo.session_history(completed_since=datetime(2026, 1, 1))
    with final_repo.engine.connect() as db:
        old_evidence = db.execute(select(s.evidence.c.status).where(s.evidence.c.attempt_id == initial_attempt['id'])).scalars().all()
        final_evidence = db.execute(select(s.evidence.c.status).where(s.evidence.c.attempt_id == followup_attempt['id'])).scalars().all()
        assert 'insufficient_evidence' in old_evidence
        assert set(final_evidence) == {'demonstrated'}
        event = db.execute(select(s.events)).mappings().one()
        assert event['old_value'] == .75 and event['new_value'] == .8 and event['changed_variable'] == 'speech_rate'
        assert event['reason'] == result['adaptation']['reason'] and event['created_at']
    next_exercise = restored.post('/api/exercises').json()
    assert next_exercise['difficulty'] == profile.difficulty.model_dump()
    assert json.loads(final_repo.exercise(next_exercise['id'])['content'])['speech_rate'] == .8
    assert len(final_repo.session_history()) == 2
    assert restored.get(f"/api/exercises/{next_exercise['id']}/transcript").status_code == 403


def prepare_completion(repo, exercise):
    exercise_id = str(uuid4())
    repo.save_exercise(exercise_id, exercise, repo.profile().difficulty, 'clip.mp3', audio=b'generated-audio')
    attempt_id = repo.add_attempt(exercise_id, Transcription(text='The team restored the previous version.'))
    turn = ConversationTurn(attempt_id=attempt_id, text='The team restored the previous version.')
    evidence = assessment(exercise, [turn])
    conversation = Conversation(exercise_id=exercise_id, turns=[turn], assessment=evidence, state='GIVING_FEEDBACK')
    evaluation = final_evaluation(exercise, evidence)
    profile, event = adapt_final(repo.profile(), evaluation, Settings(_env_file=None))
    return attempt_id, evaluation, profile, event, conversation


def test_completion_is_atomic_and_duplicate_safe(repository_factory, exercise, monkeypatch):
    repo = repository_factory()
    attempt_id, evaluation, profile, event, conversation = prepare_completion(repo, exercise)
    original = repo.write_conversation
    def fail(db, conversation):
        raise RuntimeError('Simulated checkpoint storage failure')
    monkeypatch.setattr(repo, 'write_conversation', fail)
    with pytest.raises(RuntimeError):
        repo.complete(attempt_id, 'Confirmed answer', evaluation, event, profile, conversation)
    reopened = repository_factory()
    assert reopened.profile().completed_attempts == 0
    assert reopened.attempt(attempt_id)['status'] == 'transcribed'
    assert reopened.result(attempt_id) is None and reopened.history() == []
    assert reopened.session_history()[0].status == 'active'
    monkeypatch.setattr(repo, 'write_conversation', original)
    reopened.complete(attempt_id, 'Confirmed answer', evaluation, event, profile, conversation)
    with pytest.raises(ValueError, match='already evaluated'):
        repo.complete(attempt_id, 'Confirmed answer', evaluation, event, profile, conversation)
    assert len(repository_factory().history()) == 1


def test_ownership_is_scoped_for_reads_and_writes(repository_factory, exercise):
    first = repository_factory()
    attempt_id, evaluation, profile, event, conversation = prepare_completion(first, exercise)
    other = repository_factory(str(uuid4()))
    exercise_id = conversation.exercise_id
    assert other.exercise(exercise_id) is None and other.attempt(attempt_id) is None
    assert other.conversation(exercise_id) is None and other.completed(exercise_id) is None
    assert other.result(attempt_id) is None and other.current_exercise() is None
    assert other.history() == [] and other.session_history() == []
    assert other.session_exercises(first.session_history()[0].id) == []
    assert other.audio_blob('clip.mp3') is None
    for operation in (
        lambda: other.confirm_text(attempt_id, 'Changed'),
        lambda: other.add_attempt(exercise_id, Transcription(text='Changed')),
        lambda: other.save_conversation(conversation),
        lambda: other.save_coach_audio(exercise_id, 'summary', 'other.mp3'),
        lambda: other.complete(attempt_id, 'Changed', evaluation, event, profile, conversation),
    ):
        with pytest.raises(ValueError):
            operation()
    assert first.attempt(attempt_id)['confirmed_text'] is None


def test_legacy_sqlite_upgrade_preserves_completed_state(tmp_path, exercise):
    from app.models import Followup
    from app.evaluation import score_judgments
    from app.adaptive import adapt
    import asyncio
    provider = FakeProvider()
    text_answer = 'The team restored the previous version.'
    evaluation = score_judgments(exercise, asyncio.run(provider.evaluate(exercise, text_answer)), text_answer)
    profile, event = adapt(LearnerProfile(name='Maya'), evaluation, Settings(_env_file=None))
    old_profile = profile.model_dump()
    for key in ('target_situations', 'onboarding_status', 'initial_listening_profile'):
        old_profile.pop(key)
    path = tmp_path / 'audli.sqlite3'
    exercise_id, attempt_id = str(uuid4()), str(uuid4())
    pending_exercise_id, pending_attempt_id = str(uuid4()), str(uuid4())
    pending_turn = ConversationTurn(attempt_id=pending_attempt_id, text=text_answer)
    pending_assessment = assessment(exercise, [pending_turn], {'inference': 'insufficient_evidence'})
    question = Followup(id=str(uuid4()), dimension='inference', index=0, question='Why did they test first?')
    checkpoint = Conversation(exercise_id=pending_exercise_id, state='AWAITING_FOLLOWUP',
        turns=[pending_turn], assessment=pending_assessment, followups=[question], active_followup=question)
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / 'fixtures/sqlite_v0.2.sql').read_text())
        db.execute('INSERT INTO users(id) VALUES (?)', ('local',))
        db.execute('INSERT INTO learner_profiles VALUES (?,?)', ('local', json.dumps(old_profile)))
        db.execute('INSERT INTO exercises(id,user_id,content,difficulty,audio_name) VALUES (?,?,?,?,?)',
            (exercise_id, 'local', exercise.model_dump_json(), event.old_difficulty.model_dump_json(), 'old.mp3'))
        db.execute('INSERT INTO attempts(id,exercise_id,transcription,confirmed_text,status) VALUES (?,?,?,?,?)',
            (attempt_id, exercise_id, Transcription(text=text_answer).model_dump_json(), text_answer, 'evaluated'))
        db.execute('INSERT INTO evaluation_results VALUES (?,?)', (attempt_id, evaluation.model_dump_json()))
        db.execute('INSERT INTO adaptation_events(id,attempt_id,data) VALUES (?,?,?)', (str(uuid4()), attempt_id, event.model_dump_json()))
        db.execute('UPDATE exercises SET created_at=? WHERE id=?', ('2026-10-01 10:00:00', exercise_id))
        db.execute('UPDATE attempts SET created_at=? WHERE id=?', ('2026-10-01 10:01:00', attempt_id))
        db.execute('UPDATE adaptation_events SET created_at=? WHERE attempt_id=?', ('2026-10-01 10:03:00', attempt_id))
        db.execute('INSERT INTO exercises(id,user_id,content,difficulty,audio_name) VALUES (?,?,?,?,?)',
            (pending_exercise_id, 'local', exercise.model_dump_json(), profile.difficulty.model_dump_json(), 'pending.mp3'))
        db.execute('INSERT INTO attempts(id,exercise_id,transcription,confirmed_text,status) VALUES (?,?,?,?,?)',
            (pending_attempt_id, pending_exercise_id, Transcription(text=text_answer).model_dump_json(), text_answer, 'transcribed'))
        db.execute('INSERT INTO conversations VALUES (?,?)', (pending_exercise_id, checkpoint.model_dump_json()))
    upgraded = ProgressRepository(path)
    assert upgraded.profile().difficulty == profile.difficulty
    assert upgraded.profile().completed_attempts == 1
    assert upgraded.result(attempt_id) == {'evaluation': evaluation.model_dump(), 'adaptation': event.model_dump()}
    assert {session.status for session in upgraded.session_history()} == {'active', 'completed'}
    completed = next(session for session in upgraded.session_history() if session.status == 'completed')
    assert completed.completed_at == datetime(2026, 10, 1, 10, 3, tzinfo=timezone.utc)
    assert upgraded.conversation(pending_exercise_id) == checkpoint
    assert upgraded.current_exercise()['id'] == pending_exercise_id
    assert upgraded.profile().onboarding_status == 'profile_saved'
    assert upgraded.completed(exercise_id) == attempt_id
    assert path.with_suffix('.pre-postgres.sqlite3').exists()
    assert ProgressRepository(path).history() == upgraded.history()


def test_blank_placeholder_values_keep_local_configuration_working(monkeypatch):
    monkeypatch.setenv('AUDLI_DATABASE_URL', '')
    monkeypatch.setenv('AUDLI_LEARNER_ID', '')
    settings = Settings(environment='development', persistence='sqlite', _env_file=None)
    assert settings.database_url is None and settings.learner_id is None


def test_stale_adaptation_cannot_overwrite_persisted_difficulty(repository_factory, exercise):
    repo = repository_factory()
    attempt_id, evaluation, profile, event, conversation = prepare_completion(repo, exercise)
    newer = repo.profile()
    newer.difficulty.speech_rate = .85
    repo.save_profile(newer)
    with pytest.raises(ValueError, match='state changed'):
        repo.complete(attempt_id, 'Confirmed answer', evaluation, event, profile, conversation)
    assert repository_factory().profile() == newer
    assert repo.result(attempt_id) is None and repo.session_history()[0].status == 'active'


def test_concurrent_completion_applies_adaptation_once(repository_factory, exercise):
    from concurrent.futures import ThreadPoolExecutor
    first = repository_factory()
    second = repository_factory()
    attempt_id, evaluation, profile, event, conversation = prepare_completion(first, exercise)
    def finish(repo):
        try:
            repo.complete(attempt_id, 'Confirmed answer', evaluation, event, profile, conversation)
            return 'completed'
        except ValueError:
            return 'duplicate'
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(finish, [first, second])) == ['completed', 'duplicate']
    assert repository_factory().profile().completed_attempts == 1 and len(first.history()) == 1


@pytest.mark.parametrize('kwargs', [
    {'environment': 'production'},
    {'persistence': 'postgres'},
    {'persistence': 'postgres', 'database_url': 'postgresql://user:private-password@host/db'},
    {'persistence': 'postgres', 'learner_id': str(uuid4())},
    {'persistence': 'postgres', 'learner_id': str(uuid4()), 'database_url': 'https://user:private-password@host/db'},
    {'persistence': 'postgres', 'learner_id': str(uuid4()), 'database_url': 'postgresql://host/db'},
    {'environment': 'production', 'persistence': 'postgres', 'learner_id': str(uuid4()), 'database_url': 'postgresql://user:private-password@host/db?sslmode=disable'},
    {'persistence': 'postgres', 'learner_id': 'not-a-uuid', 'database_url': 'postgresql://user:private-password@host/db'},
    {'database_url': 'postgresql://user:private-password@host/db'},
])
def test_invalid_persistence_settings_fail_without_exposing_credentials(kwargs):
    with pytest.raises(ValidationError) as error:
        Settings(**kwargs, _env_file=None)
    assert 'private-password' not in str(error.value)


def test_postgres_startup_failure_does_not_fallback_or_leak_credentials(monkeypatch):
    from app.storage import adapters
    def fail(*args, **kwargs):
        raise RuntimeError('postgresql://user:private-password@host/db')
    monkeypatch.setattr(adapters, 'create_engine', fail)
    with pytest.raises(RuntimeError, match='Postgres persistence could not start') as error:
        PostgresRepository('postgresql://user:private-password@host/db', str(uuid4()))
    assert 'private-password' not in str(error.value) and error.value.__suppress_context__


@pytest.mark.parametrize('stage,code,expected', [
    ('connection', '28P01', 'Database authentication failed'),
    ('connection', None, 'database connection'),
    ('versions', '42501', 'Database role lacks required permissions'),
    ('versions', '42P01', 'Required schema tables are missing'),
    ('version_mismatch', None, 'python -m scripts.migrate_postgres'),
    ('columns', None, 'required table and column validation'),
    ('missing_column', None, 'Required schema columns are missing'),
    ('learner', '42501', 'development learner initialization'),
])
def test_postgres_startup_diagnostics_are_specific_but_private(monkeypatch, stage, code, expected):
    from unittest.mock import MagicMock
    from app.storage import adapters
    secret = 'postgresql://private-user:private-password@private-host/db private-learner-text'
    failure = RuntimeError(secret)
    failure.sqlstate = code
    engine = MagicMock()
    db = engine.connect.return_value.__enter__.return_value
    db.execute.return_value.scalars.return_value.all.return_value = [1] if stage == 'version_mismatch' else [1, 2]
    inspector = MagicMock()
    inspector.get_columns.side_effect = lambda name: [{'name': column.name} for column in s.metadata.tables[name].columns]
    monkeypatch.setattr(adapters, 'create_engine', lambda *args, **kwargs: engine)
    monkeypatch.setattr(adapters, 'inspect', lambda db: inspector)
    if stage == 'connection': engine.connect.side_effect = failure
    if stage == 'versions': db.execute.side_effect = failure
    if stage == 'columns': inspector.get_columns.side_effect = failure
    if stage == 'missing_column': inspector.get_columns.side_effect = None; inspector.get_columns.return_value = []
    if stage == 'learner': monkeypatch.setattr(PostgresRepository, 'initialize_learner', lambda self: (_ for _ in ()).throw(failure))
    with pytest.raises(RuntimeError, match=expected) as error:
        PostgresRepository(secret.split(' ')[0], str(uuid4()) if stage == 'learner' else None)
    engine.dispose.assert_called_once()
    assert error.value.__suppress_context__
    assert all(value not in str(error.value) for value in ('private-user', 'private-password', 'private-host', 'private-learner-text'))


def test_backend_image_contains_all_numbered_migrations():
    import shlex
    from app.storage.adapters import SCHEMA_VERSION
    root = Path(__file__).parents[1]
    copies = [shlex.split(line) for line in (root / 'Dockerfile').read_text().splitlines() if line.startswith('COPY ')]
    packaged = {destination for instruction, *sources, destination in copies if all((root / source).exists() for source in sources)}
    for version in range(1, SCHEMA_VERSION + 1):
        path = 'docs/postgres.sql' if version == 1 else f'docs/postgres-{version:03}.sql'
        assert path in packaged, f'Backend image is missing {path}; the migration runner cannot upgrade existing databases'


def test_postgres_baseline_matches_schema_and_protects_browser_roles():
    assert (Path(__file__).parents[1] / 'docs/postgres.sql').read_text() == postgres_baseline()
    for table in s.metadata.sorted_tables:
        assert f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY;' in postgres_baseline() + postgres_lesson_migration()


def test_import_retains_progress_and_refuses_overwrite(repository_factory, tmp_path, exercise):
    from scripts.import_sqlite import import_learner
    source_path = tmp_path / 'source.sqlite3'
    source = ProgressRepository(source_path)
    attempt_id, evaluation, profile, event, conversation = prepare_completion(source, exercise)
    source.complete(attempt_id, 'Confirmed answer', evaluation, event, profile, conversation)
    target = repository_factory()
    assert import_learner(source_path, target) == 1
    assert target.profile() == source.profile()
    assert target.result(attempt_id) == source.result(attempt_id)
    assert target.conversation(conversation.exercise_id) == source.conversation(conversation.exercise_id)
    assert target.audio_blob('clip.mp3') == b'generated-audio'
    assert target.session_history()[0].learner_id == target.learner_id
    with pytest.raises(ValueError, match='already has exercises'):
        import_learner(source_path, target)
    assert len(target.history()) == 1
    with pytest.raises(ValueError, match='does not exist'):
        import_learner(source_path, target, 'nonexistent-owner')
    source.close()


def test_unsupported_schema_version_fails_without_resetting_state(repository_factory):
    repo = repository_factory()
    profile = repo.profile()
    profile.name = 'Preserve me'
    repo.save_profile(profile)
    with repo.engine.begin() as db:
        db.execute(s.versions.update().where(s.versions.c.version == 2).values(version=999))
    with pytest.raises(RuntimeError):
        repository_factory()
    assert repo.profile().name == 'Preserve me'


def test_postgres_rls_blocks_an_untrusted_role(repository_factory):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        return
    role = 'audli_untrusted_' + uuid4().hex
    from app.lesson import Lesson, now
    at = now()
    repo.create_lesson(Lesson(id=str(uuid4()), started_at=at, active_since=at, name='Private learner', focus='details'))
    with repo.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {role} NOLOGIN')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {role}')
        db.exec_driver_sql(f'GRANT SELECT ON learner_profiles TO {role}')
        db.exec_driver_sql(f'GRANT SELECT ON lesson_lifecycles, lesson_exercises TO {role}')
    try:
        with repo.engine.begin() as db:
            db.exec_driver_sql(f'SET LOCAL ROLE {role}')
            assert db.exec_driver_sql('SELECT count(*) FROM learner_profiles').scalar_one() == 0
            assert db.exec_driver_sql('SELECT count(*) FROM lesson_lifecycles').scalar_one() == 0
            assert db.exec_driver_sql('SELECT count(*) FROM lesson_exercises').scalar_one() == 0
    finally:
        with repo.engine.begin() as db:
            db.exec_driver_sql(f'DROP OWNED BY {role}')
            db.exec_driver_sql(f'DROP ROLE {role}')
