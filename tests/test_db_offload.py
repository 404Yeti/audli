"""AUD-87: synchronous database sections run off the event loop (AUDLI_DB_OFFLOAD, default off), with identical results."""
import asyncio
import re
import threading
import time

import httpx
import pytest

from app import turntrace
from app.config import Settings
from app.main import create_app
from app.offload import Offload
from app.storage.repository import SQLProgressRepository
from conftest import ASGIClient, FakeProvider
from lesson_flow import run_lesson
from test_persistence import repository_factory  # noqa: F401  (fixture)
from test_state_only_ready import OWNER, OTHER, deterministic_ids, dump, make_repo, normalise  # noqa: F401  (fixtures)


def app_for(repo, tmp_path, flag, audio_bytes):
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return audio_bytes
    provider.speak = speak
    app = create_app(Settings(data_dir=tmp_path / f'data-{flag}', db_offload=flag, _env_file=None), provider, repo)
    return app, ASGIClient(app, provider)


def test_off_by_default():
    assert Settings.model_fields['db_offload'].default is False


def test_a_whole_lesson_gives_identical_responses_and_rows_with_the_flag_on(make_repo, tmp_path, audio_bytes, monkeypatch):
    results = {}
    for flag in (False, True):
        deterministic_ids(monkeypatch)
        repo = make_repo(f'flag-{flag}')
        _, client = app_for(repo, tmp_path, flag, audio_bytes)
        ready = run_lesson(client, audio_bytes)
        results[flag] = (normalise(ready), dump(repo))
    assert results[True] == results[False]


def test_the_hot_routes_really_leave_the_loop_thread_and_the_flag_off_keeps_them_on_it(make_repo, tmp_path, audio_bytes, monkeypatch):
    seen = {}
    names = ('attempt', 'conversation', 'add_attempt', 'save_conversation', 'prepare_assessment', 'complete', 'coach_audio', 'save_coach_audio', 'audio_blob')
    for flag in (False, True):
        seen[flag] = {}
        deterministic_ids(monkeypatch)
        repo = make_repo(f'loop-{flag}')
        for name in names:
            original = getattr(SQLProgressRepository, name)

            def spy(self, *args, _original=original, _name=name, **kw):
                seen[flag].setdefault(_name, set()).add(turntrace.on_loop())
                return _original(self, *args, **kw)
            monkeypatch.setattr(SQLProgressRepository, name, spy)
        _, client = app_for(repo, tmp_path, flag, audio_bytes)
        run_lesson(client, audio_bytes)
        monkeypatch.undo()
    for name in ('attempt', 'add_attempt', 'prepare_assessment', 'complete', 'save_coach_audio', 'coach_audio'):
        assert seen[False][name] == {True}, name      # unchanged: on the loop
        assert seen[True][name] == {False}, name      # offloaded: never on the loop


def test_the_loop_keeps_serving_other_requests_while_a_database_section_runs(make_repo, tmp_path, audio_bytes, monkeypatch):
    latencies = {}
    for flag in (False, True):
        repo = make_repo(f'busy-{flag}')
        app, client = app_for(repo, tmp_path, flag, audio_bytes)
        exercise = client.post('/api/exercises').json()
        client.post(f"/api/exercises/{exercise['id']}/conversation/listened")
        original = SQLProgressRepository.conversation
        monkeypatch.setattr(SQLProgressRepository, 'conversation', lambda self, *a, **k: (time.sleep(.6), original(self, *a, **k))[1])

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t') as http:
                slow = asyncio.create_task(http.get(f"/api/exercises/{exercise['id']}/conversation"))
                started = time.perf_counter()
                await asyncio.sleep(.1)             # a timer on the same loop: late if a synchronous section freezes it
                await http.get('/api/health')
                latencies[flag] = time.perf_counter() - started
                await slow
        asyncio.run(run())
        monkeypatch.undo()
    assert latencies[False] > .5            # the synchronous section froze the loop (timer and health check both waited)
    assert latencies[True] < .3             # offloaded: the timer and the health check are served at once


def test_errors_raised_inside_the_threads_keep_their_status(make_repo, tmp_path, audio_bytes):
    repo = make_repo('errors')
    app, client = app_for(repo, tmp_path, True, audio_bytes)
    missing = '00000000-0000-4000-8000-000000000099'
    assert client.get(f'/api/exercises/{missing}/conversation').status_code == 404
    assert client.post(f'/api/exercises/{missing}/conversation/listened').status_code == 404
    assert client.post(f'/api/attempts/{missing}/assess', json={'text': 'x', 'confirmed': True}).status_code == 404
    assert client.post(f'/api/exercises/{missing}/coach-audio', json={'cue_id': 'summary'}).status_code == 404


def test_the_request_context_travels_into_the_thread():
    from contextvars import ContextVar
    var = ContextVar('learner', default='none')

    async def run():
        var.set('learner-a')
        return await Offload(True).run(lambda: (var.get(), threading.current_thread() is threading.main_thread()))
    assert asyncio.run(run()) == ('learner-a', False)


def test_a_cancelled_request_still_closes_the_lease_connection():
    closed = threading.Event()

    class Connection:
        def close(self):
            time.sleep(.1)
            closed.set()

    async def run():
        task = asyncio.create_task(Offload(True).release(Connection()))
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(.3)
    asyncio.run(run())
    assert closed.is_set()


def test_with_the_flag_off_the_call_is_inline():
    async def run():
        return await Offload(False).run(lambda: threading.current_thread() is threading.main_thread())
    assert asyncio.run(run()) is True
