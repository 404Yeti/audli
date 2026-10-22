"""Compatibility only: real PostgreSQL, mocked identity and speech providers."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.lesson import Lesson, now, prompt
from app.main import create_app
from app.repository import PostgresRepository
from conftest import FakeProvider
from test_auth import settings_for
from test_persistence import repository_factory
from test_lesson_lifecycle import lesson_client, exercises, complete_exercise, age, action


def snapshot(engine):
    """Fingerprint all public rows, including opaque planner snapshots."""
    with engine.connect() as db:
        names = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
        return {name: db.execute(text(f'SELECT row_to_json(t)::text FROM "{name}" t ORDER BY row_to_json(t)::text')).scalars().all()
                for name in names}


@pytest.mark.parametrize('version', [3, 4, 5])
def test_real_startup_preserves_all_rows_and_has_no_planner(repository_factory, tmp_path, version):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Real PostgreSQL compatibility gate')
    url = repo.engine.url.render_as_string(hide_password=False)
    with repo.engine.begin() as db:
        current = db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one()
        if current != version:
            pytest.skip('Older-schema gates run separately; never downgrade test records')
    profile = repo.profile()
    profile.name = 'Bridge learner'
    profile.onboarding_status = 'complete'
    profile.accent_preferences.accents = ['british']
    profile.accent_preferences.status = 'preferred'
    repo.save_profile(profile)
    at = now()
    lesson = Lesson(id=str(uuid4()), started_at=at, active_since=None,
                    status='paused', name=profile.name, focus=profile.focus,
                    plan_topic='Travel announcements', plan_number=7)
    repo.create_lesson(lesson)
    if version >= 4:
        with repo.engine.begin() as db:
            db.execute(text("INSERT INTO learner_roadmaps VALUES (:owner, 8, CAST(:data AS jsonb))"),
                       {'owner': repo.learner_id, 'data': '{"activation_at":"2026-10-14T00:00:00Z","baseline_ids":["historic"],"feedback":[{"status":"skipped"}],"opaque_future":true}'})
    before = snapshot(repo.engine)
    identity = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    app = create_app(settings_for(tmp_path, persistence='postgres', database_url=url, planner_enabled=False),
                     FakeProvider(), auth_client=identity)
    try:
        async def startup_probe():
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                    assert (await client.get('/api/health')).status_code == 200
                    assert (await client.get('/api/profile')).status_code == 401
                    assert (await client.get('/api/plan')).status_code == 401
                assert app.state.repository.for_learner(repo.learner_id).lesson(lesson.id) == lesson
                assert snapshot(repo.engine) == before
        asyncio.run(startup_probe())
    finally:
        app.state.repository.close()
        asyncio.run(identity.aclose())
    restarted = PostgresRepository(url)
    try:
        assert restarted.for_learner(repo.learner_id).profile() == profile
        assert snapshot(repo.engine) == before
    finally:
        restarted.close()


@pytest.mark.parametrize('versions', [[1], [1, 2], [1, 3], [1, 2, 4], [1, 2, 3, 5], [1, 2, 3, 4, 5, 7], [1, 2, 3, 4, 5, 6, 8], [1, 2, 3, 4, 5, 6, 7, 8], [1, 2, 3, 4, 5, 6, 7, 9]])
def test_incomplete_or_future_ledgers_still_fail_closed(repository_factory, versions):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Real PostgreSQL compatibility gate')
    with repo.engine.begin() as db:
        db.execute(text('DELETE FROM schema_migrations'))
        for version in versions:
            db.execute(text('INSERT INTO schema_migrations VALUES (:version, now())'), {'version': version})
    before = snapshot(repo.engine)
    with pytest.raises(RuntimeError, match='schema version validation'):
        PostgresRepository(repo.engine.url.render_as_string(hide_password=False))
    assert snapshot(repo.engine) == before


def test_planner_snapshot_metadata_is_inert_bounded_and_strict():
    at = now()
    ordinary = Lesson(id='lesson', started_at=at, active_since=at, name='Maya', focus='details')
    planned = ordinary.model_copy(update={'plan_topic': 'Travel', 'plan_number': 7})
    assert prompt(planned) == prompt(ordinary)
    assert Lesson.model_validate(planned.model_dump()).plan_number == 7
    for fields in ({'plan_number': 0}, {'plan_topic': 'x' * 161}, {'unknown_future_state': True}):
        with pytest.raises(ValidationError):
            Lesson.model_validate(ordinary.model_dump() | fields)


def test_assessed_lesson_and_resume_survive_004_transition(lesson_client, audio_bytes):
    client = lesson_client
    repo = client.app.state.repository
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Real PostgreSQL compatibility gate')
    item = exercises(client, audio_bytes)
    stored = repo.lesson(item['id'])
    stored.plan_topic = 'Travel announcements'
    stored.plan_number = 1
    repo.save_lesson(stored, stored.revision)
    item = client.get('/api/lessons/current').json()
    complete_exercise(client, item, audio_bytes)
    item = age(client, item, 520)
    item = action(client, item, 'advance')
    item = action(client, item, 'heard')
    assert item['status'] == 'completed'
    before = snapshot(repo.engine)
    with repo.engine.connect() as db:
        version = db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one()
    if version == 3:
        with repo.engine.connect() as db:
            db.exec_driver_sql((Path(__file__).parent / 'fixtures/postgres-004.sql').read_text())
        after = snapshot(repo.engine)
        assert all(after[name] == rows for name, rows in before.items() if name != 'schema_migrations')
        assert after['learner_roadmaps'] == []
    owner = repo.learner_id
    url = repo.engine.url.render_as_string(hide_password=False)
    restarted = PostgresRepository(url, owner)
    try:
        assert restarted.history() == repo.history()
        assert restarted.profile() == repo.profile()
        assert restarted.lesson(item['id']).status == 'completed'
        assert restarted.lesson(item['id']).plan_number == 1
    finally:
        restarted.close()
    # A later planner checkpoint can be resumed using ordinary rules.
    item = client.post('/api/lessons/start').json()
    stored = repo.lesson(item['id'])
    stored.plan_topic = 'Announcements'
    stored.plan_number = 2
    repo.save_lesson(stored, stored.revision)
    item = client.get('/api/lessons/current').json()
    item = action(client, item, 'end')
    item = action(client, item, 'resume')
    assert repo.lesson(item['id']).plan_topic == 'Announcements'
    assert client.get('/api/history').status_code == 200
    assert repo.lesson(item['id']).plan_number == 2
