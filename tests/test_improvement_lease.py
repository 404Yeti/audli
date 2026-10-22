"""The account-lease exemption for Review coaching is exactly one method and one path shape (AUD-77).

Authentication and ownership still apply first; the exemption only means the account-wide lease is not held across the
provider call. The handler takes short transactions of its own and never the in-process or database account locks.
"""
import ast
import asyncio
import inspect
import re
import time
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app import auth, improvement_api
from test_auth import accounts_with, USER_A, USER_B  # noqa: F401
from test_persistence import repository_factory  # noqa: F401
from test_review_improvement import Coach, seeded, ANSWER, post, Clock

ID = str(uuid4())
EXEMPT = f'/api/exercises/{ID}/review/improvement'


def test_exactly_one_new_route_is_exempt_and_the_telemetry_exemption_is_unchanged():
    assert auth.LEASE_EXEMPT_PATHS == frozenset({'/api/telemetry/turn'})
    assert len(auth.LEASE_EXEMPT_ROUTES) == 1 and auth.LEASE_EXEMPT_ROUTES[0][0] == 'POST'
    assert isinstance(auth.LEASE_EXEMPT_ROUTES, tuple)




@pytest.mark.parametrize('method', ['PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
def test_other_methods_on_the_same_path_are_not_exempt(method):
    assert auth.lease_exempt(method, EXEMPT) is False


NEAR_MISSES = [
    EXEMPT + '/', EXEMPT + '/x', EXEMPT + 's', EXEMPT.replace('/improvement', '/improvement.json'),
    EXEMPT.replace('/review/improvement', '/review'), EXEMPT.replace('/review/improvement', '/improvement'),
    EXEMPT.replace('/review/', '/Review/'), EXEMPT.replace('/api/', '/API/'), EXEMPT.replace('/api/', '/api//'),
    EXEMPT.replace('/exercises/', '/exercises//'), EXEMPT.replace(ID, ID.upper()), EXEMPT.replace(ID, 'not-a-uuid'),
    EXEMPT.replace(ID, ID[:-1]), EXEMPT.replace(ID, ID + '0'), EXEMPT.replace(ID, '..'), EXEMPT.replace(ID, ID + '/../' + ID),
    EXEMPT + ' ', ' ' + EXEMPT, EXEMPT + ';x=1', EXEMPT + '%2F', EXEMPT + '\n', '/x' + EXEMPT,
    f'/api/exercises/{ID}/attempts', f'/api/exercises/{ID}/coach-audio', f'/api/exercises/{ID}/conversation/listened',
    f'/api/exercises/{ID}/review/improvement/audio', '/api/exercises/review/improvement', '/api/review/improvement',
    f'/api/attempts/{ID}/review/improvement', f'/api/lessons/{ID}/review/improvement',
]


@pytest.mark.parametrize('path', NEAR_MISSES)
def test_near_miss_paths_are_not_exempt(path):
    assert auth.lease_exempt('POST', path) is False, path


def test_handler_reaches_no_account_lock_and_never_the_daily_allowance():
    tree = ast.parse(inspect.getsource(improvement_api))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    forbidden = {'lock', 'timed_lock', 'LearnerLocks', 'limits', 'admit', 'admit_exercise', 'admit_legacy', 'LessonLimits',
                 'save_profile', 'complete', 'save_lesson', 'adapt', 'adapt_final', 'evaluate'}
    assert not names & forbidden, names & forbidden


@pytest.fixture
def accounts_on(tmp_path, repository_factory):
    yield from accounts_with(tmp_path, repository_factory, Coach, review_improvement_enabled=True)


def test_exactly_one_route_matches_the_exempt_shape_and_authentication_still_comes_first(accounts_on, exercise):
    a, b, _ = accounts_on
    app = a.app
    routes = [r for r in app.routes if hasattr(r, 'path_regex') and r.path_regex.match(EXEMPT)]
    assert len(routes) == 1 and routes[0].methods == {'POST'} and routes[0].endpoint.__name__ == 'improve'
    from starlette.testclient import TestClient  # noqa: F401
    from conftest import ASGIClient
    anonymous = ASGIClient(app, a.provider)
    assert anonymous.post(EXEMPT).status_code == 401
    forged = a.post(EXEMPT, headers={'Authorization': 'Bearer forged'})
    assert forged.status_code == 401
    assert a.provider.coached == []
    # Authenticated but not the owner: ownership is still enforced inside the handler.
    a.get('/api/profile'); b.get('/api/profile')
    owner = app.state.repository.for_learner(USER_A)
    _, ids = seeded(owner, exercise)
    assert b.post(f'/api/exercises/{ids[0]}/review/improvement').status_code == 404


# ------------------------------------------------------------------------------------------ PostgreSQL: the live lease
def lease_key(user):
    return int.from_bytes(sha256(('audli-account:' + user).encode()).digest()[:8], 'big', signed=True)


def pg(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')


def test_postgres_the_lease_is_not_held_during_the_provider_call_and_not_needed_to_start(accounts_on, exercise):
    a, b, _ = accounts_on
    app = a.app
    pg(app.state.repository)
    a.get('/api/profile'); b.get('/api/profile')
    owner = app.state.repository.for_learner(USER_A)
    app.state.review_clock = Clock()
    _, ids = seeded(owner, exercise, count=2)
    probes = []

    def during():
        with app.state.repository.engine.begin() as connection:  # Another connection: is the account lease free right now?
            probes.append(connection.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': lease_key(USER_A)}).scalar())
    a.provider.during = during
    assert a.post(f'/api/exercises/{ids[0]}/review/improvement').json()['improvement']['state'] == 'ready'
    assert probes == [True]  # The provider ran with the account lease free: nothing account-wide was held.

    async def held():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as client:
            blocker = app.state.repository.engine.connect(); transaction = blocker.begin()
            blocker.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lease_key(USER_A)})
            headers = {'Authorization': 'Bearer valid-a'}
            try:
                other = None
                try:  # An ordinary mutating route still queues behind the held lease...
                    other = await asyncio.wait_for(client.put('/api/profile/accent-preferences', json={'accents': [], 'status': 'no_preference'}, headers=headers), .6)
                except asyncio.TimeoutError:
                    other = 'queued'
                # ...while the improvement endpoint, which takes only its own short transactions, proceeds.
                mine = await asyncio.wait_for(client.post(f'/api/exercises/{ids[1]}/review/improvement', headers=headers), 5)
            finally:
                transaction.rollback(); blocker.close()
            return other, mine.status_code
    other, status = asyncio.run(held())
    assert other == 'queued' and status == 200


def test_predicate_accepts_only_the_exact_shapes():
    assert auth.lease_exempt('POST', EXEMPT) is True
    assert auth.lease_exempt('POST', '/api/telemetry/turn') is True
    assert auth.lease_exempt('POST', '/api/profile') is False and auth.lease_exempt('PUT', '/api/profile') is False
