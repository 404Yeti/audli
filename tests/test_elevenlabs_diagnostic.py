import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.services import elevenlabs_diagnostic as diagnostic


def settings():
    return SimpleNamespace(elevenlabs_api_key=SecretStr('private-test-value'),
        elevenlabs_voice_id='yKYzqEa22xh5PdidhN70', elevenlabs_model='eleven_flash_v2_5',
        elevenlabs_stability=.5, elevenlabs_similarity_boost=.75,
        elevenlabs_style=0, elevenlabs_speaker_boost=True)


@pytest.mark.parametrize(('status', 'code', 'category'), [
    (200, None, 'accepted'), (401, 'invalid_api_key', 'invalid_api_key'),
    (401, 'quota_exceeded', 'quota_exceeded'),
    (401, 'missing_permissions', 'missing_permissions'),
    (401, 'private-test-value', 'provider_rejected'),
    (403, 'paid_plan_required', 'paid_plan_required'),
])
def test_single_request_and_sanitized_log(monkeypatch, caplog, status, code, category):
    calls = []
    class Response:
        def __init__(self):
            self.status = status
        def read(self, maximum):
            assert status != 200  # Never read or persist successful audio.
            assert maximum == 16385
            return json.dumps({'detail': {'status': code, 'message': 'private-test-value'}}).encode()
    class Connection:
        def __init__(self, host, timeout):
            assert host == 'api.elevenlabs.io' and timeout == 15
        def request(self, method, path, body, headers):
            calls.append((method, path))
            assert headers['xi-api-key'] == 'private-test-value'
            data = json.loads(body)
            assert data['text'] == 'Hello.' and data['model_id'] == 'eleven_flash_v2_5'
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(diagnostic.http.client, 'HTTPSConnection', Connection)
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        diagnostic._probe(settings())
    assert calls == [('POST', '/v1/text-to-speech/yKYzqEa22xh5PdidhN70/stream?output_format=mp3_44100_128')]
    assert len(caplog.records) == 1
    assert caplog.messages == [f'ElevenLabs diagnostic http_status={status} category={category}']
    assert 'private-test-value' not in caplog.text


def test_network_failure_is_not_retried_or_exposed(monkeypatch, caplog):
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('private-test-value')
    monkeypatch.setattr(diagnostic.http.client, 'HTTPSConnection', unavailable)
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        diagnostic._probe(settings())
    assert len(calls) == 1
    assert caplog.messages == ['ElevenLabs diagnostic http_status=0 category=network_failure']


def test_missing_key_never_connects(monkeypatch, caplog):
    s = settings()
    s.elevenlabs_api_key = None
    monkeypatch.setattr(diagnostic.http.client, 'HTTPSConnection', lambda *a, **k: pytest.fail('network'))
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        diagnostic._probe(s)
    assert caplog.messages == ['ElevenLabs diagnostic http_status=0 category=missing_credentials']


@pytest.mark.parametrize(('enabled', 'target', 'revision'), [
    ('false', 'a' * 40, 'a' * 40), ('', '', ''),
    ('true', 'b' * 40, 'a' * 40), ('true', '', 'a' * 40),
    ('true', '../unsafe', '../unsafe'),
])
def test_disabled_or_unpinned_is_noop(monkeypatch, enabled, target, revision):
    monkeypatch.setenv('AUDLI_ELEVENLABS_DIAGNOSTIC_ENABLED', enabled)
    monkeypatch.setenv('AUDLI_ELEVENLABS_DIAGNOSTIC_REVISION', target)
    monkeypatch.setenv('RENDER_GIT_COMMIT', revision)
    monkeypatch.setattr(diagnostic, '_probe', lambda s: pytest.fail('probe'))
    assert diagnostic.schedule(settings()) is None


def test_background_once_per_container_revision(monkeypatch, tmp_path):
    monkeypatch.setenv('AUDLI_ELEVENLABS_DIAGNOSTIC_ENABLED', 'true')
    monkeypatch.setenv('AUDLI_ELEVENLABS_DIAGNOSTIC_REVISION', 'a' * 40)
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'a' * 40)
    monkeypatch.setattr(diagnostic, 'Path', lambda _: tmp_path)
    calls = []
    monkeypatch.setattr(diagnostic, '_probe', lambda s: calls.append(True))
    async def run():
        task = diagnostic.schedule(settings())
        assert task is not None
        assert calls == []  # Startup does not await networking.
        assert diagnostic.schedule(settings()) is None
        await task
        assert diagnostic.schedule(settings()) is None
    asyncio.run(run())
    assert calls == [True]


def test_marker_failure_never_breaks_startup(monkeypatch, tmp_path):
    monkeypatch.setenv('AUDLI_ELEVENLABS_DIAGNOSTIC_ENABLED', 'true')
    monkeypatch.setenv('AUDLI_ELEVENLABS_DIAGNOSTIC_REVISION', 'a' * 40)
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'a' * 40)
    monkeypatch.setattr(diagnostic, 'Path', lambda _: tmp_path / 'missing-parent')
    assert diagnostic.schedule(settings()) is None
