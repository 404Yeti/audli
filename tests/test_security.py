import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.security import LocalRequestGuard
from conftest import ASGIClient, FakeProvider


PRODUCTION_ORIGIN = 'https://audli-seven.vercel.app'


@pytest.mark.parametrize('origin,status', [
    ('http://localhost:3000', 200),
    ('http://127.0.0.1:3000', 200),
    ('http://[::1]:3000', 200),
    (PRODUCTION_ORIGIN, 200),
    ('https://AUDLI-SEVEN.VERCEL.APP:443', 200),
    ('https://custom.example', 200),
    ('https://unconfigured.example', 403),
    (PRODUCTION_ORIGIN + '.evil.example', 403),
    ('https://evil-audli-seven.vercel.app', 403),
    ('http://[::1].evil.example', 403),
    ('http://audli-seven.vercel.app', 403),
    (PRODUCTION_ORIGIN + ':444', 403),
    (PRODUCTION_ORIGIN + ':0', 403),
    (PRODUCTION_ORIGIN + '/path', 403),
    ('https://user@audli-seven.vercel.app', 403),
    ('https://audli-seven.vercel.app:bad', 403),
    ('null', 403),
    ('', 403),
])
def test_configured_origins_on_real_api(tmp_path, monkeypatch, origin, status):
    monkeypatch.setenv('AUDLI_ALLOWED_ORIGINS', f' {PRODUCTION_ORIGIN}, https://custom.example ')
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path, environment='production', _env_file=None), provider)
    client = ASGIClient(app, provider)
    response = client.put('/api/profile', headers={'origin': origin}, json={'name': 'Learner', 'goal': 'Listen better'})
    assert response.status_code == status, response.text


def test_external_origin_is_not_enabled_by_default(client):
    assert client.put('/api/profile', headers={'origin': PRODUCTION_ORIGIN}, json={'name': 'Learner', 'goal': 'Listen better'}).status_code == 403
    assert client.put('/api/profile', json={'name': 'Learner', 'goal': 'Listen better'}).status_code == 200


@pytest.mark.parametrize('value', ['*', 'null', 'https://*.example', 'https://user@example.com',
    'https://example.com/', 'https://example.com?q=1', 'https://example.com#',
    'https://example.com:bad', 'https://example.com:65536', 'https://example.com:',
    'https://example.com\n.evil', 'file://localhost'])
def test_invalid_origin_configuration_fails_startup(value):
    with pytest.raises(ValidationError):
        Settings(allowed_origins=value, _env_file=None)


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH'])
@pytest.mark.parametrize('path,maximum', [('/api/profile', 40000), ('/api/exercises/id/attempts', 65536 + 1024)])
@pytest.mark.parametrize('mode', ['boundary', 'declared', 'streamed', 'invalid_length'])
def test_body_limits_and_replay_for_allowed_origin(method, path, maximum, mode):
    received = []
    sent = []
    body = b'x' * (maximum + (mode == 'streamed'))
    messages = [
        {'type': 'http.request', 'body': body[:100], 'more_body': True},
        {'type': 'http.request', 'body': body[100:], 'more_body': False},
        {'type': 'http.disconnect'},
    ]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        received.append(await receive())
        received.append(await receive())
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})

    headers = [(b'origin', PRODUCTION_ORIGIN.encode())]
    if mode in ('declared', 'invalid_length'):
        headers.append((b'content-length', str(maximum + 1).encode() if mode == 'declared' else b'invalid'))
    guard = LocalRequestGuard(downstream, max_audio_bytes=1024, allowed_origins=(PRODUCTION_ORIGIN,))
    asyncio.run(guard({'type': 'http', 'method': method, 'path': path, 'headers': headers}, receive, send))
    assert sent[0]['status'] == (200 if mode == 'boundary' else 413)
    if mode == 'boundary':
        assert received == [{'type': 'http.request', 'body': body, 'more_body': False}, {'type': 'http.disconnect'}]
    else:
        assert received == []
