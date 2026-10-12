"""AUD-35: real lesson checkpoints/routing with synthetic provider responses."""
import asyncio
import pytest
import httpx
from types import SimpleNamespace
from app.main import create_app
from app.accent_practice import authorized_accent
from conftest import ASGIClient
from test_persistence import repository_factory
from test_accent_preference_offer import setup, reply
from test_lesson_lifecycle import start, action, complete_exercise


@pytest.fixture(autouse=True)
def activate(monkeypatch):
    from accent_control_helpers import enable_test_controls
    enable_test_controls(monkeypatch)


def passage_fixture(monkeypatch, audio_bytes, failure=None):
    calls = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append((url, kwargs['json']))
            assert url.endswith('/tyepWYJJwJM9TTFIg5U7')
            if failure == 'timeout':
                raise httpx.ReadTimeout('private provider details must not be logged')
            return httpx.Response(failure or 200, content=audio_bytes,
                                 request=httpx.Request('POST', url))
    monkeypatch.setattr('app.accent_practice.httpx', SimpleNamespace(
        AsyncClient=lambda **kwargs: Client(), HTTPError=httpx.HTTPError))
    return calls


def requested(repo, tmp_path, audio_bytes, rate):
    client, provider, settings = setup(repo, tmp_path)
    profile = repo.profile(); profile.difficulty.speech_rate = rate
    repo.save_profile(profile)
    before = repo.profile().model_copy(deep=True)
    item = reply(client, provider, start(client), audio_bytes,
                 'Can we use Australian for the next passage?')
    assert authorized_accent(repo.lesson()) == 'australian'
    assert 'next new passage only' in item['prompt']
    grant = repo.lesson().accent_consent.model_copy(deep=True)
    return client, provider, settings, item, before, grant


def generate(client, item):
    item = action(client, item, 'heard')
    item = action(client, item, 'heard')
    assert item['phase'] == 'EXERCISE'
    return action(client, item, 'exercise')


@pytest.mark.parametrize('rate', [.7, .75, .8, .85, .9, .95, 1.0, 1.05, 1.1, 1.15])
def test_adaptive_rate_routes_clara_and_preserves_one_passage_scope(
        repository_factory, tmp_path, audio_bytes, monkeypatch, rate):
    repo = repository_factory()
    calls = passage_fixture(monkeypatch, audio_bytes)
    client, provider, settings, item, before, grant = requested(repo, tmp_path, audio_bytes, rate)
    reopened = repository_factory()
    assert reopened.lesson().accent_consent == grant
    client = ASGIClient(create_app(settings, provider, reopened), provider)
    item = generate(client, client.get('/api/lessons/current').json())
    voice = item['exercise']['passage_voice']
    assert voice['accent'] == 'australian' and voice['voice_id'] == 'tyepWYJJwJM9TTFIg5U7'
    assert voice['provider'] == 'elevenlabs' and not voice['fallback']
    assert 'usual passage voice' not in item['introduction_prompt']
    assert len(calls) == 1 and calls[0][1]['voice_settings']['speed'] == rate
    assert calls[0][1]['model_id'] == 'eleven_flash_v2_5'
    assert reopened.lesson().accent_consent.state == 'consumed'
    assert reopened.profile() == before
    stored_audio = client.get(item['exercise']['audio_url']).content
    configured_voices = settings.passage_voices
    settings.passage_voices = {}
    assert action(client, item, 'exercise')['exercise'] == item['exercise']
    assert client.get(item['exercise']['audio_url']).content == stored_audio
    assert len(calls) == 1
    assert settings.elevenlabs_voice_id == 'yKYzqEa22xh5PdidhN70'
    settings.passage_voices = configured_voices
    complete_exercise(client, item, audio_bytes)
    item = action(client, client.get('/api/lessons/current').json(), 'advance')
    item = action(client, item, 'exercise')
    assert item['exercise']['passage_voice']['accent'] == 'default' and len(calls) == 1


@pytest.mark.parametrize('failure', ['disabled', 'kill_switch', 'missing_voice', 'missing_key',
                                    'budget', 'concurrency', 'unsupported_rate', 'minimum_rate', 403, 503, 'timeout'])
def test_fallback_is_honest_persisted_and_replayable(
        repository_factory, tmp_path, audio_bytes, monkeypatch, caplog, failure):
    repo = repository_factory()
    calls = passage_fixture(monkeypatch, audio_bytes, failure if failure in (403, 503, 'timeout') else None)
    rate = .65 if failure == 'unsupported_rate' else .6 if failure == 'minimum_rate' else .8
    client, provider, settings, item, before, grant = requested(repo, tmp_path, audio_bytes, rate)
    if failure == 'disabled': settings.accent_practice_enabled = False
    if failure == 'kill_switch': client.app.state.accent_controls.configure(enabled=False)
    if failure == 'missing_voice': settings.passage_voices = {}
    if failure == 'missing_key': settings.elevenlabs_api_key = None
    if failure == 'budget': client.app.state.accent_controls.configure(enabled=True, daily_limit=1)
    if failure == 'concurrency':
        from accent_control_helpers import scope
        other = repository_factory('00000000-0000-0000-0000-000000000035')
        other_lesson = scope(other, settings, 'australian')
        controls = client.app.state.accent_controls
        controls.configure(enabled=True, concurrent_limit=1)
        assert controls.reserve(other.learner_id, other_lesson.id, 0, 'australian', 1)
    item = generate(client, item)
    voice = item['exercise']['passage_voice']
    assert voice['accent'] == 'default' and voice['fallback']
    assert 'couldn’t use the requested accent for this recording' in item['introduction_prompt']
    assert 'unavailable' not in item['introduction_prompt']
    assert len(calls) == (1 if failure in (403, 503, 'timeout') else 0)
    assert repo.lesson().accent_consent.state == 'consumed' and authorized_accent(repo.lesson()) is None
    assert repo.profile() == before
    original = client.get(item['exercise']['audio_url']).content
    restarted = ASGIClient(create_app(settings, provider, repository_factory()), provider) if failure != 'missing_key' else client
    restored = restarted.get('/api/lessons/current').json()
    assert restored['introduction_prompt'] == item['introduction_prompt']
    assert action(restarted, restored, 'exercise')['exercise'] == item['exercise']
    assert restarted.get(item['exercise']['audio_url']).content == original
    assert 'private provider details' not in caplog.text
    assert 'Accent passage outcome=' in caplog.text


def test_missing_and_invalid_configuration_fail_closed():
    from app.config import Settings
    from app.accent_practice import VerifiedPassageVoice
    with pytest.raises(ValueError):
        VerifiedPassageVoice(voice_id='invalid', validation='Synthetic approved fixture')
    with pytest.raises(ValueError):
        Settings(_env_file=None, passage_voices={'australian': {
            'voice_id': 'tyepWYJJwJM9TTFIg5U7', 'validation': 'Synthetic approved fixture'}})


def test_authenticated_adaptive_request_survives_relogin_and_audio_ownership(
        repository_factory, tmp_path, audio_bytes, monkeypatch):
    from app.config import Settings
    from test_auth import AccountClient, auth_response, USER_A, USER_B
    root = repository_factory(None); repo = root.for_learner(USER_A)
    _, provider, config = setup(repo, tmp_path)
    profile = repo.profile(); profile.difficulty.speech_rate = .8
    repo.save_profile(profile)
    settings = Settings(**(config.model_dump() | {'auth_mode': 'supabase',
        'supabase_url': 'https://project.supabase.co',
        'supabase_publishable_key': 'sb_publishable_test'}))
    calls = passage_fixture(monkeypatch, audio_bytes)
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    try:
        app = create_app(settings, provider, root, auth)
        client = AccountClient(app, provider, 'valid-a')
        item = reply(client, provider, start(client), audio_bytes,
                     'Can we use Australian for the next passage?')
        grant = repo.lesson().accent_consent.model_copy(deep=True)
        assert grant.state == 'accepted'
        restarted_root = repository_factory(None)
        restarted_app = create_app(settings, provider, restarted_root, auth)
        client = AccountClient(restarted_app, provider, 'valid-a')
        assert restarted_root.for_learner(USER_A).lesson().accent_consent == grant
        item = generate(client, client.get('/api/lessons/current').json())
        assert item['exercise']['passage_voice']['voice_id'] == 'tyepWYJJwJM9TTFIg5U7'
        assert calls[0][1]['voice_settings']['speed'] == .8 and len(calls) == 1
        audio_url = item['exercise']['audio_url']
        assert client.get(audio_url).status_code == 200
        assert ASGIClient(restarted_app, provider).get(audio_url).status_code == 401
        assert AccountClient(restarted_app, provider, 'valid-b').get(audio_url).status_code == 404
        assert restarted_root.for_learner(USER_B).lesson() is None
        assert repo.profile().accent_preferences.status == 'unspecified'
        assert repo.lesson().accent_consent.state == 'consumed'
    finally:
        asyncio.run(auth.aclose())
