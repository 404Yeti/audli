"""AUD-82: flagged direct-browser API access. Exact origins, bearer tokens only, auth and guards unchanged."""
import pytest
from pydantic import ValidationError

from app.config import Settings
from test_auth import accounts, accounts_with, AccountClient  # noqa: F401 (fixture)
from test_persistence import repository_factory  # noqa: F401 (fixture)

ORIGIN = 'https://audli-seven.vercel.app'


@pytest.fixture
def cors_accounts(tmp_path, repository_factory):
    yield from accounts_with(tmp_path, repository_factory, cors_enabled=True)


def test_off_by_default_and_requires_explicit_origins():
    assert Settings(_env_file=None).cors_enabled is False
    with pytest.raises(ValidationError, match='AUDLI_ALLOWED_ORIGINS'):
        Settings(_env_file=None, cors_enabled=True)


def test_disabled_sends_no_cors_headers(accounts):
    a, _, _ = accounts
    response = a.get('/api/profile', headers={'Origin': ORIGIN})
    assert 'access-control-allow-origin' not in response.headers


def test_preflight_for_allowed_origin_needs_no_token_and_leaks_nothing(cors_accounts):
    a, _, _ = cors_accounts
    # Preflights never carry credentials, so they must be answered before authentication.
    response = a.request('OPTIONS', '/api/attempts/anything/assess', headers={'Origin': ORIGIN,
        'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,content-type'})
    assert response.status_code == 200 and response.content == b'OK'
    assert response.headers['access-control-allow-origin'] == ORIGIN
    assert 'access-control-allow-credentials' not in response.headers
    allowed = {h.strip().lower() for h in response.headers['access-control-allow-headers'].split(',')}
    assert {'authorization', 'content-type'} <= allowed and allowed <= {'authorization', 'content-type', 'accept', 'accept-language', 'content-language'}
    assert response.headers['access-control-max-age'] == '600'


@pytest.mark.parametrize('origin', ['https://evil.example', 'http://audli-seven.vercel.app', 'https://audli-seven.vercel.app.evil.example', 'null'])
def test_other_origins_are_refused(cors_accounts, origin):
    a, _, _ = cors_accounts
    response = a.request('OPTIONS', '/api/profile', headers={'Origin': origin, 'Access-Control-Request-Method': 'GET'})
    assert response.status_code == 400 and 'access-control-allow-origin' not in response.headers
    # The existing guard still refuses cross-site writes, and simple reads get no CORS grant.
    assert a.post('/api/profile', headers={'Origin': origin}, json={}).status_code == 403
    assert 'access-control-allow-origin' not in a.get('/api/profile', headers={'Origin': origin}).headers


def test_requests_still_need_a_valid_token_and_errors_are_readable(cors_accounts):
    a, _, _ = cors_accounts
    anonymous = AccountClient(a.app, a.provider, 'not-a-token')
    response = anonymous.get('/api/profile', headers={'Origin': ORIGIN})
    assert response.status_code == 401 and response.headers['access-control-allow-origin'] == ORIGIN
    ok = a.get('/api/profile', headers={'Origin': ORIGIN})
    assert ok.status_code == 200 and ok.headers['access-control-allow-origin'] == ORIGIN
    assert 'access-control-allow-credentials' not in ok.headers


def test_oversized_bodies_are_still_refused_for_allowed_origin(cors_accounts):
    a, _, _ = cors_accounts
    response = a.post('/api/profile', headers={'Origin': ORIGIN, 'Content-Type': 'application/json'}, content=b'x' * 50000)
    assert response.status_code == 413 and response.headers['access-control-allow-origin'] == ORIGIN
