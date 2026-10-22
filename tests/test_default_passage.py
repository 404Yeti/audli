"""AUD-74: flagged ElevenLabs route for ordinary passages. HTTP is faked; no paid call is made."""
import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from app.config import Settings
from app.main import create_app
from app.storage.accent_controls import SharedAccentControls
from app.accent_practice import passage_audio
from test_persistence import repository_factory
from test_accent_practice import VOICE
from accent_control_helpers import scope
from conftest import FakeProvider, ASGIClient

TUTOR_VOICE = 'yKYzqEa22xh5PdidhN70'
MP3 = b'ID3' + b'\0' * 64
CONTENT = SimpleNamespace(script='A short fixture passage about a team update.', speech_rate=.9)


def config(**kwargs):
    return Settings(_env_file=None, elevenlabs_api_key='fixture', default_passage_elevenlabs_enabled=True,
        passage_tts_daily_characters=500, passage_tts_max_concurrent=2, **kwargs)


class Provider:
    calls = 0
    async def speech(self, content):
        Provider.calls += 1
        return b'openai-coral'


def shim(client):
    # Replace only this module's HTTP client; the ASGI test client keeps the real httpx.
    return SimpleNamespace(AsyncClient=client, AsyncHTTPTransport=lambda **kwargs: None, HTTPError=httpx.HTTPError,
        TimeoutException=httpx.TimeoutException)


@pytest.fixture
def eleven(monkeypatch):
    """Records ElevenLabs requests; `behavior` selects the response."""
    state = SimpleNamespace(requests=[], behavior=200)
    async def passthrough(data, kind, **kwargs):
        return data
    monkeypatch.setattr('app.audio.sanitize_generated_audio', passthrough)
    monkeypatch.setattr('app.main.sanitize_generated_audio', passthrough)
    Provider.calls = 0
    class Client:
        def __init__(self, **kwargs):
            state.client_kwargs = kwargs
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            state.requests.append((url, kwargs))
            if state.behavior == 'timeout':
                await asyncio.sleep(5)
            if state.behavior == 'network':
                raise httpx.ConnectError('SECRET network detail')
            if state.behavior == 'invalid':
                return httpx.Response(200, content=b'not audio', request=httpx.Request('POST', url))
            if state.behavior == 200:
                return httpx.Response(200, content=MP3, headers={'character-cost': '42'}, request=httpx.Request('POST', url))
            body = {'detail': {'status': 'quota_exceeded', 'message': 'SECRET'}} if state.behavior == 401 else {}
            return httpx.Response(state.behavior, json=body, request=httpx.Request('POST', url))
    monkeypatch.setattr('app.default_passage.httpx', shim(Client))
    state.Client = Client
    return state


@pytest.fixture
def controls(repository_factory):
    root = repository_factory(None)
    settings = config()
    control = SharedAccentControls(root.engine, settings)
    control.configure(enabled=False, daily_limit=500, concurrent_limit=2)  # accent kill switch stays independent
    try:
        yield settings, control
    finally:
        control.close()


def run(settings, control, content=CONTENT, accent=None, **kwargs):
    return asyncio.run(passage_audio(settings, Provider(), content, accent, control, **kwargs))


def records(caplog):
    return [r.getMessage() for r in caplog.records if 'Passage TTS' in r.getMessage()]


def test_flag_defaults_off_and_keeps_openai_route(eleven):
    assert Settings(_env_file=None).default_passage_elevenlabs_enabled is False
    settings = Settings(_env_file=None, elevenlabs_api_key='fixture')
    audio, voice = run(settings, None)
    assert audio == b'openai-coral' and (voice.provider, voice.voice_id, voice.fallback) == ('openai', 'coral', False)
    assert eleven.requests == []


def test_flag_on_uses_tutor_voice_id_and_settings(controls, eleven, caplog):
    settings, control = controls
    caplog.set_level(logging.INFO)
    audio, voice = run(settings, control)
    assert audio == MP3 and (voice.provider, voice.voice_id, voice.accent, voice.fallback) == ('elevenlabs', TUTOR_VOICE, 'default', False)
    (url, kwargs), = eleven.requests
    assert url.endswith('/text-to-speech/' + TUTOR_VOICE) and Provider.calls == 0
    assert kwargs['json']['voice_settings'] == {'stability': .5, 'similarity_boost': .75, 'style': 0,
        'use_speaker_boost': True, 'speed': .9}
    assert kwargs['json']['model_id'] == 'eleven_flash_v2_5' and eleven.client_kwargs['timeout'] == 20
    log = ' '.join(records(caplog))
    assert '"outcome": "success"' in log and TUTOR_VOICE in log and '"reported_cost_units": 42.0' in log
    assert 'fixture' not in log and CONTENT.script not in log
    assert control.read()['characters'] == len(CONTENT.script)


def test_configured_tutor_voice_id_is_reused(controls, eleven):
    settings, control = controls
    settings = settings.model_copy(update={'elevenlabs_voice_id': 'b' * 20})
    _, voice = run(settings, control)
    assert voice.voice_id == 'b' * 20 and eleven.requests[0][0].endswith('/b' * 1 + 'b' * 19)


def test_accent_routing_unchanged_and_not_double_charged(controls, eleven, repository_factory, monkeypatch):
    settings, control = controls
    settings = settings.model_copy(update={'accent_practice_enabled': True, 'passage_voices': {'british': VOICE}})
    assert settings.available_passage_voices
    control.settings = settings
    control.configure(enabled=True, daily_limit=500, concurrent_limit=2)
    monkeypatch.setattr('app.accent_practice.httpx.AsyncClient', eleven.Client)
    root = repository_factory(None)
    repo = root.for_learner(str(uuid4()))
    lesson = scope(repo, settings)
    audio, voice = run(settings, control, accent='british', owner=repo.learner_id, lesson=lesson)
    assert (voice.accent, voice.provider, voice.voice_id) == ('british', 'elevenlabs', VOICE.voice_id)
    assert eleven.requests[0][0].endswith('/' + VOICE.voice_id) and len(eleven.requests) == 1


def test_accent_provider_failure_falls_to_default_voice_with_fallback_flag(controls, eleven, repository_factory, monkeypatch):
    settings, control = controls
    settings = settings.model_copy(update={'accent_practice_enabled': True, 'passage_voices': {'british': VOICE}})
    control.settings = settings
    control.configure(enabled=True, daily_limit=500, concurrent_limit=2)
    root = repository_factory(None)
    repo = root.for_learner(str(uuid4()))
    lesson = scope(repo, settings)
    calls = []
    class Flaky:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append(url)
            status = 503 if VOICE.voice_id in url else 200
            return httpx.Response(status, content=MP3, request=httpx.Request('POST', url))
    monkeypatch.setattr('app.accent_practice.httpx.AsyncClient', Flaky)
    monkeypatch.setattr('app.default_passage.httpx', shim(Flaky))
    audio, voice = run(settings, control, accent='british', owner=repo.learner_id, lesson=lesson)
    assert audio == MP3 and voice.accent == 'default' and voice.fallback is True
    assert voice.voice_id == TUTOR_VOICE and voice.provider == 'elevenlabs'


@pytest.mark.parametrize('behavior,reason', [(401, 'quota_exceeded'), (429, 'http_rejected'), (503, 'http_rejected'),
                                              ('network', 'network'), ('invalid', 'invalid_audio'), ('timeout', 'timeout')])
def test_provider_failure_falls_back_explicitly(controls, eleven, caplog, behavior, reason):
    settings, control = controls
    settings.passage_tts_timeout_seconds = .05
    eleven.behavior = behavior
    caplog.set_level(logging.INFO)
    audio, voice = run(settings, control)
    assert audio == b'openai-coral' and (voice.provider, voice.voice_id) == ('openai', 'coral')
    warning, = [r for r in caplog.records if 'Passage TTS' in r.getMessage() and r.levelno == logging.WARNING]
    text = warning.getMessage()
    assert f'"fallback_reason": "{reason}"' in text and '"fallback_provider": "openai"' in text
    assert 'SECRET' not in text and CONTENT.script not in text and 'fixture' not in text
    assert len(eleven.requests) == 1  # never retried
    assert control.read()['characters'] == len(CONTENT.script)  # no refund


def test_budget_exhaustion_falls_back_without_calling_elevenlabs(controls, eleven, caplog):
    settings, control = controls
    caplog.set_level(logging.INFO)
    big = SimpleNamespace(script='x' * 300, speech_rate=.9)
    assert run(settings, control, big)[1].provider == 'elevenlabs'
    audio, voice = run(settings, control, big)  # 600 > 500
    assert audio == b'openai-coral' and len(eleven.requests) == 1
    assert '"fallback_reason": "budget_exhausted"' in ' '.join(records(caplog))


def test_concurrency_limit_leaves_tutor_capacity(controls, eleven):
    settings, control = controls
    for _ in range(2):
        assert isinstance(control.reserve_default(10), tuple)
    assert control.reserve_default(10) == 'concurrency_limit'
    audio, voice = run(settings, control)
    assert audio == b'openai-coral' and eleven.requests == []


def test_oversized_and_unsupported_rate_fall_back(controls, eleven):
    settings, control = controls
    assert run(settings, control, SimpleNamespace(script='x' * 5000, speech_rate=.9))[0] == b'openai-coral'
    assert run(settings, control, SimpleNamespace(script='story', speech_rate=1.5))[0] == b'openai-coral'
    assert eleven.requests == []


def test_missing_configuration(controls, eleven, caplog):
    settings, control = controls
    caplog.set_level(logging.INFO)
    no_key = settings.model_copy(update={'elevenlabs_api_key': None})
    assert run(no_key, control)[0] == b'openai-coral'
    assert run(settings, None)[0] == b'openai-coral'
    reasons = ' '.join(records(caplog))
    assert '"fallback_reason": "missing_credentials"' in reasons and '"fallback_reason": "admission_unavailable"' in reasons
    assert eleven.requests == []
    with pytest.raises(ValidationError, match='ELEVENLABS_API_KEY'):
        Settings(_env_file=None, default_passage_elevenlabs_enabled=True, passage_tts_daily_characters=10)
    with pytest.raises(ValidationError, match='DAILY_CHARACTERS'):
        Settings(_env_file=None, default_passage_elevenlabs_enabled=True, elevenlabs_api_key='x')
    with pytest.raises(ValidationError, match='AUDLI_PROVIDER'):
        Settings(_env_file=None, default_passage_elevenlabs_enabled=True, elevenlabs_api_key='x', provider='demo',
                 passage_tts_daily_characters=10)


def test_control_plane_unavailable_falls_back(controls, eleven, monkeypatch, caplog):
    settings, control = controls
    caplog.set_level(logging.INFO)
    def broken(count): raise OSError('SECRET database detail')
    monkeypatch.setattr(control, 'reserve_default', broken)
    assert run(settings, control)[0] == b'openai-coral' and eleven.requests == []
    assert '"fallback_reason": "control_unavailable"' in ' '.join(records(caplog))


def test_existing_exercise_is_reused_not_regenerated(tmp_path, eleven):
    provider = FakeProvider()
    app = create_app(config(data_dir=tmp_path).model_copy(update={'passage_tts_daily_characters': 50000}), provider)
    app.state.accent_controls.configure(enabled=False, daily_limit=50000, concurrent_limit=2)
    client = ASGIClient(app, provider)
    first = client.post('/api/exercises').json()
    audio = client.get(first['audio_url']).content
    assert first['passage_voice']['provider'] == 'elevenlabs' and first['passage_voice']['voice_id'] == TUTOR_VOICE
    assert len(eleven.requests) == 1
    assert client.post('/api/exercises').json()['id'] == first['id']
    assert client.get(first['audio_url']).content == audio and len(eleven.requests) == 1
    app.state.accent_controls.close()


@pytest.mark.parametrize('reserve_seconds', [0, .08], ids=['fast-start', 'slow-cold-start'])
def test_burst_never_exceeds_passage_concurrency_cap(controls, monkeypatch, caplog, reserve_seconds):
    """Six simultaneous ordinary passages: at most the shared cap reach ElevenLabs; the rest fall back explicitly.

    `slow-cold-start` models what a cold database does: reservations run in worker threads but serialize on the write lock, and
    each takes 80 ms, longer than the 50 ms fake synthesis. Without the hold-until-decided gate below, the first admitted call
    would finish and free its slot before the sixth request arrived, so the cap would not be exercised and a third passage would
    reach ElevenLabs."""
    settings, control = controls
    if reserve_seconds:
        import threading, time
        write_lock, real_reserve = threading.Lock(), control.reserve_default
        def slow_reserve(count):
            with write_lock:
                time.sleep(reserve_seconds)
                return real_reserve(count)
        monkeypatch.setattr(control, 'reserve_default', slow_reserve)
    caplog.set_level(logging.INFO)
    live = {'now': 0, 'peak': 0}
    decided = {}  # Set once all four refused passages have fallen back; created inside the running loop.
    async def passthrough(data, kind, **kwargs):
        return data
    monkeypatch.setattr('app.audio.sanitize_generated_audio', passthrough)
    class Slow:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            live['now'] += 1; live['peak'] = max(live['peak'], live['now'])
            await asyncio.sleep(.05)
            # The burst is simultaneous by construction: the two admitted requests keep their slots until every other request
            # has asked for one, however long this process takes to start. Otherwise a slow cold start lets the first call finish
            # (and free its slot) before the sixth request arrives, and the cap is never actually exercised.
            await asyncio.wait_for(decided['all'].wait(), 15)
            live['now'] -= 1
            return httpx.Response(200, content=MP3, request=httpx.Request('POST', url))
    class Gated(Provider):
        async def speech(self, content):
            result = await super().speech(content)
            if Provider.calls >= 4:
                decided['all'].set()
            return result
    monkeypatch.setattr('app.default_passage.httpx', shim(Slow))
    Provider.calls = 0
    async def burst():
        decided['all'] = asyncio.Event()
        return await asyncio.gather(*[passage_audio(settings, Gated(), CONTENT, None, control) for _ in range(6)])
    results = asyncio.run(burst())
    assert live['peak'] <= settings.passage_tts_max_concurrent == 2
    assert sorted(v.provider for _, v in results) == ['elevenlabs'] * 2 + ['openai'] * 4 and Provider.calls == 4
    assert ' '.join(records(caplog)).count('"fallback_reason": "concurrency_limit"') == 4


def test_reserve_default_is_atomic_across_threads_and_shared_with_accents(controls):
    from concurrent.futures import ThreadPoolExecutor
    settings, control = controls
    with ThreadPoolExecutor(max_workers=8) as pool:
        grants = list(pool.map(lambda _: control.reserve_default(10), range(8)))
    assert sum(isinstance(g, tuple) for g in grants) == 2 and grants.count('concurrency_limit') == 6
    assert control.read()['characters'] == 20


def test_flag_environment_parsing_and_startup_event(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv('AUDLI_DEFAULT_PASSAGE_ELEVENLABS_ENABLED', raising=False)
    assert Settings(_env_file=None).default_passage_elevenlabs_enabled is False
    monkeypatch.setenv('AUDLI_DEFAULT_PASSAGE_ELEVENLABS_ENABLED', 'true')
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'fixture')
    monkeypatch.setenv('AUDLI_PASSAGE_TTS_DAILY_CHARACTERS', '10000')
    on = Settings(_env_file=None, data_dir=tmp_path)
    assert on.default_passage_elevenlabs_enabled is True
    monkeypatch.setenv('AUDLI_DEFAULT_PASSAGE_ELEVENLABS_ENABLED', 'false')  # rollback = unset/false
    off = Settings(_env_file=None, data_dir=tmp_path)
    assert off.default_passage_elevenlabs_enabled is False
    from app.default_passage import emit_default_passage_configuration
    caplog.set_level(logging.INFO)
    emit_default_passage_configuration(on); emit_default_passage_configuration(off)
    first, second = records(caplog)
    assert '"default_passage_elevenlabs_enabled": true' in first and TUTOR_VOICE in first and 'fixture' not in first
    assert '"default_passage_elevenlabs_enabled": false' in second and '"voice_id": null' in second
