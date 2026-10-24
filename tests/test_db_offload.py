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


# ----------------------------------------------------------------------------------------------- cancellation, cleanup, thread safety
def test_a_cancelled_request_waits_for_the_running_section_and_hands_back_its_result():
    order, received = [], []

    def section():
        time.sleep(.3)
        order.append('section finished')
        return 'result'

    async def run():
        task = asyncio.create_task(Offload(True).run(section, on_cancel=received.append))
        await asyncio.sleep(.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        order.append('cancellation delivered')
    asyncio.run(run())
    assert order == ['section finished', 'cancellation delivered'] and received == ['result']      # never released under a running section


def test_repeated_cancellation_and_failing_sections_leave_nothing_behind(recwarn):
    def failing():
        time.sleep(.1)
        raise RuntimeError('database down')

    async def run():
        task = asyncio.create_task(Offload(True).run(failing))
        await asyncio.sleep(.02)
        task.cancel(); await asyncio.sleep(.01); task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RuntimeError):
            await Offload(True).run(failing)
    asyncio.run(run())
    import gc; gc.collect()
    assert not [w for w in recwarn if 'never retrieved' in str(w.message)]


def test_no_repository_call_ever_holds_one_connection_while_taking_another(make_repo, tmp_path, audio_bytes, monkeypatch):
    """Offloaded sections run concurrently; a call that checked out a second connection while holding the first could exhaust the
    small pool (3 connections, no overflow) and deadlock. Prove the depth never exceeds one in a whole lesson, per thread."""
    from sqlalchemy import event
    for flag in (False, True):
        deterministic_ids(monkeypatch)
        repo = make_repo(f'nested-{flag}')
        depth, worst, held = threading.local(), [0], [0]

        @event.listens_for(repo.engine, 'checkout')
        def checked_out(dbapi_connection, record, proxy):
            depth.value = getattr(depth, 'value', 0) + 1
            worst[0] = max(worst[0], depth.value)

        @event.listens_for(repo.engine, 'checkin')
        def checked_in(dbapi_connection, record):
            depth.value = getattr(depth, 'value', 1) - 1
        _, client = app_for(repo, tmp_path, flag, audio_bytes)
        run_lesson(client, audio_bytes)
        assert worst[0] == 1, (flag, worst[0])
        pool = repo.engine.pool
        if hasattr(pool, 'checkedout'):
            assert pool.checkedout() == 0                      # every connection went back


def test_concurrent_accounts_stay_consistent_and_isolated(tmp_path, repository_factory, audio_bytes):
    import httpx
    from test_auth import USER_A, USER_B, auth_response, settings_for
    from test_auth_lease_exemption import client_for
    from test_conversation import assessment
    repo = repository_factory(None)
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return audio_bytes
    provider.speak = speak

    async def assess(content, turns):
        await asyncio.sleep(.2)
        return assessment(content, turns, {'inference': 'insufficient_evidence'} if len(turns) == 1 else {})
    provider.assess = assess
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, db_offload=True), provider, repository=repo, auth_client=http)

    async def learner(client, token):
        headers = {'Authorization': 'Bearer ' + token}
        exercise = (await client.post('/api/exercises', headers=headers)).json()
        await client.post(f"/api/exercises/{exercise['id']}/conversation/listened", headers=headers)
        followup, outcomes = None, []
        for _ in range(2):
            up = await client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('r.wav', audio_bytes, 'audio/wav')}, headers=headers)
            result = await client.post(f"/api/attempts/{up.json()['id']}/assess", headers=headers,
                                       json={'text': up.json()['transcription']['text'], 'confirmed': True, 'followup_id': followup['id'] if followup else None})
            outcomes.append((up.status_code, result.status_code))
            followup = result.json().get('active_followup')
        return exercise['id'], outcomes, (await client.get(f"/api/exercises/{exercise['id']}/conversation", headers=headers)).json()

    async def run():
        async with client_for(app) as client:
            return await asyncio.gather(learner(client, 'valid-a'), learner(client, 'valid-b'))
    (id_a, out_a, state_a), (id_b, out_b, state_b) = asyncio.run(run())
    assert id_a != id_b and out_a == out_b == [(200, 200), (200, 200)]
    assert state_a['state'] == state_b['state'] == 'GIVING_FEEDBACK' and state_a['result'] and state_b['result']
    assert repo.for_learner(USER_A).completed(id_a) and repo.for_learner(USER_B).completed(id_b)
    assert repo.for_learner(USER_A).exercise(id_b) is None and repo.for_learner(USER_B).exercise(id_a) is None      # no cross-account leakage
    repo.close(); asyncio.run(http.aclose())


def test_a_cancelled_request_keeps_the_account_lease_until_its_section_finished(tmp_path, repository_factory, monkeypatch):
    """The safety property of the offload: cancelling a request must not release the lease under a section still writing."""
    import httpx
    from sqlalchemy import text
    from test_auth import auth_response, settings_for
    from test_auth_lease_exemption import HEADERS, lease_key
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')
    original = SQLProgressRepository.exercise
    entered, release = threading.Event(), threading.Event()

    def slow(self, exercise_id):
        entered.set()
        assert release.wait(20)                               # the section stays "writing" until the test lets it finish
        return original(self, exercise_id)
    monkeypatch.setattr(SQLProgressRepository, 'exercise', slow)
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, db_offload=True), FakeProvider(), repository=repo, auth_client=http)
    held = []

    def lease_is_free():
        with repo.engine.connect() as probe:
            return probe.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lease_key()}).scalar_one()

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://t') as client:
            request = asyncio.create_task(client.post('/api/exercises/00000000-0000-4000-8000-000000000042/conversation/listened', headers=HEADERS))
            assert await asyncio.to_thread(entered.wait, 20)       # inside the section: lease held, section blocked
            request.cancel()
            await asyncio.sleep(.2)
            held.append(not await asyncio.to_thread(lease_is_free))     # cancelled, yet the lease is still held
            release.set()
            for _ in range(100):                                         # released once the section is over
                if await asyncio.to_thread(lease_is_free):
                    held.append(True)
                    break
                await asyncio.sleep(.05)
            with pytest.raises(asyncio.CancelledError):
                await request
    asyncio.run(run())
    assert held == [True, True]
    repo.close(); asyncio.run(http.aclose())
