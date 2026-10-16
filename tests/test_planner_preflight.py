"""Release gates: planner faults cannot become lesson failures; no live providers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.lesson import Lesson, now
from app.planner import qualifying_count
from app.storage import schema as s
from app.storage.planner import due
from test_persistence import repository_factory
from test_lesson_lifecycle import lesson_client
from test_planner import complete, ready


def closing(repo):
    lesson = Lesson(id=str(uuid4()), name='Maya', focus='details', started_at=now(), active_since=now(), phase='CLOSING', closing='Thanks for listening.')
    repo.create_lesson(lesson)
    return lesson


@pytest.mark.parametrize('fault', ['database', 'generation', 'timeout'])
def test_completion_survives_planner_fault_and_recovers(lesson_client, monkeypatch, caplog, fault):
    client = lesson_client; repo = client.app.state.repository
    initial = repo.roadmap()
    lesson = closing(repo)
    before = repo.profile()
    import app.storage.planner as planner
    def fail(*args, **kwargs):
        if fault == 'database':
            # Force a real aborted planner transaction on Postgres too.
            args[1].exec_driver_sql('SELECT * FROM aud40_missing_planner_table')
        elif fault == 'timeout':
            raise TimeoutError('PRIVATE learner/provider text')
        else:
            raise RuntimeError('PRIVATE learner/provider text')
    with monkeypatch.context() as patch:
        patch.setattr(planner, 'persist' if fault == 'database' else 'outlines', fail)
        response = client.post(f'/api/lessons/{lesson.id}/heard', json={'revision':0})
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed'
        assert repo.lesson(lesson.id).status == 'completed'
        assert repo.profile() == before
        with repo.engine.connect() as db:
            assert db.execute(select(s.roadmaps.c.data).where(s.roadmaps.c.user_id == repo.learner_id)).scalar_one()['revision'] == initial.revision
        assert 'Planner refresh deferred' in caplog.text
        assert 'PRIVATE' not in caplog.text and 'PRIVATE' not in response.text
    recovered = repo.roadmap()
    assert recovered.completed_ids == [lesson.id] and qualifying_count(recovered) == 1
    assert repo.roadmap() == recovered and due(recovered) is None
    assert client.post(f'/api/lessons/{lesson.id}/heard', json={'revision':0}).status_code == 409
    assert repo.roadmap() == recovered


@pytest.mark.parametrize('fault', ['timeout', 'database', 'missing', 'generation'])
def test_start_fallback_generates_audio_and_resumes_normally(lesson_client, monkeypatch, caplog, fault):
    client = lesson_client; repo = client.app.state.repository
    before = repo.profile()
    original = repo.roadmap
    def unavailable(*args, **kwargs):
        if fault == 'missing': return None
        if fault == 'timeout': raise TimeoutError('PRIVATE')
        if fault == 'database': raise OperationalError('PRIVATE SQL', {}, Exception('PRIVATE'))
        raise RuntimeError('PRIVATE')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'roadmap', unavailable)
        response = client.post('/api/lessons/start')
        assert response.status_code == 200, response.text
        lesson = repo.lesson()
        assert lesson.plan_number is None and lesson.plan_topic == before.interests[0]
        assert lesson.focus == before.focus and repo.profile() == before
        assert 'PRIVATE' not in caplog.text and 'PRIVATE' not in response.text
        lesson.phase = 'EXERCISE'; repo.save_lesson(lesson, lesson.revision)
        generated = client.post(f'/api/lessons/{lesson.id}/exercise', json={'revision':lesson.revision})
        assert generated.status_code == 200, generated.text
        exercise_id = generated.json()['exercise']['id']
        assert client.get(f'/api/exercises/{exercise_id}/audio').status_code == 200
        assert client.post(f'/api/lessons/{lesson.id}/end').json()['status'] == 'paused'
        assert client.post(f'/api/lessons/{lesson.id}/resume').json()['status'] == 'active'
    assert original().completed_ids == []
    assert repo.lesson().plan_number is None


def test_missing_planner_table_cannot_block_start_or_completion(lesson_client):
    client = lesson_client; repo = client.app.state.repository
    with repo.engine.begin() as db: s.roadmaps.drop(db)
    assert client.post('/api/lessons/start').status_code == 200
    lesson = repo.lesson(); lesson.phase = 'CLOSING'; lesson.closing = 'Thanks for listening.'
    repo.save_lesson(lesson, lesson.revision)
    response = client.post(f'/api/lessons/{lesson.id}/heard', json={'revision':lesson.revision})
    assert response.status_code == 200 and response.json()['status'] == 'completed'
    with repo.engine.begin() as db: s.roadmaps.create(db)
    plan = repo.roadmap()
    assert plan.completed_ids == [lesson.id] and qualifying_count(plan) == 1


def test_planner_partial_write_rolls_back_without_rolling_back_completion(lesson_client, monkeypatch):
    client = lesson_client; repo = client.app.state.repository
    initial = repo.roadmap(); lesson = closing(repo)
    import app.storage.planner as planner
    original = planner.persist
    def partial_write(repo, db, roadmap):
        original(repo, db, roadmap)
        raise RuntimeError('Injected failure after planner write')
    with monkeypatch.context() as patch:
        patch.setattr(planner, 'persist', partial_write)
        assert client.post(f'/api/lessons/{lesson.id}/heard', json={'revision':0}).status_code == 200
    assert repo.lesson(lesson.id).status == 'completed'
    with repo.engine.connect() as db:
        assert db.execute(select(s.roadmaps.c.data).where(s.roadmaps.c.user_id == repo.learner_id)).scalar_one()['completed_ids'] == initial.completed_ids
    assert qualifying_count(repo.roadmap()) == 1


def test_historical_activation_baseline_and_onboarding_preserve_cycles(repository_factory):
    repo = repository_factory(); ready(repo)
    activation = now() + timedelta(seconds=1)
    history = [complete(repo) for _ in range(4)]
    # Representative pre-004 records, without rewriting a historical lesson.
    with repo.engine.begin() as db:
        db.execute(s.versions.update().where(s.versions.c.version == 4).values(applied_at=activation))
    initial = repo.roadmap()
    assert set(initial.baseline_ids) == {lesson.id for lesson in history}
    assert qualifying_count(initial) == 0 and due(initial) is None
    assert initial.upcoming[0].number == 5
    # Move only test clock values for new completion objects beyond activation.
    for _ in range(3):
        lesson = closing(repo); lesson.status = 'completed'; lesson.phase = 'COMPLETED'; lesson.completed_at = activation + timedelta(seconds=1); lesson.active_since = None
        repo.save_lesson(lesson, 0)
    plan = repo.roadmap(); assert qualifying_count(plan) == 3 and due(plan) == 3
    resolved = repo.roadmap(revision=plan.revision, feedback={'status':'skipped'})
    before = repo.accent_lesson_history()
    __import__('app.onboarding_reset', fromlist=['reset_onboarding']).reset_onboarding(repo, repo.profile().onboarding.revision)
    restarted = repo.roadmap()
    assert restarted.baseline_ids == resolved.baseline_ids and restarted.feedback == resolved.feedback
    assert due(restarted) is None and repo.accent_lesson_history() == before


def test_concurrent_completion_and_refresh_are_idempotent(repository_factory):
    repo = repository_factory(); ready(repo); repo.roadmap()
    complete(repo); complete(repo)
    lesson = closing(repo)
    def finish(_):
        copy = lesson.model_copy(deep=True); copy.status = 'completed'; copy.phase = 'COMPLETED'; copy.completed_at = now(); copy.active_since = None
        try:
            repo.save_lesson(copy, 0)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(finish, range(2))) == 1
        plans = list(pool.map(lambda _: repo.roadmap(), range(4)))
    assert all(qualifying_count(plan) == 3 and due(plan) == 3 for plan in plans)
    resolved = repo.roadmap(revision=plans[-1].revision, feedback={'status':'answered', 'response':'The pace is fine'})
    assert len(resolved.feedback) == 1 and due(repo.roadmap()) is None
    with pytest.raises(ValueError): repo.roadmap(revision=plans[-1].revision, feedback={'status':'skipped'})
    assert len(repo.roadmap().feedback) == 1


def test_failed_authoritative_write_does_not_count_a_completion(repository_factory):
    repo = repository_factory(); ready(repo); plan = repo.roadmap(); lesson = closing(repo)
    lesson.status = 'completed'; lesson.completed_at = now(); lesson.phase = 'COMPLETED'
    with pytest.raises(ValueError): repo.save_lesson(lesson, 999)
    assert repo.lesson(lesson.id).status == 'active' and repo.roadmap() == plan


def test_planner_kill_switch_keeps_ordinary_lessons(lesson_client, tmp_path):
    from app.main import create_app
    from app.config import Settings
    from conftest import ASGIClient
    client = ASGIClient(create_app(Settings(data_dir=tmp_path, planner_enabled=False, _env_file=None),
        lesson_client.provider, repository=lesson_client.app.state.repository), lesson_client.provider)
    assert client.get('/api/plan').status_code == 503
    assert client.post('/api/lessons/start').status_code == 200
    repo = client.app.state.repository
    lesson = repo.lesson()
    assert lesson.plan_number is None
    lesson.phase = 'CLOSING'; lesson.closing = 'Thanks for listening.'
    repo.save_lesson(lesson, lesson.revision)
    response = client.post(f'/api/lessons/{lesson.id}/heard', json={'revision':lesson.revision})
    assert response.status_code == 200 and response.json()['status'] == 'completed'


def test_postgres_planner_lock_timeout_keeps_completion_authoritative(lesson_client):
    import time
    client = lesson_client; repo = client.app.state.repository
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL planner-table lock gate')
    repo.roadmap(); lesson = closing(repo)
    with repo.engine.begin() as locked:
        locked.exec_driver_sql('LOCK TABLE learner_roadmaps IN ACCESS EXCLUSIVE MODE')
        started = time.monotonic()
        result = client.post(f'/api/lessons/{lesson.id}/heard', json={'revision':0})
        assert result.status_code == 200 and result.json()['status'] == 'completed'
        assert time.monotonic() - started < 3
        assert repo.lesson(lesson.id).status == 'completed'
    assert qualifying_count(repo.roadmap()) == 1


def test_planner_outage_preserves_real_assessment_and_adaptation(lesson_client, audio_bytes, monkeypatch):
    from test_lesson_lifecycle import exercises, complete_exercise, age, action
    client = lesson_client; repo = client.app.state.repository
    lesson = exercises(client, audio_bytes)
    complete_exercise(client, lesson, audio_bytes)
    history = repo.history(); profile = repo.profile()
    assert profile.completed_attempts > 0 and history
    lesson = age(client, lesson, 530)
    lesson = action(client, lesson, 'advance')
    assert lesson['phase'] == 'CLOSING'
    with monkeypatch.context() as patch:
        def unavailable(*args, **kwargs): raise OperationalError('PRIVATE', {}, Exception('PRIVATE'))
        patch.setattr(repo, 'roadmap', unavailable)
        completed = action(client, lesson, 'heard')
        assert completed['status'] == 'completed'
    assert repo.history() == history and repo.profile() == profile
    assert qualifying_count(repo.roadmap()) == 1


def test_activation_persists_through_initial_refresh_failure(repository_factory, monkeypatch):
    repo = repository_factory(); ready(repo)
    import app.storage.planner as planner
    original = planner.persist
    with monkeypatch.context() as patch:
        def unavailable(*args, **kwargs): raise RuntimeError('initial planner outage')
        patch.setattr(planner, 'persist', unavailable)
        assert repo.optional_roadmap() is None
        for _ in range(3): complete(repo)
    recovered = repo.roadmap()
    assert qualifying_count(recovered) == 3 and due(recovered) == 3
    assert recovered.baseline_ids == []
    assert repo.roadmap().activation_at == recovered.activation_at
