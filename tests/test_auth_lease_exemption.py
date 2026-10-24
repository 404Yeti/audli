"""Security review of the per-account lease exemption (AUD-68).

Claims proven here:
  1. Exactly one path is exempt, and it maps to exactly one POST route whose handler cannot reach learner state.
  2. The exemption is only about the *lease*: authentication and the outer origin/size guard still apply first.
  3. No near-miss path (case, slashes, encodings, siblings) is exempt.
  4. Every other mutating route still queues behind a held lease (PostgreSQL only: the lease does not exist elsewhere).
"""
import ast
import asyncio
import inspect
import re
from hashlib import sha256

import httpx
import pytest
from sqlalchemy import text

from app import auth, telemetry_api
from app.main import create_app
from app.telemetry import aggregator
from conftest import FakeProvider
from test_auth import USER_A, auth_response, settings_for
from test_persistence import repository_factory  # noqa: F401  (fixture)

EXEMPT = '/api/telemetry/turn'
HEADERS = {'Authorization': 'Bearer valid-a'}


def build(tmp_path, repository):
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, latency_aggregates=True), FakeProvider(), repository=repository, auth_client=http)
    return app, http


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver')


# --------------------------------------------------------------------------- 1. structure
def test_exactly_one_exact_path_is_exempt():
    assert auth.LEASE_EXEMPT_PATHS == frozenset({EXEMPT})      # widening this set must be a deliberate, reviewed edit
    assert isinstance(auth.LEASE_EXEMPT_PATHS, frozenset)      # immutable at runtime


def test_exempt_path_is_one_post_route_and_its_module_cannot_touch_learner_state(tmp_path, repository_factory):
    app, http = build(tmp_path, repository_factory(None))
    routes = [r for r in app.routes if getattr(r, 'path', None) == EXEMPT]
    assert len(routes) == 1 and routes[0].methods == {'POST'} and routes[0].endpoint.__name__ == 'turn_report'
    # No other route's path pattern can also match the exempt path (a different handler must never inherit the exemption).
    matching = [r for r in app.routes if hasattr(r, 'path_regex') and r.path_regex.match(EXEMPT)]
    assert len(matching) == 1
    tree = ast.parse(inspect.getsource(telemetry_api))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {'typing', 'fastapi', 'pydantic', 'app.models', 'app.telemetry'}, imported   # no repository/lock/DB access
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'turn_report')
    body_nodes = [n for statement in handler.body for n in ast.walk(statement)]      # the executed body, not decorators/annotations
    used = {n.id for n in body_nodes if isinstance(n, ast.Name)} | {n.attr for n in body_nodes if isinstance(n, ast.Attribute)}
    assert used <= {'body', 'settings', 'aggregator', 'record', 'record_client', 'record_client_trace', 'latency_aggregates', 'stages', 'items', 'stage', 'elapsed', 'Response', 'status_code', 'turn', 'requests'}, used
    app.state.repository.close(); asyncio.run(http.aclose())


def test_telemetry_handler_only_writes_the_in_memory_aggregate(tmp_path, repository_factory):
    """Running it must not change any persisted learner state."""
    repo = repository_factory(None)
    app, http = build(tmp_path, repo)
    async def run():
        async with client_for(app) as client:
            before = (await client.get('/api/profile', headers=HEADERS)).json()
            assert (await client.post(EXEMPT, json={'stages': {'assessment': 5}}, headers=HEADERS)).status_code == 204
            return before, (await client.get('/api/profile', headers=HEADERS)).json(), (await client.get('/api/history', headers=HEADERS)).json()
    before, after, history = asyncio.run(run())
    assert before == after and history == []
    app.state.repository.close(); asyncio.run(http.aclose())


# --------------------------------------------------------------------------- 2. authentication and outer guards still apply
@pytest.mark.parametrize('token, status', [(None, 401), ('invalid', 401), ('outage', 503), ('anonymous', 401), ('unconfirmed', 401)])
def test_authentication_is_never_skipped(tmp_path, repository_factory, token, status):
    aggregator.reset()
    app, http = build(tmp_path, repository_factory(None))
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    async def run():
        async with client_for(app) as client:
            return await client.post(EXEMPT, json={'stages': {'assessment': 5}}, headers=headers)
    response = asyncio.run(run())
    assert response.status_code in (status, 403), response.text
    assert 'client.assessment' not in aggregator.snapshot()['stages']        # rejected requests record nothing
    app.state.repository.close(); asyncio.run(http.aclose())


def test_duplicate_or_malformed_authorization_headers_are_rejected(tmp_path, repository_factory):
    app, http = build(tmp_path, repository_factory(None))
    async def run():
        async with client_for(app) as client:
            twice = await client.post(EXEMPT, json={'stages': {'assessment': 5}}, headers=[('Authorization', 'Bearer valid-a'), ('Authorization', 'Bearer valid-a')])
            wrong = await client.post(EXEMPT, json={'stages': {'assessment': 5}}, headers={'Authorization': 'Basic valid-a'})
            return twice.status_code, wrong.status_code
    assert asyncio.run(run()) == (401, 401)
    app.state.repository.close(); asyncio.run(http.aclose())


def test_origin_guard_and_body_limit_still_run_before_the_exempt_route(tmp_path, repository_factory):
    app, http = build(tmp_path, repository_factory(None))
    async def run():
        async with client_for(app) as client:
            foreign = await client.post(EXEMPT, json={'stages': {'assessment': 5}}, headers=HEADERS | {'Origin': 'https://evil.example'})
            huge = await client.post(EXEMPT, content=b'{"stages":{"assessment":5}}' + b' ' * 50_000, headers=HEADERS | {'Content-Type': 'application/json'})
            extra = await client.post(EXEMPT, json={'stages': {'assessment': 5}, 'learner_id': USER_A}, headers=HEADERS)
            return foreign.status_code, huge.status_code, extra.status_code
    assert asyncio.run(run()) == (403, 413, 422)
    app.state.repository.close(); asyncio.run(http.aclose())


@pytest.mark.parametrize('method', ['GET', 'PUT', 'PATCH', 'DELETE'])
def test_other_methods_on_the_exempt_path_do_nothing(tmp_path, repository_factory, method):
    aggregator.reset()
    app, http = build(tmp_path, repository_factory(None))
    async def run():
        async with client_for(app) as client:
            return await client.request(method, EXEMPT, json={'stages': {'assessment': 5}}, headers=HEADERS)
    assert asyncio.run(run()).status_code == 405
    assert not [name for name in aggregator.snapshot()['stages'] if name.startswith('client.')]   # nothing was recorded
    app.state.repository.close(); asyncio.run(http.aclose())


# --------------------------------------------------------------------------- 3. near-miss paths are not exempt
VARIANTS = ['/api/telemetry/turn/', '/API/telemetry/turn', '/api/Telemetry/turn', '/api//telemetry/turn', '/api/telemetry//turn',
            '/api/telemetry/turn%2F', '/api/telemetry/turn.json', '/api/telemetry', '/api/telemetry/turns', '/api/./telemetry/turn',
            '/api/telemetry/turn/../profile', '/api/telemetry/turn%00', '/api/telemetry/turn ', '/api/telemetry/turn;x=1']


async def raw_post(app, raw_path, body=b'{"stages":{"assessment":5}}', token='valid-a'):
    """Send an ASGI request with exactly this path, bypassing any client-side URL normalization."""
    from urllib.parse import unquote
    sent, status = False, None
    async def receive():
        nonlocal sent
        if sent:
            return {'type': 'http.disconnect'}
        sent = True
        return {'type': 'http.request', 'body': body, 'more_body': False}
    async def send(message):
        nonlocal status
        if message['type'] == 'http.response.start':
            status = message['status']
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
             'path': unquote(raw_path), 'raw_path': raw_path.encode(), 'query_string': b'', 'root_path': '',
             'headers': [(b'host', b'testserver'), (b'content-type', b'application/json'), (b'content-length', str(len(body)).encode()),
                         (b'authorization', b'Bearer ' + token.encode())], 'client': ('127.0.0.1', 1), 'server': ('testserver', 80), 'state': {}}
    await app(scope, receive, send)
    return status


@pytest.mark.parametrize('variant', VARIANTS)
def test_near_miss_paths_are_not_the_telemetry_route(tmp_path, repository_factory, variant):
    from urllib.parse import unquote
    aggregator.reset()
    app, http = build(tmp_path, repository_factory(None))
    status = asyncio.run(raw_post(app, variant))
    assert unquote(variant) != EXEMPT and status != 204, (variant, status)
    assert not [n for n in aggregator.snapshot()['stages'] if n.startswith('client.')]      # the handler never ran
    app.state.repository.close(); asyncio.run(http.aclose())


def test_percent_encoded_form_of_the_exact_path_is_the_same_route_not_a_bypass(tmp_path, repository_factory):
    """After decoding it IS the exempt path, and the router matches on that same decoded path."""
    app, http = build(tmp_path, repository_factory(None))
    for encoded in ('/api/telemetry/%74urn', '/api%2Ftelemetry/turn'):
        assert asyncio.run(raw_post(app, encoded)) == 204
    app.state.repository.close(); asyncio.run(http.aclose())


# --------------------------------------------------------------------------- 4. PostgreSQL: the lease itself
def lease_key():
    return int.from_bytes(sha256(('audli-account:' + USER_A).encode()).digest()[:8], 'big', signed=True)


def hold_lease(repo):
    blocker = repo.engine.connect(); transaction = blocker.begin()
    blocker.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lease_key()})
    return blocker, transaction


def concrete(path):
    return re.sub(r'\{[^}]+\}', 'x', path)


def test_every_other_mutating_route_still_queues_behind_the_lease(tmp_path, repository_factory):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')
    app, http = build(tmp_path, repo)
    targets = sorted({(m, concrete(r.path)) for r in app.routes if hasattr(r, 'methods') and r.path.startswith('/api')
                      for m in r.methods if m in ('POST', 'PUT', 'PATCH', 'DELETE') and r.path != EXEMPT})
    assert len(targets) > 40, len(targets)         # the whole mutating surface, not a sample
    async def run():
        async with client_for(app) as client:
            blocker, transaction = hold_lease(repo)
            try:
                still_waiting, finished = [], []
                for i in range(0, len(targets), 12):
                    batch = targets[i:i + 12]
                    async def probe(method, path):
                        try:
                            await asyncio.wait_for(client.request(method, path, json={}, headers=HEADERS), .5)
                            return (method, path), True        # completed -> did NOT wait for the lease
                        except asyncio.TimeoutError:
                            return (method, path), False
                    for target, done in await asyncio.gather(*(probe(*t) for t in batch)):
                        (finished if done else still_waiting).append(target)
                exempt = await client.post(EXEMPT, json={'stages': {'assessment': 5}}, headers=HEADERS)
            finally:
                transaction.rollback(); blocker.close()
            after = await client.put('/api/profile', json={'name': 'A', 'goal': 'B'}, headers=HEADERS)   # lease released: proceeds
            return finished, len(still_waiting), exempt.status_code, after.status_code
    finished, waiting, exempt_status, after_status = asyncio.run(run())
    assert finished == [], f'These mutating routes did not wait for the lease: {finished}'
    assert waiting == len(targets) and exempt_status == 204 and after_status == 200
    app.state.repository.close(); asyncio.run(http.aclose())


def test_read_only_requests_never_took_the_lease_and_still_do_not(tmp_path, repository_factory):
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')
    app, http = build(tmp_path, repo)
    async def run():
        async with client_for(app) as client:
            blocker, transaction = hold_lease(repo)
            try:
                return (await asyncio.wait_for(client.get('/api/profile', headers=HEADERS), 2)).status_code
            finally:
                transaction.rollback(); blocker.close()
    assert asyncio.run(run()) == 200
    app.state.repository.close(); asyncio.run(http.aclose())


def test_every_near_miss_path_queues_behind_the_lease(tmp_path, repository_factory):
    """Probed one at a time: concurrent waiters would starve the event loop (see docs/AUD-68-LEASE-EXEMPTION-REVIEW.md)."""
    repo = repository_factory(None)
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('The account lease exists only on PostgreSQL.')
    app, http = build(tmp_path, repo)
    async def run():
        blocker, transaction = hold_lease(repo)
        try:
            outcome = {}
            for variant in VARIANTS:
                try:
                    outcome[variant] = await asyncio.wait_for(raw_post(app, variant), .4)    # completed without the lease
                except asyncio.TimeoutError:
                    outcome[variant] = 'queued'
            exact = await asyncio.wait_for(raw_post(app, EXEMPT), 2)
        finally:
            transaction.rollback(); blocker.close()
        return outcome, exact
    outcome, exact = asyncio.run(run())
    # Anything under the authenticated prefix must wait for the lease. Paths outside the lowercase prefix never enter the
    # auth middleware at all and are answered 404 by the router, which has no such route (case-sensitive).
    for variant, result in outcome.items():
        if variant.startswith('/api'):
            assert result == 'queued', (variant, result)
        else:
            assert result == 404, (variant, result)
    assert exact == 204
    app.state.repository.close(); asyncio.run(http.aclose())


def test_every_route_lives_under_the_authenticated_prefix(tmp_path, repository_factory):
    """The auth middleware is gated on the lowercase '/api' prefix; a route outside it would be unauthenticated."""
    app, http = build(tmp_path, repository_factory(None))
    outside = [r.path for r in app.routes if hasattr(r, 'path') and not r.path.startswith('/api')]
    assert outside == [], outside
    app.state.repository.close(); asyncio.run(http.aclose())
