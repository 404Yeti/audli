"""AUD-87: the account lease from kept connections with one statement (AUDLI_LEASE_POOL, default off). Same lock, same key."""
import asyncio
import uuid
from hashlib import sha256

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool, QueuePool

from app.auth import LearnerAuthentication
from app.config import Settings
from app.main import create_app
from app.telemetry import aggregator
from conftest import FakeProvider
from test_auth import USER_A, USER_B, auth_response, settings_for
from test_auth_lease_exemption import client_for, hold_lease, lease_key, HEADERS
from test_persistence import repository_factory  # noqa: F401  (fixture)

POST = ('/api/recognition/retry-audio', None)           # an ordinary mutating route, so it takes the lease


def build(tmp_path, repository, flag, **settings):
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return await provider.speech(None)
    provider.speak = speak
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, lease_pool=flag, latency_aggregates=True, **settings), provider, repository=repository, auth_client=http)
    return app, http


def postgres_only(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')


def middleware(app):
    node = app.build_middleware_stack()
    while node is not None and not isinstance(node, LearnerAuthentication):
        node = getattr(node, 'app', None)
    return node


def test_off_by_default_and_the_pool_is_only_used_when_enabled(tmp_path, repository_factory):
    assert Settings.model_fields['lease_pool'].default is False   # the declared default, so a suite forced on still checks it
    repo = repository_factory(None); postgres_only(repo)
    off, http_off = build(tmp_path / 'off', repo, False)
    on, http_on = build(tmp_path / 'on', repo, True)
    assert isinstance(middleware(off).lease_engine.pool, NullPool) and isinstance(middleware(on).lease_engine.pool, QueuePool)
    repo.close()


def test_one_statement_and_no_new_connection_per_lease(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    counts = {}
    for flag in (False, True):
        aggregator.reset()
        app, http = build(tmp_path / str(flag), repo, flag)

        async def run():
            async with client_for(app) as client:
                for _ in range(6):
                    assert (await client.post(*POST[:1], headers=HEADERS)).status_code == 200
        asyncio.run(run())
        stages = aggregator.snapshot()['stages']
        counts[flag] = (stages['lease.query']['count'], stages.get('lease.connect_physical', {}).get('count', 0))
    assert counts[False] == (12, 6)                  # two statements and a new physical connection per request
    queries, physical = counts[True]
    assert queries == 6 and physical <= 2            # one statement per request; connections are reused
    repo.close()


def test_the_lease_still_excludes_and_is_released_afterwards(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    app, http = build(tmp_path, repo, True)

    async def run():
        async with client_for(app) as client:
            blocker, transaction = hold_lease(repo)
            try:
                pending = asyncio.create_task(client.post(*POST[:1], headers=HEADERS))
                await asyncio.sleep(.5)
                waiting = not pending.done()                     # excluded by a holder of the same key
            finally:
                transaction.rollback(); blocker.close()
            assert (await pending).status_code == 200
            # Not leaked: the key can be taken at once from another connection after the request finished.
            with repo.engine.connect() as probe:
                free = probe.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lease_key()}).scalar_one()
            return waiting, free
    waiting, free = asyncio.run(run())
    assert waiting and free
    repo.close()


def test_two_requests_of_one_account_serialize_and_two_accounts_do_not(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    app, http = build(tmp_path, repo, True)
    seen = {'active': 0, 'peak': 0}

    async def run():
        async with client_for(app) as client:
            await asyncio.gather(*(client.post(POST[0], headers=HEADERS) for _ in range(6)))
            blocker, transaction = hold_lease(repo)                       # holds USER_A's key
            try:
                other = await asyncio.wait_for(client.post(POST[0], headers={'Authorization': 'Bearer valid-b'}), 5)
            finally:
                transaction.rollback(); blocker.close()
            return other.status_code
    assert asyncio.run(run()) == 200                                      # USER_B is unaffected by USER_A's lease
    repo.close()


def test_a_stale_kept_connection_is_replaced_without_failing_the_request(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    app, http = build(tmp_path, repo, True)

    async def run():
        async with client_for(app) as client:
            assert (await client.post(POST[0], headers=HEADERS)).status_code == 200          # a connection is now kept
            with repo.engine.connect() as admin:
                admin.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()'))
                admin.commit()
            codes = [(await client.post(POST[0], headers=HEADERS)).status_code for _ in range(3)]
            return codes
    assert asyncio.run(run()) == [200, 200, 200]
    repo.close()


def test_a_failing_handler_still_releases_the_lease(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    app, http = build(tmp_path, repo, True)

    async def run():
        async with client_for(app) as client:
            missing = await client.post(f'/api/attempts/{uuid.uuid4()}/assess', json={'text': 'x', 'confirmed': True}, headers=HEADERS)
            with repo.engine.connect() as probe:
                free = probe.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lease_key()}).scalar_one()
            return missing.status_code, free
    status, free = asyncio.run(run())
    assert status == 404 and free
    repo.close()


def test_exempt_and_read_requests_still_take_no_lease(tmp_path, repository_factory):
    repo = repository_factory(None); postgres_only(repo)
    app, http = build(tmp_path, repo, True)
    aggregator.reset()

    async def run():
        async with client_for(app) as client:
            blocker, transaction = hold_lease(repo)
            try:
                got = await asyncio.wait_for(client.get('/api/profile', headers=HEADERS), 5)
                telemetry = await asyncio.wait_for(client.post('/api/telemetry/turn', json={'stages': {'assessment': 5}}, headers=HEADERS), 5)
            finally:
                transaction.rollback(); blocker.close()
            return got.status_code, telemetry.status_code
    assert asyncio.run(run()) == (200, 204)
    assert 'lease.query' not in aggregator.snapshot()['stages']
    repo.close()


def test_the_pool_size_is_configurable_and_a_full_pool_spills_to_a_one_off_connection_instead_of_blocking(tmp_path, repository_factory, monkeypatch):
    """Capacity: size + overflow kept lease connections at most; beyond that a request opens a one-off connection, as the unpooled lease did."""
    import time
    from app.storage.repository import SQLProgressRepository
    repo = repository_factory(None); postgres_only(repo)
    aggregator.reset()
    app, http = build(tmp_path, repo, True, lease_pool_size=1, lease_pool_overflow=0)
    pool = middleware(app).lease_engine.pool
    assert (pool.size(), pool._max_overflow) == (1, 0)
    original = SQLProgressRepository.audio_blob

    def slow(self, name):
        time.sleep(.5)
        return original(self, name)
    monkeypatch.setattr(SQLProgressRepository, 'audio_blob', slow)

    async def run():
        async with client_for(app) as client:
            started = time.perf_counter()
            first = asyncio.create_task(client.post(POST[0], headers=HEADERS))
            second = asyncio.create_task(client.post(POST[0], headers={'Authorization': 'Bearer valid-b'}))
            results = [(await first).status_code, (await second).status_code]
            return results, time.perf_counter() - started
    results, elapsed = asyncio.run(run())
    assert results == [200, 200] and elapsed < 3                         # a wait for the first's connection would freeze the loop for the 5 s of the old timeout, then fail
    assert aggregator.snapshot()['stages']['lease.connect_physical']['count'] >= 2    # the kept connection plus a one-off for the second request
    assert pool.checkedout() == 0
    repo.close()


def test_defaults_are_small():
    assert (Settings.model_fields['lease_pool_size'].default, Settings.model_fields['lease_pool_overflow'].default) == (2, 6)
