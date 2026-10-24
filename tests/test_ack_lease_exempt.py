"""AUD-87: the acknowledgment cue stops queueing behind the account lease (AUDLI_ACK_LEASE_EXEMPT, default off)."""
import asyncio
import inspect
import time
import uuid

import httpx
import pytest
from sqlalchemy import event

from app import auth
from app.ack_cache import AckCache
from app.config import Settings
from app.main import create_app
from conftest import FakeProvider
from test_auth import USER_A, auth_response, settings_for
from test_auth_lease_exemption import client_for, hold_lease, HEADERS
from test_persistence import repository_factory  # noqa: F401  (fixture)
from test_state_only_ready import dump

CUE = '/api/recognition/acknowledgment-audio'


def build(tmp_path, repository, flag, **settings):
    provider = FakeProvider()
    provider.spoken = 0

    async def speak(text, speech_rate=.9):
        provider.spoken += 1
        await asyncio.sleep(.05)
        return await provider.speech(None)
    provider.speak = speak
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, ack_lease_exempt=flag, latency_aggregates=True, **settings), provider, repository=repository, auth_client=http)
    return app, provider, http


def postgres_only(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')


def test_off_by_default():
    assert Settings(_env_file=None).ack_lease_exempt is False
    assert auth.ACK_ROUTE == ('POST', CUE) and auth.LEASE_EXEMPT_PATHS == frozenset({'/api/telemetry/turn'})   # the reviewed set did not widen


# ---------------------------------------------------------------------------------------------- the lease
def served_while_lease_is_held(tmp_path, repo, flag):
    app, provider, http = build(tmp_path, repo, flag)

    async def run():
        async with client_for(app) as client:
            blocker, transaction = hold_lease(repo)
            try:
                cue = asyncio.create_task(client.post(CUE, json={'cue': 'assessment'}, headers=HEADERS))
                other = asyncio.create_task(client.post('/api/recognition/retry-audio', headers=HEADERS))
                await asyncio.sleep(1.0)
                finished = (cue.done(), other.done())
            finally:
                transaction.rollback(); blocker.close()
            return finished, (await cue).status_code, (await other).status_code
    result = asyncio.run(run())
    app.state.repository.close(); asyncio.run(http.aclose())
    return result


def test_with_the_flag_the_cue_is_served_while_the_lease_is_held_and_other_posts_still_queue(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    (cue_done, other_done), cue_status, other_status = served_while_lease_is_held(tmp_path, repo, True)
    assert cue_done and not other_done            # the cue did not wait; an ordinary POST is still queued behind the lease
    assert cue_status == 200 and other_status == 200


def test_without_the_flag_the_cue_still_queues_behind_the_lease(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    (cue_done, other_done), cue_status, _ = served_while_lease_is_held(tmp_path, repo, False)
    assert not cue_done and not other_done and cue_status == 200


# ---------------------------------------------------------------------------------------------- it cannot mutate learner state
def test_serving_the_cue_runs_only_selects_and_changes_no_row(tmp_path, repository_factory):
    repo = repository_factory(None)
    app, provider, http = build(tmp_path, repo, True)
    statements = []

    @event.listens_for(repo.engine, 'before_cursor_execute')
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    async def run():
        async with client_for(app) as client:
            await client.get('/api/profile', headers=HEADERS)               # create the account's rows first
            before = dump(repo)
            statements.clear()
            for cue in ('assessment', 'followup', 'reflection', 'assessment'):           # cold and warm cache, every cue
                response = await client.post(CUE, json={'cue': cue}, headers=HEADERS)
                assert response.status_code == 200 and response.content and response.headers['x-audli-tutor-fallback'] == 'false'
            return before, dump(repo)
    before, after = asyncio.run(run())
    assert before == after                                                     # every table identical
    assert statements and set(statements) <= {'SELECT', 'SHOW'}, set(statements)
    assert provider.spoken == 3                                                # one provider call per distinct cue
    app.state.repository.close(); asyncio.run(http.aclose())


def test_the_exempt_handler_has_no_write_path():
    from app import lesson_api
    source = inspect.getsource(lesson_api)
    start = source.index('async def static_acknowledgment')
    handler = source[start:source.index("@app.post('/api/recognition/acknowledgment-audio')", start)]
    for forbidden in ('cache_lesson_audio', 'transaction', 'save_', 'audio_blob', 'add_', 'complete', 'commit'):
        assert forbidden not in handler, forbidden


# ---------------------------------------------------------------------------------------------- billing and authorization
def test_a_miss_is_admitted_against_the_allowance_and_a_hit_is_not(tmp_path, repository_factory):
    repo = repository_factory(None)
    app, provider, http = build(tmp_path, repo, True)
    admitted = []
    original = app.state.limits.admit
    app.state.limits.admit = lambda *args, **kw: (admitted.append(args), original(*args, **kw))[1]

    async def run():
        async with client_for(app) as client:
            for _ in range(3):
                assert (await client.post(CUE, json={'cue': 'assessment'}, headers=HEADERS)).status_code == 200
    asyncio.run(run())
    assert len(admitted) == 1 and admitted[0][0] == 'practice'
    app.state.repository.close(); asyncio.run(http.aclose())


def test_a_refused_miss_is_refused_like_before(tmp_path, repository_factory):
    from datetime import datetime, timedelta, timezone
    from app.lesson_limit import LimitRefused
    repo = repository_factory(None)
    app, provider, http = build(tmp_path, repo, True)

    def refuse(*args, **kw):
        now = datetime.now(timezone.utc)
        raise LimitRefused('lesson_cooldown', now, now + timedelta(hours=1))
    app.state.limits.admit = refuse

    async def run():
        async with client_for(app) as client:
            return (await client.post(CUE, json={'cue': 'assessment'}, headers=HEADERS)).status_code
    assert asyncio.run(run()) == 429 and provider.spoken == 0
    app.state.repository.close(); asyncio.run(http.aclose())


@pytest.mark.parametrize('headers, status', [({}, 401), ({'Authorization': 'Bearer invalid'}, 401), ({'Authorization': 'Bearer outage'}, 503)])
def test_authentication_still_comes_first(tmp_path, repository_factory, headers, status):
    repo = repository_factory(None)
    app, provider, http = build(tmp_path, repo, True)

    async def run():
        async with client_for(app) as client:
            return (await client.post(CUE, json={'cue': 'assessment'}, headers=headers)).status_code
    assert asyncio.run(run()) == status and provider.spoken == 0
    app.state.repository.close(); asyncio.run(http.aclose())


@pytest.mark.parametrize('path', [CUE + '/', CUE.upper(), CUE + '.json', CUE.replace('recognition', 'Recognition'), '/api' + CUE])
def test_only_the_exact_route_is_exempt(path):
    assert ('POST', path) != auth.ACK_ROUTE


# ---------------------------------------------------------------------------------------------- concurrency
def test_concurrent_cues_generate_once_and_share_the_result(tmp_path, repository_factory):
    repo = repository_factory(None)
    app, provider, http = build(tmp_path, repo, True)

    async def run():
        async with client_for(app) as client:
            responses = await asyncio.gather(*(client.post(CUE, json={'cue': 'assessment'}, headers=HEADERS) for _ in range(20)))
            return {r.status_code for r in responses}, {r.content for r in responses}
    codes, bodies = asyncio.run(run())
    assert codes == {200} and len(bodies) == 1 and provider.spoken == 1
    app.state.repository.close(); asyncio.run(http.aclose())


def test_the_cue_does_not_wait_for_a_long_request_that_holds_the_lease(tmp_path, repository_factory):
    """The reported problem: an assessment-like request holds the lease for seconds; the cue must return meanwhile."""
    repo = repository_factory(None); postgres_only(repo)
    app, provider, http = build(tmp_path, repo, True)

    async def run():
        async with client_for(app) as client:
            blocker, transaction = hold_lease(repo)
            try:
                started = time.perf_counter()
                response = await client.post(CUE, json={'cue': 'assessment'}, headers=HEADERS)
                elapsed = time.perf_counter() - started
            finally:
                transaction.rollback(); blocker.close()
            return response.status_code, elapsed
    status, elapsed = asyncio.run(run())
    assert status == 200 and elapsed < 2.0
    app.state.repository.close(); asyncio.run(http.aclose())


# ---------------------------------------------------------------------------------------------- the cache
def test_cache_is_single_flight_per_key_and_keyed_by_voice():
    calls = []

    async def run():
        cache = AckCache()

        async def factory(tag):
            calls.append(tag); await asyncio.sleep(.02)
            return tag, True
        same = await asyncio.gather(*(cache.get(('voice-a', 'cue'), lambda: factory('a')) for _ in range(10)))
        other = await cache.get(('voice-b', 'cue'), lambda: factory('b'))          # a different voice never reuses another's audio
        again = await cache.get(('voice-a', 'cue'), lambda: factory('never'))
        return same, other, again
    same, other, again = asyncio.run(run())
    assert calls == ['a', 'b'] and {value for value, _ in same} == {'a'} and other == ('b', False) and again == ('a', True)


def test_fallback_results_and_failures_are_not_cached_and_the_size_is_bounded():
    async def run():
        cache = AckCache(limit=2)
        order = []

        async def fallback():
            order.append('fallback'); return 'x', False
        await cache.get('k', fallback); await cache.get('k', fallback)

        async def failing():
            raise RuntimeError('provider down')
        results = await asyncio.gather(*(cache.get('f', failing) for _ in range(3)), return_exceptions=True)

        async def good(value):
            return value, True
        for key in 'abc':
            await cache.get(key, lambda key=key: good(key))
        return order, results, len(cache)
    order, results, size = asyncio.run(run())
    assert order == ['fallback', 'fallback'] and all(isinstance(r, RuntimeError) for r in results) and size == 2


def test_a_cancelled_waiter_does_not_cancel_the_shared_generation():
    async def run():
        cache = AckCache()

        async def slow():
            await asyncio.sleep(.1)
            return 'audio', True
        first = asyncio.create_task(cache.get('k', slow))
        second = asyncio.create_task(cache.get('k', slow))
        await asyncio.sleep(.01)
        first.cancel()
        return await second, len(cache)
    (value, _), size = asyncio.run(run())
    assert value == 'audio' and size == 1
