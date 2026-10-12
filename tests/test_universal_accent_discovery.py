"""AUD-30: returning lesson checkpoints share the original voice discovery flow."""
import asyncio
import httpx
import pytest
from app.accent_practice import discover, authorized_accent
from app.lesson import Lesson, now
from app.models import AccentPreferences
from app.onboarding import apply_accent_answer
from app.main import create_app
from conftest import ASGIClient
from test_persistence import repository_factory
from test_accent_preference_offer import setup, reply, activate
from test_lesson_lifecycle import start, action
from test_auth import AccountClient, auth_response, USER_A
from app.config import Settings


def checkpoint(repo, phase):
    item = Lesson(id='existing', started_at=now(), active_since=now(),
                  name='Maya', focus='details', phase=phase,
                  reflection='Science', review_response='I’ll keep science in mind.')
    repo.create_lesson(item)
    return item


@pytest.mark.parametrize('text,status,accents', [
    ('Australian', 'preferred', ['australian']),
    ('British and Irish', 'preferred', ['british', 'irish']),
    ('None', 'no_preference', []),
    ('No thanks', 'no_preference', []),
])
def test_new_onboarding_preserves_spoken_preferences(text, status, accents):
    from app.models import LearnerProfile
    profile = apply_accent_answer(LearnerProfile(), text)
    assert profile.accent_preferences.status == status
    assert profile.accent_preferences.accents == accents
    assert profile.onboarding.accents_asked


def test_abandoned_scheduled_prompts_do_not_exhaust_discovery():
    history = []
    for index in range(4):
        history.append(Lesson(id=str(index), started_at=now(), active_since=now(),
            name='Maya', focus='details', accent_discovery=True))
    assert discover(AccentPreferences(), history, {'australian': object()})
    history[0].accent_discovery_resolved = True
    history[3].accent_discovery_resolved = True
    assert not discover(AccentPreferences(), history, {'australian': object()})


@pytest.mark.parametrize('phase', ['WELCOME_ACK', 'REVIEW_ACK', 'REVIEW'])
def test_existing_topic_checkpoint_gets_discovery_before_passage(repository_factory, tmp_path, audio_bytes, phase):
    repo = repository_factory(); client, provider, _ = setup(repo, tmp_path)
    checkpoint(repo, phase)
    item = start(client)
    assert item['phase'] == phase  # Resume preserves the current spoken checkpoint.
    if phase == 'REVIEW':
        item = reply(client, provider, item, audio_bytes, 'Science')
    item = action(client, item, 'heard')
    assert item['phase'] == 'REVIEW' and 'English accents you find difficult' in item['prompt']
    assert repo.lesson().reflection == 'Science'
    assert repo.lesson().exercises_completed == 0
    item = reply(client, provider, item, audio_bytes, 'Australian')
    assert repo.profile().accent_preferences.accents == ['australian']
    assert authorized_accent(repo.lesson()) is None
    assert 'Would you like to try' in item['prompt']
    item = reply(client, provider, item, audio_bytes, 'No thanks')
    assert authorized_accent(repo.lesson()) is None
    assert repo.profile().accent_preferences.accents == ['australian']
    item = action(client, item, 'heard')
    assert 'English accents you find difficult' not in (item['prompt'] or '')


@pytest.mark.parametrize('prefs', [AccentPreferences(status='no_preference', response='No thanks'),
    AccentPreferences(status='preferred', accents=['australian'], response='Australian')])
def test_saved_response_does_not_restart_discovery(repository_factory, tmp_path, prefs):
    repo = repository_factory(); client, _, _ = setup(repo, tmp_path)
    profile = repo.profile(); profile.accent_preferences = prefs; repo.save_profile(profile)
    checkpoint(repo, 'REVIEW_ACK')
    item = action(client, start(client), 'heard')
    assert item['phase'] == 'TRANSITION' and not repo.lesson().accent_discovery
    assert repo.profile().accent_preferences == prefs


def test_ambiguous_discovery_respects_existing_cooldown(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); client, provider, _ = setup(repo, tmp_path)
    checkpoint(repo, 'REVIEW_ACK')
    item = action(client, start(client), 'heard')
    item = reply(client, provider, item, audio_bytes, 'I enjoy science')
    assert repo.profile().accent_preferences.status == 'unspecified'
    assert repo.lesson().accent_discovery_resolved
    item = action(client, item, 'heard')
    assert item['phase'] == 'TRANSITION'
    assert not discover(repo.profile().accent_preferences, repo.accent_lesson_history(), {'australian': object()})


def test_voice_update_and_none_survive_reopening(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); client, provider, _ = setup(repo, tmp_path, discovery=False)
    item = reply(client, provider, start(client), audio_bytes, 'Australian accents are difficult for me')
    assert repo.profile().accent_preferences.accents == ['australian']
    assert authorized_accent(repo.lesson()) is None
    item = action(client, item, 'heard')
    item = reply(client, provider, item, audio_bytes, 'No thanks')
    item = action(client, item, 'heard')
    # A later ordinary topic conversation can update the saved preference.
    stored = repo.lesson(); stored.phase = 'REVIEW'; repo.save_lesson(stored, stored.revision)
    item = client.get('/api/lessons/current').json()
    item = reply(client, provider, item, audio_bytes, 'Irish accents are difficult for me')
    assert repository_factory().profile().accent_preferences.accents == ['irish']
    assert authorized_accent(repo.lesson()) is None


def test_preferences_survive_logout_and_new_verified_login(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(USER_A); _, provider, base = setup(repo, tmp_path)
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    settings = Settings(_env_file=None, data_dir=tmp_path, provider=base.provider,
        auth_mode='supabase', supabase_url='https://project.supabase.co',
        supabase_publishable_key='sb_publishable_test',
        elevenlabs_api_key='fixture', accent_practice_enabled=True,
        passage_tts_daily_characters=5000, passage_voices=base.passage_voices)
    app = create_app(settings, provider, repository_factory(None), auth_client=auth)
    try:
        signed_in = AccountClient(app, provider, 'valid-a')
        item = reply(signed_in, provider, start(signed_in), audio_bytes, 'Australian and Irish')
        assert ASGIClient(app, provider).get('/api/profile').status_code == 401
        auth_response_before = AccountClient(app, provider, 'valid-a').get('/api/profile')
        assert auth_response_before.json()['profile']['accent_preferences']['accents'] == ['australian', 'irish']
        assert AccountClient(app, provider, 'valid-b').get('/api/profile').json()['profile']['accent_preferences']['status'] == 'unspecified'
    finally:
        app.state.repository.close()
        asyncio.run(auth.aclose())


@pytest.mark.parametrize('text', ['Can we try Australian instead?', 'Try a Scottish accent instead'])
def test_explicit_practice_request_resolves_returning_discovery(repository_factory, tmp_path, audio_bytes, text):
    repo = repository_factory(); client, provider, _ = setup(repo, tmp_path)
    checkpoint(repo, 'REVIEW_ACK')
    item = action(client, start(client), 'heard')
    item = reply(client, provider, item, audio_bytes, text)
    assert repo.lesson().accent_discovery_resolved
    item = action(client, item, 'heard')
    assert item['phase'] == 'TRANSITION'
    assert repo.profile().accent_preferences.status == 'unspecified'
