"""AUD-89 P2 (PR A): the server-delivered upload retry policy is OFF by default, fails closed, and is a read-only authenticated GET."""
import asyncio
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.repository import ProgressRepository
from app.upload_policy import DISABLED, UploadPolicy, flag_value, resolve_policy
from conftest import ASGIClient, FakeProvider
from test_auth import auth_response, settings_for
from test_auth_lease_exemption import HEADERS, client_for
from test_persistence import repository_factory  # noqa: F401  (fixture)

OWNER = '00000000-0000-4000-8000-000000000001'


def app_for(tmp_path, **settings):
    repo = ProgressRepository(tmp_path / 'x.sqlite3', OWNER)
    return create_app(Settings(data_dir=tmp_path, _env_file=None, **settings), FakeProvider(), repo), repo


def policy_of(tmp_path, **settings):
    app, repo = app_for(tmp_path, **settings)
    try:
        response = ASGIClient(app, FakeProvider()).get('/api/upload-policy')
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        return response.json(), app
    finally:
        repo.close()


# ----------------------------------------------------------------------------------------------------------- defaults and kill switch
def test_off_by_default_and_the_document_says_only_that():
    assert Settings.model_fields['upload_retry'].default is False and Settings(_env_file=None).upload_retry is False
    assert resolve_policy(False, '') == DISABLED == {'enabled': False}


def test_the_endpoint_is_disabled_by_default_and_health_agrees(tmp_path):
    body, app = policy_of(tmp_path)
    assert body == {'enabled': False}
    app, repo = app_for(tmp_path / 'h')
    assert ASGIClient(app, FakeProvider()).get('/api/health').json()['upload_retry'] is False
    repo.close()


def test_turning_it_on_serves_the_reviewed_defaults(tmp_path):
    body, app = policy_of(tmp_path, upload_retry=True)
    assert body == {'enabled': True, **UploadPolicy().model_dump()}
    assert (body['base_ms'], body['bytes_per_second'], body['min_ms'], body['max_ms']) == (8000, 64000, 9000, 30000)
    assert (body['growth'], body['max_sends'], body['deadline_ms'], body['backoff_ms'], body['hidden_grace_sends']) == ([1.0, 1.25, 1.5], 3, 50000, [300, 900], 1)


def test_the_kill_switch_is_immediate_and_needs_no_other_change(tmp_path):
    on, _ = policy_of(tmp_path / 'a', upload_retry=True, upload_retry_policy='{"max_sends": 2}')
    off, _ = policy_of(tmp_path / 'b', upload_retry=False, upload_retry_policy='{"max_sends": 2}')
    assert on['enabled'] is True and on['max_sends'] == 2 and off == {'enabled': False}


@pytest.mark.parametrize('value, expected', [('true', True), ('TRUE', True), ('1', True), ('yes', True), ('on', True), (' true ', True), (True, True),
                                             ('', False), ('false', False), ('0', False), ('no', False), ('maybe', False), ('tru', False),
                                             ('enabled', False), (None, False), (False, False), (2, False)])
def test_only_a_clear_true_turns_it_on_and_a_typo_never_stops_the_service(value, expected):
    assert flag_value(value) is expected
    assert Settings(upload_retry=value, _env_file=None).upload_retry is expected


def test_the_environment_variable_is_read_as_documented(monkeypatch):
    monkeypatch.setenv('AUDLI_UPLOAD_RETRY', 'sure')
    assert Settings(_env_file=None).upload_retry is False
    monkeypatch.setenv('AUDLI_UPLOAD_RETRY', 'true')
    assert Settings(_env_file=None).upload_retry is True


# ----------------------------------------------------------------------------------------------------------- overrides and invalid policies
def test_valid_overrides_apply_and_unspecified_values_keep_their_defaults(tmp_path):
    overrides = {'base_ms': 6000, 'max_sends': 2, 'growth': [1, 2], 'backoff_ms': [], 'deadline_ms': 30000}
    body, _ = policy_of(tmp_path, upload_retry=True, upload_retry_policy=json.dumps(overrides))
    assert body['enabled'] is True and body['base_ms'] == 6000 and body['max_sends'] == 2 and body['growth'] == [1.0, 2.0]
    assert body['backoff_ms'] == [] and body['bytes_per_second'] == 64000 and body['min_ms'] == 9000


INVALID = ['{', 'not json', '[]', '"x"', '123', 'null', '{"unknown": 1}', '{"base_ms": "8000"}', '{"base_ms": 999}', '{"base_ms": 60001}',
           '{"bytes_per_second": 100}', '{"min_ms": 40000, "max_ms": 20000}', '{"deadline_ms": 3000}', '{"max_sends": 0}', '{"max_sends": 6}',
           '{"growth": []}', '{"growth": [0.5]}', '{"growth": [4]}', '{"growth": "1"}', '{"growth": [1, 1, 1, 1, 1, 1]}',
           '{"backoff_ms": [-1]}', '{"backoff_ms": [10001]}', '{"backoff_ms": [1, 2, 3, 4, 5]}', '{"hidden_grace_sends": 4}', '{"slow_hint_ms": 100}',
           '{"base_ms": null}', '{"base_ms": 8000.5}', '{"base_ms": true}']


@pytest.mark.parametrize('overrides', INVALID)
def test_an_invalid_policy_leaves_retries_disabled_even_when_the_flag_is_on(overrides, caplog):
    caplog.set_level(logging.WARNING, logger='uvicorn.error')
    assert resolve_policy(True, overrides) == {'enabled': False}
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ['AUDLI_UPLOAD_RETRY_POLICY is invalid; recording upload retries stay disabled']      # the value itself is never logged


def test_an_invalid_policy_through_the_app_is_disabled_and_health_says_so(tmp_path):
    body, _ = policy_of(tmp_path, upload_retry=True, upload_retry_policy='{"max_sends": 99}')
    assert body == {'enabled': False}
    app, repo = app_for(tmp_path / 'h', upload_retry=True, upload_retry_policy='{"max_sends": 99}')
    assert ASGIClient(app, FakeProvider()).get('/api/health').json()['upload_retry'] is False
    repo.close()


def test_the_schema_bounds_every_value_so_no_policy_can_make_a_send_or_a_wait_unbounded():
    widest = UploadPolicy(max_ms=120000, deadline_ms=300000, max_sends=5, growth=[3, 3, 3, 3, 3], backoff_ms=[10000] * 4, base_ms=60000)
    assert (widest.deadline_ms, widest.max_sends) == (300000, 5)           # the client stops at deadline_ms whatever the rest says
    for field, value in (('deadline_ms', 300001), ('max_sends', 6), ('max_ms', 120001), ('base_ms', 60001), ('growth', [3.01]), ('backoff_ms', [10001])):
        with pytest.raises(ValueError):
            UploadPolicy(**{field: value})


def test_the_document_contains_nothing_but_policy_numbers(tmp_path):
    body, _ = policy_of(tmp_path, upload_retry=True)
    assert set(body) == {'enabled', 'base_ms', 'bytes_per_second', 'min_ms', 'max_ms', 'growth', 'max_sends', 'deadline_ms', 'backoff_ms',
                         'hidden_grace_sends', 'slow_hint_ms'}
    raw = json.dumps(body)
    assert OWNER not in raw and 'sb_' not in raw and 'http' not in raw


# ----------------------------------------------------------------------------------------------------------- authentication and side effects
def test_it_is_authenticated_like_every_api_route_and_changes_no_learner_state(tmp_path, repository_factory):
    repo = repository_factory(None)
    http = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path, upload_retry=True), FakeProvider(), repository=repo, auth_client=http)

    async def run():
        async with client_for(app) as client:
            anonymous = await client.get('/api/upload-policy')
            invalid = await client.get('/api/upload-policy', headers={'Authorization': 'Bearer invalid'})
            before = (await client.get('/api/profile', headers=HEADERS)).json()
            allowed = await client.get('/api/upload-policy', headers=HEADERS)
            after = (await client.get('/api/profile', headers=HEADERS)).json()
            return anonymous, invalid, allowed, before, after
    anonymous, invalid, allowed, before, after = asyncio.run(run())
    assert anonymous.status_code == 401 and invalid.status_code == 401
    assert allowed.status_code == 200 and allowed.json()['enabled'] is True and before == after
    repo.close(); asyncio.run(http.aclose())


def test_it_is_a_get_only_route_so_it_takes_no_account_lease(tmp_path):
    app, repo = app_for(tmp_path, upload_retry=True)
    routes = [r for r in app.routes if getattr(r, 'path', None) == '/api/upload-policy']
    assert len(routes) == 1 and routes[0].methods == {'GET'}
    assert ASGIClient(app, FakeProvider()).post('/api/upload-policy').status_code == 405
    repo.close()


# ----------------------------------------------------------------------------------------------------------- shared cases with the browser
def test_the_shared_cases_agree_with_the_server_and_the_documented_defaults():
    """tests/fixtures/upload_policy_cases.json is also read by tests/upload-policy.test.mjs, so the server and the browser accept and reject the same policies."""
    from pathlib import Path
    shared = json.loads((Path(__file__).parent / 'fixtures' / 'upload_policy_cases.json').read_text())
    assert shared['defaults'] == UploadPolicy().model_dump()
    for case in shared['cases']:
        resolved = resolve_policy(True, json.dumps(case['overrides']))
        assert resolved['enabled'] is case['valid'], case['name']
        if case['valid']:
            assert {key: value for key, value in resolved.items() if key != 'enabled'} == UploadPolicy.model_validate(case['overrides']).model_dump(), case['name']
