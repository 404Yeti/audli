"""AUD-31: natural consent, bounded clarification, retry and saved-recording scope.

Every provider response is synthetic. No paid services or production data.
"""
from datetime import timedelta
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from app.accent_practice import authorized_accent, intent, resolve
from app.lesson import prompt
from app.config import Settings
from app.models import AccentPreferences, Transcription
from app.main import create_app
from conftest import ASGIClient
from test_persistence import repository_factory
from test_accent_preference_offer import setup, reply
from test_accent_practice import CANDIDATE_IDS, VOICE
from test_accent_consent import checkpoint
from test_lesson_lifecycle import start, action, complete_exercise
from test_auth import AccountClient, auth_response, USER_A, USER_B


@pytest.fixture(autouse=True)
def activate(monkeypatch):
    from accent_control_helpers import enable_test_controls
    enable_test_controls(monkeypatch)


def mock_passage(monkeypatch, audio_bytes, *, fail=False):
    calls = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append(url)
            assert url.endswith('/' + CANDIDATE_IDS['australian'])
            return httpx.Response(503 if fail else 200, content=audio_bytes,
                                  request=httpx.Request('POST', url))
    monkeypatch.setattr('app.accent_practice.httpx', SimpleNamespace(
        AsyncClient=lambda **kwargs: Client(), HTTPError=httpx.HTTPError))
    return calls


def offered(repo, tmp_path, audio_bytes, *, welcome=False):
    client, provider, settings = setup(repo, tmp_path)
    if welcome:
        profile = repo.profile()
        profile.accent_preferences = AccentPreferences(status='preferred', accents=['australian'])
        repo.save_profile(profile)
    item = start(client)
    if not welcome:
        item = reply(client, provider, item, audio_bytes, 'Australian accents are difficult for me.')
        item = action(client, item, 'heard')
    return client, provider, settings, item


@pytest.mark.parametrize('text', ["Yes, please.", "Yes, I'd like to try that.",
    "Yeah, let's do it!", "Sure, that sounds good.", 'Absolutely.',
    'I would like to try it.', "Yes, I'd like to practice Australian English.",
    "Yes, I want to try it.", "I'd like to try Australian English."])
def test_complete_natural_affirmatives_require_an_offer(text):
    assert intent(text, 'australian') == ('select', 'australian')
    if 'Australian' not in text:
        assert intent(text) == (None, None)


@pytest.mark.parametrize('text', ['No, thanks.', 'No thanks', 'Not today', 'I’d rather not'])
def test_explicit_refusal_never_clarifies(text):
    item = checkpoint()
    assert 'usual passage voice' in resolve(item, text, {'british': VOICE})
    assert item.accent_consent.state == 'declined' and not item.accent_confirmation


@pytest.mark.parametrize('welcome', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_natural_acceptance_persists_routes_once_and_announces_actual_fallback(
        repository_factory, tmp_path, audio_bytes, monkeypatch, welcome, fail):
    repo = repository_factory()
    client, provider, settings, item = offered(repo, tmp_path, audio_bytes, welcome=welcome)
    calls = mock_passage(monkeypatch, audio_bytes, fail=fail)
    item = reply(client, provider, item, audio_bytes, "Yes, I'd like to try that.")
    assert 'Australian English' in item['prompt'] and 'usual passage voice' not in item['prompt']
    assert authorized_accent(repo.lesson()) == 'australian'
    assert not repo.lesson().accent_confirmation
    reopened = repository_factory()
    assert reopened.lesson().accent_consent == repo.lesson().accent_consent
    restarted = ASGIClient(create_app(settings, provider, reopened), provider)
    item = restarted.get('/api/lessons/current').json()
    item = action(restarted, item, 'heard'); assert item['phase'] == 'TRANSITION'
    item = action(restarted, item, 'heard'); item = action(restarted, item, 'exercise')
    voice = item['exercise']['passage_voice']
    assert voice['accent'] == ('default' if fail else 'australian')
    assert voice['fallback'] is fail
    assert ('usual passage voice' in item['introduction_prompt']) is fail
    assert reopened.lesson().accent_consent.state == 'consumed'
    assert authorized_accent(reopened.lesson()) is None
    audio = restarted.get(item['exercise']['audio_url']).content
    settings.passage_voices = {}
    assert action(restarted, item, 'exercise')['exercise'] == item['exercise']
    assert restarted.get(item['exercise']['audio_url']).content == audio
    assert len(calls) == 1
    assert settings.elevenlabs_voice_id == 'yKYzqEa22xh5PdidhN70'


@pytest.mark.parametrize('answer', ['Yes, please.', 'Maybe', 'No thanks'])
def test_ambiguous_offer_gets_one_persisted_clarification(
        repository_factory, tmp_path, audio_bytes, answer):
    repo = repository_factory(); client, provider, settings, item = offered(repo, tmp_path, audio_bytes)
    item = reply(client, provider, item, audio_bytes, 'Maybe')
    assert item['phase'] == 'REVIEW' and 'You can say yes or no' in item['prompt']
    assert 'usual passage voice' not in item['prompt'] and item['pending'] is None
    assert authorized_accent(repo.lesson()) is None
    restarted = ASGIClient(create_app(settings, provider, repository_factory()), provider)
    assert restarted.get('/api/lessons/current').json()['prompt'] == item['prompt']
    item = reply(restarted, provider, item, audio_bytes, answer)
    assert item['phase'] == 'REVIEW_ACK' and not repo.lesson().accent_confirmation
    assert authorized_accent(repo.lesson()) == ('australian' if answer.startswith('Yes') else None)
    assert ('usual passage voice' in item['prompt']) == (not answer.startswith('Yes'))
    assert action(restarted, item, 'heard')['phase'] == 'TRANSITION'


@pytest.mark.parametrize('response', ['I enjoy science', 'Yes, work was busy today'])
def test_legacy_redundant_confirmation_cannot_revoke_accepted_consent(response):
    item = checkpoint(); resolve(item, 'Yes', {'british': VOICE})
    grant = item.accent_consent.model_copy(deep=True)
    item.accent_confirmation = True  # Snapshot from the deployed reader.
    assert resolve(item, response, {'british': VOICE}) is None
    assert item.accent_consent == grant and authorized_accent(item) == 'british'
    assert not item.accent_confirmation


def test_lost_attempt_and_answer_responses_reload_without_overwriting_consent(
        repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); client, provider, settings, item = offered(repo, tmp_path, audio_bytes)
    transcriptions = []
    async def transcribe(*args):
        transcriptions.append(True)
        return Transcription(text='Yes, please.', confidence=.99)
    provider.transcribe = transcribe
    path = f"/api/lessons/{item['id']}"
    # Server commits, but the client loses the response before receiving the new revision.
    assert client.post(path + '/attempts', data={'revision': item['revision']},
                       files={'audio': ('reply.wav', audio_bytes, 'audio/wav')}).status_code == 200
    assert client.post(path + '/attempts', data={'revision': item['revision']},
                       files={'audio': ('reply.wav', audio_bytes, 'audio/wav')}).status_code == 409
    reopened = repository_factory(); restarted = ASGIClient(create_app(settings, provider, reopened), provider)
    pending = restarted.get('/api/lessons/current').json()
    assert pending['pending']['text'] == 'Yes, please.' and len(transcriptions) == 1
    accepted = action(restarted, pending, 'answer')
    grant = reopened.lesson().accent_consent.model_copy(deep=True)
    assert restarted.post(path + '/answer', json={'revision': pending['revision']}).status_code == 409
    assert reopened.lesson().accent_consent == grant
    assert reopened.lesson().pending is None and authorized_accent(reopened.lesson()) == 'australian'
    assert restarted.get('/api/lessons/current').json()['prompt'] == accepted['prompt']
    foreign = repository_factory(str(uuid4())); foreign.profile()
    stranger = ASGIClient(create_app(settings, provider, foreign), provider)
    assert stranger.get(path).status_code == 404
    assert stranger.post(path + '/answer', json={'revision': accepted['revision']}).status_code == 404


def test_saved_recording_keeps_audio_and_next_new_passage_consent(
        repository_factory, tmp_path, audio_bytes, exercise, monkeypatch):
    repo = repository_factory(); profile = repo.profile()
    profile.difficulty.speech_rate = 1.0
    repo.save_profile(profile)
    exercise = exercise.model_copy(update={'speech_rate': 1.0})
    saved_id = str(uuid4())
    repo.save_exercise(saved_id, exercise, repo.profile().difficulty, saved_id + '.wav', audio=audio_bytes)
    client, provider, settings, item = offered(repo, tmp_path, audio_bytes, welcome=True)
    calls = mock_passage(monkeypatch, audio_bytes)
    item = reply(client, provider, item, audio_bytes, 'Yes, please.')
    assert 'saved recording first' in item['prompt'] and 'next new passage only' in item['prompt']
    accepted_at = repo.lesson().accent_consent.accepted_at
    item = action(client, item, 'heard'); item = action(client, item, 'heard')
    item = action(client, item, 'exercise')
    assert item['exercise']['id'] == saved_id and len(calls) == 0
    assert client.get(item['exercise']['audio_url']).content == audio_bytes
    assert repo.lesson().accent_consent.exercise_number == 1
    assert repo.lesson().accent_consent.accepted_at == accepted_at  # No TTL extension.
    complete_exercise(client, item, audio_bytes,
                      {'main_idea': 'partially_demonstrated', 'details': 'partially_demonstrated'})
    item = action(client, item, 'advance')
    assert authorized_accent(repo.lesson()) == 'australian'
    item = action(client, item, 'exercise')
    assert item['exercise']['passage_voice']['voice_id'] == CANDIDATE_IDS['australian']
    assert repo.lesson().accent_consent.state == 'consumed' and len(calls) == 1
    assert client.get(f'/api/exercises/{saved_id}/audio').content == audio_bytes


def test_resume_expires_grant_without_silently_reconfirming(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); client, provider, _, item = offered(repo, tmp_path, audio_bytes, welcome=True)
    item = reply(client, provider, item, audio_bytes, 'Yes, please.')
    item = action(client, item, 'end'); item = action(client, item, 'resume')
    assert authorized_accent(repo.lesson()) is None and repo.lesson().accent_consent.state == 'expired'
    assert not repo.lesson().accent_confirmation
    assert 'has expired' in item['prompt'] and 'Perfect' not in item['prompt']


def test_expired_accepted_acknowledgment_does_not_promise_accent():
    item = checkpoint()
    item.welcome_response = resolve(item, 'Yes', {'british': VOICE})
    item.phase = 'WELCOME_ACK'
    item.accent_consent.accepted_at -= timedelta(seconds=600)
    assert authorized_accent(item) is None
    assert 'has expired' in prompt(item) and 'Perfect' not in prompt(item)


def test_interrupted_transcription_keeps_revision_and_can_retry(
        repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); client, provider, _, item = offered(repo, tmp_path, audio_bytes)
    before = repo.lesson().model_dump()
    async def interrupt():
        started = asyncio.Event()
        async def transcribe(*args):
            started.set()
            await asyncio.Event().wait()
        provider.transcribe = transcribe
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://testserver') as transport:
            task = asyncio.create_task(transport.post(f"/api/lessons/{item['id']}/attempts",
                data={'revision': item['revision']}, files={'audio': ('reply.wav', audio_bytes, 'audio/wav')}))
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(interrupt())
    assert repo.lesson().model_dump() == before
    item = reply(client, provider, item, audio_bytes, 'Yes, please.')
    assert authorized_accent(repo.lesson()) == 'australian'
    assert item['phase'] == 'REVIEW_ACK'


def test_authenticated_consent_survives_relogin_and_isolated_accounts(
        repository_factory, tmp_path, audio_bytes):
    root = repository_factory(None); repo = root.for_learner(USER_A)
    _, provider, config = setup(repo, tmp_path)
    profile = repo.profile()
    profile.accent_preferences = AccentPreferences(status='preferred', accents=['australian'])
    repo.save_profile(profile)
    settings = Settings(**(config.model_dump() | {'auth_mode': 'supabase',
        'supabase_url': 'https://project.supabase.co',
        'supabase_publishable_key': 'sb_publishable_test'}))
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    try:
        app = create_app(settings, provider, root, auth)
        a = AccountClient(app, provider, 'valid-a'); b = AccountClient(app, provider, 'valid-b')
        item = reply(a, provider, start(a), audio_bytes, 'Yes, please.')
        grant = repo.lesson().accent_consent.model_copy(deep=True)
        assert grant.state == 'accepted'
        path = f"/api/lessons/{item['id']}"
        assert ASGIClient(app, provider).get(path).status_code == 401
        assert b.get(path).status_code == 404
        assert b.post(path + '/answer', json={'revision': item['revision']}).status_code == 404
        reopened = repository_factory(None)
        relogged = AccountClient(create_app(settings, provider, reopened, auth), provider, 'valid-a')
        assert relogged.get('/api/lessons/current').json()['prompt'] == item['prompt']
        assert reopened.for_learner(USER_A).lesson().accent_consent == grant
        assert reopened.for_learner(USER_B).lesson() is None
        assert reopened.for_learner(USER_B).profile().accent_preferences.status == 'unspecified'
    finally:
        asyncio.run(auth.aclose())


def test_expired_grant_generates_truthful_default_introduction(
        repository_factory, tmp_path, audio_bytes, monkeypatch):
    repo = repository_factory(); client, provider, _, item = offered(repo, tmp_path, audio_bytes, welcome=True)
    calls = mock_passage(monkeypatch, audio_bytes)
    item = reply(client, provider, item, audio_bytes, 'Yes, please.')
    stored = repo.lesson(); stored.accent_consent.accepted_at -= timedelta(seconds=600)
    repo.save_lesson(stored, stored.revision)
    item = client.get('/api/lessons/current').json()
    assert 'has expired' in item['prompt']
    item = action(client, item, 'heard'); item = action(client, item, 'heard')
    item = action(client, item, 'exercise')
    assert item['exercise']['passage_voice']['accent'] == 'default'
    assert item['exercise']['passage_voice']['fallback'] is True
    assert 'usual passage voice' in item['introduction_prompt'] and not calls
    assert repo.lesson().accent_consent.state == 'expired'
