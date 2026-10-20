"""AUD-70: per-learner locks. Same-learner serialization, cross-learner concurrency, revision guarantees,
cancellation and cleanup. Every HTTP scenario runs with the flag off (legacy global lock) and on."""
import asyncio
import io
import math
import struct
import wave
from time import perf_counter

import httpx
import pytest

from app.config import Settings
from app.locking import LearnerLocks
from app.main import create_app
from app.models import Transcription
from conftest import FakeProvider
from lock_concurrency import SlowProvider, wav_bytes
from test_auth import auth_response, settings_for
from test_conversation import assessment
from test_persistence import repository_factory  # noqa: F401  (fixture)

DELAY = .3


# --------------------------------------------------------------------------- LearnerLocks unit tests
def keyed(enabled=True):
    current = {'key': 'a'}
    return LearnerLocks(enabled, lambda: current['key']), current


async def hold(lock, current, key, log, label, seconds=.05):
    current['key'] = key
    async with lock:
        log.append(('in', label)); await asyncio.sleep(seconds); log.append(('out', label))


def test_distinct_keys_run_concurrently_and_same_key_is_fifo():
    async def run():
        lock, current = keyed(); log = []
        started = perf_counter()
        await asyncio.gather(hold(lock, current, 'a', log, 'a1'), hold(lock, current, 'b', log, 'b1'))
        parallel = perf_counter() - started
        log.clear()
        await asyncio.gather(*(hold(lock, current, 'a', log, f'a{i}', .02) for i in range(4)))
        return parallel, log, lock.active_keys()
    parallel, log, remaining = asyncio.run(run())
    assert parallel < .09                                                   # ~1 hold, not 2
    assert log == [(d, f'a{i}') for i in range(4) for d in ('in', 'out')]    # strict FIFO, never overlapping
    assert remaining == 0


def test_disabled_flag_is_exactly_one_global_lock():
    async def run():
        lock, current = keyed(enabled=False); log = []
        started = perf_counter()
        await asyncio.gather(hold(lock, current, 'a', log, 'a'), hold(lock, current, 'b', log, 'b'))
        return perf_counter() - started
    assert asyncio.run(run()) >= .095


def test_cancelled_waiter_is_unwound_and_does_not_block_others():
    async def run():
        lock, current = keyed(); log = []
        holder = asyncio.create_task(hold(lock, current, 'a', log, 'holder', .1))
        await asyncio.sleep(.01)
        waiter = asyncio.create_task(hold(lock, current, 'a', log, 'waiter'))
        await asyncio.sleep(.01)
        assert lock.queue_depth() == 2
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert lock.queue_depth() == 1
        follower = asyncio.create_task(hold(lock, current, 'a', log, 'follower', .01))
        await asyncio.gather(holder, follower)
        return log, lock.active_keys()
    log, remaining = asyncio.run(run())
    assert ('in', 'waiter') not in log and ('in', 'follower') in log and remaining == 0


def test_cancelled_holder_releases_and_next_waiter_proceeds():
    async def run():
        lock, current = keyed(); log = []
        holder = asyncio.create_task(hold(lock, current, 'a', log, 'holder', 5))
        await asyncio.sleep(.01)
        nxt = asyncio.create_task(hold(lock, current, 'a', log, 'next', .01))
        await asyncio.sleep(.01)
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
        await asyncio.wait_for(nxt, 1)
        return log, lock.active_keys()
    log, remaining = asyncio.run(run())
    assert ('in', 'next') in log and ('out', 'holder') not in log and remaining == 0


def test_exception_in_critical_section_releases():
    async def run():
        lock, current = keyed()
        with pytest.raises(ValueError):
            async with lock:
                raise ValueError('provider failed')
        async with asyncio.timeout(.5):
            async with lock:
                pass
        return lock.active_keys()
    assert asyncio.run(run()) == 0


def test_lock_table_does_not_grow_with_many_learners():
    async def run():
        lock, current = keyed(); log = []
        for i in range(2000):
            current['key'] = f'learner-{i}'
            async with lock:
                pass
        # And concurrently, including cancelled arrivals.
        tasks = [asyncio.create_task(hold(lock, current, f'c{i % 50}', log, i, .001)) for i in range(300)]
        await asyncio.sleep(.002)
        for task in tasks[::3]:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return lock.active_keys()
    assert asyncio.run(run()) == 0


def test_release_without_acquire_is_rejected():
    lock, _ = keyed()
    with pytest.raises(RuntimeError):
        lock.release()


# --------------------------------------------------------------------------- HTTP scenarios
def head(token): return {'Authorization': 'Bearer ' + token}


class Slow(SlowProvider):
    assess_calls = 0
    assess_delay = 0.0

    async def assess(self, content, turns):
        self.assess_calls += 1
        await asyncio.sleep(self.assess_delay)
        return assessment(content, turns)


async def two_learner_app(repository, per_learner, delay=DELAY):
    import tempfile
    from pathlib import Path
    provider = Slow(); provider.delay = delay; provider.assess_delay = delay
    async def speak(text, speech_rate=.9): return await provider.speech(None)
    provider.speak = speak
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(Path(tempfile.mkdtemp(prefix='audli-pl-')), per_learner_locks=per_learner), provider,
                     repository=repository, auth_client=auth)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver')
    exercises = {}
    for token, name in (('valid-a', 'Alice'), ('valid-b', 'Bob')):
        assert (await client.put('/api/profile', json={'name': name, 'goal': 'Meetings'}, headers=head(token))).status_code == 200
        exercise = (await client.post('/api/exercises', headers=head(token))).json()
        assert (await client.post(f"/api/exercises/{exercise['id']}/conversation/listened", headers=head(token))).status_code == 200
        exercises[token] = exercise['id']
    return app, client, auth, provider, exercises


async def upload(client, token, exercise_id):
    started = perf_counter()
    response = await client.post(f'/api/exercises/{exercise_id}/attempts', headers=head(token), files={'audio': ('a.wav', wav_bytes(), 'audio/wav')})
    assert response.status_code == 200, response.text
    return perf_counter() - started, response.json()


@pytest.mark.parametrize('per_learner', [False, True])
def test_same_learner_requests_stay_serialized_in_both_modes(repository_factory, per_learner):
    async def run():
        app, client, auth, provider, ex = await two_learner_app(repository_factory(None), per_learner)
        started = perf_counter()
        await asyncio.gather(upload(client, 'valid-a', ex['valid-a']), upload(client, 'valid-a', ex['valid-a']))
        wall = perf_counter() - started
        remaining = app.state.locks.active_keys()
        await client.aclose(); await auth.aclose(); app.state.repository.close()
        return wall, remaining
    wall, remaining = asyncio.run(run())
    assert wall >= 1.8 * DELAY and remaining == 0, (wall, remaining)


@pytest.mark.parametrize('per_learner, parallel', [(False, False), (True, True)])
def test_different_learners_overlap_only_when_enabled(repository_factory, per_learner, parallel):
    async def run():
        app, client, auth, provider, ex = await two_learner_app(repository_factory(None), per_learner)
        started = perf_counter()
        (_, a), (_, b) = await asyncio.gather(upload(client, 'valid-a', ex['valid-a']), upload(client, 'valid-b', ex['valid-b']))
        wall = perf_counter() - started
        # Each learner's own data stays their own.
        mine = (await client.get(f"/api/exercises/{ex['valid-a']}/conversation", headers=head('valid-a'))).json()
        theirs = await client.get(f"/api/exercises/{ex['valid-a']}/conversation", headers=head('valid-b'))
        remaining = app.state.locks.active_keys()
        await client.aclose(); await auth.aclose(); app.state.repository.close()
        return wall, a['id'] != b['id'], mine['state'], theirs.status_code, remaining
    wall, distinct, state, cross_status, remaining = asyncio.run(run())
    assert distinct and state == 'AWAITING_SUMMARY' and cross_status in (403, 404) and remaining == 0
    assert (wall < 1.5 * DELAY) is parallel, wall


@pytest.mark.parametrize('per_learner', [False, True])
def test_duplicate_assessment_for_one_learner_runs_the_evaluator_and_adaptation_once(repository_factory, per_learner):
    async def run():
        app, client, auth, provider, ex = await two_learner_app(repository_factory(None), per_learner)
        _, attempt = await upload(client, 'valid-a', ex['valid-a'])
        body = {'text': attempt['transcription']['text'], 'confirmed': True, 'followup_id': None}
        url = f"/api/attempts/{attempt['id']}/assess"
        first, second = await asyncio.gather(client.post(url, json=body, headers=head('valid-a')), client.post(url, json=body, headers=head('valid-a')))
        profile = (await client.get('/api/profile', headers=head('valid-a'))).json()['profile']
        history = (await client.get('/api/history', headers=head('valid-a'))).json()
        calls = provider.assess_calls
        await client.aclose(); await auth.aclose(); app.state.repository.close()
        return first, second, calls, profile['completed_attempts'], history
    first, second, calls, completed, history = asyncio.run(run())
    assert first.status_code == second.status_code == 200
    assert first.json()['state'] == second.json()['state'] == 'GIVING_FEEDBACK'
    assert calls == 1 and completed == 1                                   # evaluator and adaptation applied once
    assert len(history) == 1


@pytest.mark.parametrize('per_learner', [False, True])
def test_cancelled_waiter_and_cancelled_holder_do_not_wedge_the_learner(repository_factory, per_learner):
    async def run():
        app, client, auth, provider, ex = await two_learner_app(repository_factory(None), per_learner, delay=.4)
        holder = asyncio.create_task(upload(client, 'valid-a', ex['valid-a']))
        await asyncio.sleep(.05)
        waiter = asyncio.create_task(upload(client, 'valid-a', ex['valid-a']))
        await asyncio.sleep(.05)
        waiter.cancel(); await asyncio.gather(waiter, return_exceptions=True)
        holder.cancel(); await asyncio.gather(holder, return_exceptions=True)      # cancelled mid-provider-call
        started = perf_counter()
        seconds, _ = await asyncio.wait_for(upload(client, 'valid-a', ex['valid-a']), 5)   # next request proceeds promptly
        remaining = app.state.locks.active_keys()
        await client.aclose(); await auth.aclose(); app.state.repository.close()
        return perf_counter() - started, remaining
    elapsed, remaining = asyncio.run(run())
    assert elapsed < 1.0 and remaining == 0, (elapsed, remaining)


@pytest.mark.parametrize('per_learner', [False, True])
def test_checkpoint_revision_guarantee_is_unchanged_under_concurrency(repository_factory, tmp_path, audio_bytes, per_learner):
    """Two simultaneous /heard calls with the same revision: exactly one advances the lesson, one gets 409."""
    from conftest import ASGIClient
    repo = repository_factory()
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()
    async def speak(text, speech_rate=.9): return audio_bytes
    provider.speak = speak
    client = ASGIClient(create_app(Settings(data_dir=tmp_path, per_learner_locks=per_learner, _env_file=None), provider, repo), provider)
    lesson = client.post('/api/lessons/start').json()
    upload_ = client.post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']}, files={'audio': ('r.wav', audio_bytes, 'audio/wav')}).json()
    lesson = client.post(f"/api/lessons/{lesson['id']}/answer", json={'revision': upload_['revision']}).json()
    assert lesson['phase'] == 'WELCOME_ACK'
    async def both():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://testserver') as http:
            return await asyncio.gather(*(http.post(f"/api/lessons/{lesson['id']}/heard", json={'revision': lesson['revision']}) for _ in range(2)))
    results = asyncio.run(both())
    assert sorted(r.status_code for r in results) == [200, 409]
    latest = client.get(f"/api/lessons/{lesson['id']}").json()
    assert latest['revision'] == lesson['revision'] + 1
    assert client.app.state.locks.active_keys() == 0
