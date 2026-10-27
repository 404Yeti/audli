"""AUD-21: phase persistence/projection stay sequential, owned and cancellation-safe."""
import asyncio
import threading
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, select, func, text

from app import turntrace
from app.config import Settings
from app.lesson import Lesson, now
from app.main import create_app
from app.offload import Offload
from app.storage import schema as s
from app.storage.repository import SQLProgressRepository
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory  # noqa: F401
from test_auth import USER_A, USER_B, settings_for, auth_response
from test_auth_lease_exemption import HEADERS, lease_key, client_for


def ready(repo):
    profile = repo.profile()
    profile.name = 'Synthetic'; profile.onboarding_status = 'complete'
    repo.save_profile(profile)
    lesson = Lesson(id=str(uuid4()), started_at=now(), active_since=now(), name='Synthetic', focus=profile.focus)
    lesson.phase = 'EXERCISE'
    repo.create_lesson(lesson)
    return lesson


def counts(repo):
    with repo.engine.connect() as db:
        return {table.name: db.execute(select(func.count()).select_from(table)).scalar_one()
                for table in (s.sessions, s.exercises, s.audio_assets, s.lesson_exercises, s.attempts)}


def local_app(repo, tmp_path):
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path / 'data', db_offload=True, _env_file=None), provider, repo)
    return app, ASGIClient(app, provider)


def test_transaction_failure_rolls_back_every_row_checkpoint_and_audio(repository_factory, tmp_path):
    repo = repository_factory(); lesson = ready(repo)
    app, client = local_app(repo, tmp_path)
    before = counts(repo); checkpoint = repo.lesson(lesson.id).model_dump()
    seen = []

    def fail_after_mapping(db, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO lesson_exercises'):
            seen.append(turntrace.on_loop())
            raise RuntimeError('synthetic failure after related rows were written')
    event.listen(repo.engine, 'after_cursor_execute', fail_after_mapping)
    try:
        response = client.post(f'/api/lessons/{lesson.id}/exercise', json={'revision': lesson.revision})
    finally:
        event.remove(repo.engine, 'after_cursor_execute', fail_after_mapping)
    assert response.status_code == 503 and seen == [False]
    assert counts(repo) == before and repo.lesson(lesson.id).model_dump() == checkpoint
    assert list((tmp_path / 'data' / 'exercise_audio').iterdir()) == []
    # A subsequent request can commit once: no poisoned transaction or orphaned state.
    response = client.post(f'/api/lessons/{lesson.id}/exercise', json={'revision': lesson.revision})
    assert response.status_code == 200
    saved = repo.lesson(lesson.id)
    assert saved.revision == lesson.revision + 1 and saved.exercise_id == response.json()['exercise']['id']
    assert counts(repo) == {**before, **{key: before[key] + 1 for key in ('training_sessions', 'exercises', 'audio_assets', 'lesson_exercises')}}
    assert len(list((tmp_path / 'data' / 'exercise_audio').iterdir())) == 1


def test_partial_audio_write_failure_cleans_up_without_starting_a_transaction(repository_factory, tmp_path, monkeypatch):
    from pathlib import Path
    repo = repository_factory(); lesson = ready(repo)
    app, client = local_app(repo, tmp_path)
    before = counts(repo); checkpoint = repo.lesson(lesson.id).model_dump()
    write = Path.write_bytes

    def fail(path, data):
        write(path, data[:8])
        raise OSError('synthetic partial disk write')
    monkeypatch.setattr(Path, 'write_bytes', fail)
    response = client.post(f'/api/lessons/{lesson.id}/exercise', json={'revision': lesson.revision})
    assert response.status_code == 503
    assert counts(repo) == before and repo.lesson(lesson.id).model_dump() == checkpoint
    assert list((tmp_path / 'data' / 'exercise_audio').iterdir()) == []


@pytest.mark.parametrize('rollback', [False, True])
def test_cancelled_phase_transaction_finishes_before_releasing_account_lease(repository_factory, tmp_path, rollback):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL account lease')
    scoped = repo.for_learner(USER_A); lesson = ready(scoped)
    before = counts(repo); checkpoint = scoped.lesson(lesson.id).model_dump()
    entered, release = threading.Event(), threading.Event()

    def blocked(db, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO lesson_exercises'):
            assert not turntrace.on_loop()
            entered.set(); assert release.wait(20)
            if rollback:
                raise RuntimeError('synthetic transaction failure during cancellation')
    event.listen(repo.engine, 'after_cursor_execute', blocked)
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path / "data", db_offload=True, lease_pool=False), FakeProvider(), repository=repo, auth_client=http)

    def lease_free():
        with repo.engine.connect() as db:
            return db.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lease_key()}).scalar_one()

    async def run():
        async with client_for(app) as client:
            request = asyncio.create_task(client.post(f'/api/lessons/{lesson.id}/exercise', headers=HEADERS, json={'revision': lesson.revision}))
            try:
                assert await asyncio.to_thread(entered.wait, 20)
                request.cancel()
                # Cancellation must not release the lease underneath this transaction.
                assert not await asyncio.to_thread(lease_free)
                assert (await asyncio.wait_for(client.get('/api/health'), 2)).status_code == 200
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await request
            for _ in range(100):
                if await asyncio.to_thread(lease_free):
                    break
                await asyncio.sleep(.01)
            assert await asyncio.to_thread(lease_free)
        await http.aclose()
    try:
        asyncio.run(run())
    finally:
        release.set(); event.remove(repo.engine, 'after_cursor_execute', blocked)
    saved = scoped.lesson(lesson.id)
    files = list((tmp_path / 'data' / 'exercise_audio').iterdir())
    if rollback:
        assert saved.model_dump() == checkpoint and counts(repo) == before and not files
    else:
        assert saved.revision == lesson.revision + 1 and saved.exercise_id
        assert scoped.exercise(saved.exercise_id) and len(files) == 1
        assert counts(repo)['exercises'] == before['exercises'] + 1
        assert counts(repo)['lesson_exercises'] == before['lesson_exercises'] + 1


def test_response_projection_does_not_block_health_or_change_checkpoint(repository_factory, tmp_path, monkeypatch):
    repo = repository_factory(); lesson = ready(repo)
    app, client = local_app(repo, tmp_path)
    saved = client.post(f'/api/lessons/{lesson.id}/exercise', json={'revision': lesson.revision}).json()
    checkpoint = repo.lesson(lesson.id).model_dump()
    entered, release = threading.Event(), threading.Event()
    original = SQLProgressRepository.exercise

    def slow(self, exercise_id):
        entered.set(); assert release.wait(20)
        assert not turntrace.on_loop()
        return original(self, exercise_id)
    monkeypatch.setattr(SQLProgressRepository, 'exercise', slow)

    async def run():
        async with client_for(app) as http:
            request = asyncio.create_task(http.get(f'/api/lessons/{lesson.id}'))
            try:
                assert await asyncio.to_thread(entered.wait, 20)
                assert not request.done()
                assert (await asyncio.wait_for(http.get('/api/health'), 2)).status_code == 200
            finally:
                release.set()
            response = await request
            assert response.status_code == 200 and response.json()['exercise'] == saved['exercise']
    asyncio.run(run())
    assert repo.lesson(lesson.id).model_dump() == checkpoint


def test_cancelled_resource_cleanup_is_off_loop_and_survives_repeated_cancellation():
    entered, acquired, cleaning, finish = (threading.Event() for _ in range(4))
    seen = []
    def acquire():
        entered.set(); assert acquired.wait(20)
        return object()
    def cleanup(value):
        seen.append(turntrace.on_loop()); cleaning.set(); assert finish.wait(20)
    async def run():
        task = asyncio.create_task(Offload(True).run(acquire, on_cancel=cleanup))
        try:
            assert await asyncio.to_thread(entered.wait, 20)
            task.cancel(); acquired.set()
            assert await asyncio.to_thread(cleaning.wait, 20)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            acquired.set(); finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert seen == [False]


def test_concurrent_phase_creation_is_owned_and_one_checkpoint_per_account(repository_factory, tmp_path):
    repo = repository_factory(None)
    lessons = {owner: ready(repo.for_learner(owner)) for owner in (USER_A, USER_B)}
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, db_offload=True, lease_pool=False), FakeProvider(), repository=repo, auth_client=http)

    async def run():
        async with client_for(app) as client:
            async def generate(owner, token):
                value = lessons[owner]
                return await client.post(f'/api/lessons/{value.id}/exercise', json={'revision': value.revision}, headers={'Authorization': 'Bearer ' + token})
            responses = await asyncio.gather(generate(USER_A, 'valid-a'), generate(USER_B, 'valid-b'))
            assert [r.status_code for r in responses] == [200, 200]
            # A duplicated stale request must not insert a second exercise/checkpoint.
            duplicate = await generate(USER_A, 'valid-a')
            assert duplicate.status_code == 409
            return [r.json()['exercise']['id'] for r in responses]
    a, b = asyncio.run(run())
    assert a != b
    for owner, own, other in ((USER_A, a, b), (USER_B, b, a)):
        scoped = repo.for_learner(owner)
        saved = scoped.lesson(lessons[owner].id)
        assert saved.revision == lessons[owner].revision + 1 and saved.exercise_id == own
        assert scoped.exercise(own) and scoped.exercise(other) is None
    assert counts(repo)['exercises'] == counts(repo)['lesson_exercises'] == 2
    assert counts(repo)['attempts'] == 0
    asyncio.run(http.aclose())


def test_failed_lease_candidate_rollback_stays_off_loop(repository_factory, tmp_path):
    from app.auth import LearnerAuthentication
    from test_auth_lease_exemption import hold_lease
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL account lease')
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, db_offload=True, lease_pool=False), FakeProvider(), repository=repo, auth_client=http)
    app.middleware_stack = app.build_middleware_stack()
    node = app.middleware_stack
    while not isinstance(node, LearnerAuthentication):
        node = node.app
    entered, release, finished = (threading.Event() for _ in range(3))
    seen = []
    def rollback(connection):
        seen.append(turntrace.on_loop()); entered.set()
        assert release.wait(20)
        finished.set()
    event.listen(node.lease_engine, 'rollback', rollback)
    blocker, transaction = hold_lease(repo)
    async def run():
        async with client_for(app) as client:
            request = asyncio.create_task(client.post('/api/recognition/retry-audio', headers=HEADERS))
            try:
                assert await asyncio.to_thread(entered.wait, 20)
                assert (await asyncio.wait_for(client.get('/api/health'), 2)).status_code == 200
                request.cancel()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert await asyncio.to_thread(finished.wait, 20)
        await http.aclose()
    try:
        asyncio.run(run())
    finally:
        release.set(); transaction.rollback(); blocker.close()
        event.remove(node.lease_engine, 'rollback', rollback)
    assert seen and all(value is False for value in seen)
