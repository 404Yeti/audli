"""AUD-32 conversations through real upload/answer/checkpoint/routing endpoints.

Synthetic recognition and provider audio only; no paid calls or learner content.
"""
import pytest
from app.accent_practice import authorized_accent
from app.lesson import Lesson, now
from app.main import create_app
from app.models import AccentPreferences, Transcription
from app.onboarding import ProfileExtraction
from conftest import ASGIClient
from test_persistence import repository_factory
from test_accent_preference_offer import setup, reply, activate
from test_accent_acceptance_reliability import mock_passage
from test_lesson_lifecycle import start, action, complete_exercise
from uuid import uuid4


CONTEXTS = ['new', 'existing', 'discovery', 'offer', 'resumed']
INTENTS = [
    ('disclosure', 'Australian accents are difficult for me.'),
    ('request', 'Can we use Australian for the next passage?'),
    ('accept', "Sure, let's try that."),
    ('decline', 'No, not today.'),
    ('suppress', 'Please stop asking me about accents.'),
    ('ambiguous', 'Maybe later.'),
    ('topic', "Let's talk about travel."),
]


def conversation(repo, tmp_path, context):
    client, provider, settings = setup(repo, tmp_path)
    if context in ('existing', 'offer'):
        profile = repo.profile()
        profile.accent_preferences = AccentPreferences(status='preferred', accents=['australian'])
        repo.save_profile(profile)
        if context == 'existing':
            old = Lesson(id=str(uuid4()), started_at=now(), active_since=now(),
                         name='Maya', focus='details', status='completed', phase='COMPLETED')
            repo.create_lesson(old)  # Offer cadence skips this next lesson.
    if context == 'resumed':
        saved = Lesson(id=str(uuid4()), started_at=now(), active_since=now(),
                       name='Maya', focus='details', phase='REVIEW', reflection='science')
        repo.create_lesson(saved)
    item = start(client)
    if context == 'discovery':
        stored = repo.lesson(); stored.phase = 'REVIEW'
        repo.save_lesson(stored, stored.revision)
        item = client.get('/api/lessons/current').json()
    async def topics(*args):
        return ProfileExtraction(name=None, target_language=None, goal=None, target_situations=None, interests=['travel'])
    provider.extract_profile = topics
    return client, provider, settings, item


@pytest.mark.parametrize('context', CONTEXTS)
@pytest.mark.parametrize('kind,text', INTENTS)
def test_seven_intents_do_not_compete_across_conversation_states(
        repository_factory, tmp_path, audio_bytes, context, kind, text):
    repo = repository_factory()
    client, provider, settings, item = conversation(repo, tmp_path, context)
    before = repo.profile().model_copy(deep=True)
    offered = bool(repo.lesson().accent_offered)
    discovering = bool(repo.lesson().accent_discovery)
    item = reply(client, provider, item, audio_bytes, text)
    grant = authorized_accent(repo.lesson())
    assert grant == ('australian' if kind == 'request' or kind == 'accept' and offered else None)
    if kind == 'request' or kind == 'accept' and offered:
        assert 'next new passage only' in item['prompt']
        assert 'usual passage voice' not in item['prompt'] and 'won’t' not in item['prompt']
    if kind == 'disclosure':
        assert repo.profile().accent_preferences.accents == ['australian']
        assert repo.lesson().accent_consent.accepted_at is None
        if offered:
            assert not repo.lesson().accent_confirmation  # No repeated offer for a disclosure.
        else:
            if item['phase'] != 'REVIEW':
                item = action(client, item, 'heard')
            assert item['phase'] == 'REVIEW' and 'Would you like to try' in item['prompt']
    elif kind == 'suppress' or kind == 'decline' and discovering:
        assert repo.profile().accent_preferences.status == 'no_preference'
    else:
        assert repo.profile().accent_preferences == before.accent_preferences
    if kind == 'topic':
        assert repo.profile().interests == ['travel']
    if kind in ('ambiguous', 'accept') and not grant:
        reopened = ASGIClient(create_app(settings, provider, repository_factory()), provider)
        assert reopened.get('/api/lessons/current').json()['prompt'] == item['prompt']
        if offered:
            assert item['phase'] == 'REVIEW' and '?' in item['prompt']
            item = reply(reopened, provider, item, audio_bytes, 'Maybe later.')
        # With no target, defer rather than invent a voice choice or force an answer.
        assert item['phase'] in ('WELCOME_ACK', 'REVIEW_ACK') and not repo.lesson().accent_confirmation
        assert authorized_accent(repo.lesson()) is None
        item = action(reopened, item, 'heard')
        assert 'English accents you find difficult' not in (item['prompt'] or '')
        assert not repo.lesson().accent_confirmation  # No immediate discovery interruption after deferral.
    after = repo.profile()
    assert after.difficulty == before.difficulty and after.listening_profile == before.listening_profile
    assert after.completed_attempts == before.completed_attempts


@pytest.mark.parametrize('text', [
    "Can we practice Australian? I don't want the usual accent.",
    'Can we use an Australian accent, not the usual voice?',
    "I don't want a British accent; can we use Australian for the next passage?",
    'Please stop asking me about accents. Can we use Australian for the next passage?',
])
@pytest.mark.parametrize('context', ['discovery', 'offer', 'resumed'])
def test_explicit_request_precedes_discovery_and_unrelated_negative_words(
        repository_factory, tmp_path, audio_bytes, context, text):
    repo = repository_factory()
    client, provider, _, item = conversation(repo, tmp_path, context)
    item = reply(client, provider, item, audio_bytes, text)
    assert authorized_accent(repo.lesson()) == 'australian'
    assert 'next new passage only' in item['prompt'] and 'usual' not in item['prompt']
    assert repo.profile().accent_preferences.status == (
        'no_preference' if text.startswith('Please stop') else
        'preferred' if context == 'offer' else 'unspecified')


@pytest.mark.parametrize('text', ['Can we use Welsh for the next passage?',
                                'Can we try a Singaporean accent?'])
def test_unsupported_request_never_substitutes_an_offered_accent(
        repository_factory, tmp_path, audio_bytes, text):
    repo = repository_factory(); client, provider, _, item = conversation(repo, tmp_path, 'offer')
    before = repo.profile().accent_preferences
    item = reply(client, provider, item, audio_bytes, text)
    assert 'isn’t available' in item['prompt'] and 'usual passage voice' in item['prompt']
    assert authorized_accent(repo.lesson()) is None
    assert repo.profile().accent_preferences == before


@pytest.mark.parametrize('text', ['Maybe try Australian', 'Can we try Australian or Irish?'])
def test_spontaneous_uncertainty_clarifies_once_without_saving_opt_out(
        repository_factory, tmp_path, audio_bytes, text):
    repo = repository_factory(); client, provider, _, item = conversation(repo, tmp_path, 'discovery')
    item = reply(client, provider, item, audio_bytes, text)
    assert item['phase'] == 'REVIEW' and '?' in item['prompt']
    assert authorized_accent(repo.lesson()) is None
    assert repo.profile().accent_preferences.status == 'unspecified'
    item = reply(client, provider, item, audio_bytes, 'Australian')
    assert authorized_accent(repo.lesson()) == 'australian'
    assert 'next new passage only' in item['prompt']


@pytest.mark.parametrize('saved', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_spontaneous_request_retries_persists_and_routes_exactly_one_new_passage(
        repository_factory, tmp_path, audio_bytes, exercise, monkeypatch, saved, fail):
    repo = repository_factory(); profile = repo.profile(); profile.difficulty.speech_rate = 1.0
    repo.save_profile(profile)
    saved_id = str(uuid4())
    if saved:
        repo.save_exercise(saved_id, exercise.model_copy(update={'speech_rate': 1.0}),
                           profile.difficulty, saved_id + '.wav', audio=audio_bytes)
    client, provider, settings, item = conversation(repo, tmp_path, 'resumed')
    calls = mock_passage(monkeypatch, audio_bytes, fail=fail)
    async def transcription(*args):
        return Transcription(text='Can we use Australian for the next passage?', confidence=.99)
    provider.transcribe = transcription
    path = f"/api/lessons/{item['id']}"
    # Lost upload response: retry must not overwrite the committed transcription.
    assert client.post(path + '/attempts', data={'revision': item['revision']},
                       files={'audio': ('request.wav', audio_bytes, 'audio/wav')}).status_code == 200
    assert client.post(path + '/attempts', data={'revision': item['revision']},
                       files={'audio': ('request.wav', audio_bytes, 'audio/wav')}).status_code == 409
    reopened = repository_factory(); client = ASGIClient(create_app(settings, provider, reopened), provider)
    pending = client.get('/api/lessons/current').json()
    item = action(client, pending, 'answer')
    grant = reopened.lesson().accent_consent.model_copy(deep=True)
    assert client.post(path + '/answer', json={'revision': pending['revision']}).status_code == 409
    assert reopened.lesson().accent_consent == grant and authorized_accent(reopened.lesson()) == 'australian'
    assert ('saved recording first' in item['prompt']) is saved
    item = action(client, item, 'heard')
    item = action(client, item, 'heard')
    item = action(client, item, 'exercise')
    if saved:
        assert item['exercise']['id'] == saved_id and calls == []
        assert client.get(item['exercise']['audio_url']).content == audio_bytes
        complete_exercise(client, item, audio_bytes,
                          {'main_idea': 'partially_demonstrated', 'details': 'partially_demonstrated'})
        item = action(client, item, 'advance')
        item = action(client, item, 'exercise')
    voice = item['exercise']['passage_voice']
    assert voice['accent'] == ('default' if fail else 'australian')
    assert voice['fallback'] is fail and len(calls) == 1
    assert ('usual passage voice' in item['introduction_prompt']) is fail
    assert reopened.lesson().accent_consent.state == 'consumed'
    selected_audio = client.get(item['exercise']['audio_url']).content
    assert action(client, item, 'exercise')['exercise']['passage_voice'] == voice
    assert client.get(item['exercise']['audio_url']).content == selected_audio and len(calls) == 1
    assert settings.elevenlabs_voice_id == 'yKYzqEa22xh5PdidhN70'


@pytest.mark.parametrize('text,granted', [
    ("Sure, let's try that. Let's talk about travel.", True),
    ("No, not today. Let's talk about travel.", False),
    ("Can we use Australian for the next passage about travel?", True),
    ("Can we use Australian for the next passage? Let's talk about travel.", True),
])
def test_accent_decision_and_topic_change_share_one_persisted_checkpoint(
        repository_factory, tmp_path, audio_bytes, text, granted):
    repo = repository_factory(); client, provider, _, item = conversation(repo, tmp_path, 'offer')
    item = reply(client, provider, item, audio_bytes, text)
    assert authorized_accent(repo.lesson()) == ('australian' if granted else None)
    assert repo.profile().interests == ['travel']
    assert repo.profile().accent_preferences.accents == ['australian']
    assert '?' not in item['prompt']


@pytest.mark.parametrize('text', ['Can I try an Australian accent?',
                                "I'd like Australian accent practice.",
                                'Please use Australian English for our next listening passage.',
                                'I want an Australian listening exercise.'])
def test_clear_current_requests_do_not_require_a_previous_offer(
        repository_factory, tmp_path, audio_bytes, text):
    repo = repository_factory(); client, provider, _, item = conversation(repo, tmp_path, 'existing')
    assert repo.lesson().accent_offered is None
    item = reply(client, provider, item, audio_bytes, text)
    assert authorized_accent(repo.lesson()) == 'australian'
    assert 'next new passage only' in item['prompt']


@pytest.mark.parametrize('kind,text', INTENTS)
@pytest.mark.parametrize('saved', [False, True])
def test_all_intents_preserve_saved_audio_and_authorize_only_the_next_new_passage(
        repository_factory, tmp_path, audio_bytes, exercise, monkeypatch, kind, text, saved):
    repo = repository_factory(); profile = repo.profile(); profile.difficulty.speech_rate = 1.0
    repo.save_profile(profile)
    saved_id = str(uuid4())
    if saved:
        repo.save_exercise(saved_id, exercise.model_copy(update={'speech_rate': 1.0}),
                           profile.difficulty, saved_id + '.wav', audio=audio_bytes)
    client, provider, _, item = conversation(repo, tmp_path, 'offer')
    calls = mock_passage(monkeypatch, audio_bytes)
    item = reply(client, provider, item, audio_bytes, text)
    expected = kind in ('request', 'accept')
    if item['phase'] == 'REVIEW':
        item = reply(client, provider, item, audio_bytes, 'No thanks')
    item = action(client, item, 'heard')
    assert item['phase'] == 'TRANSITION'
    item = action(client, item, 'heard'); item = action(client, item, 'exercise')
    if saved:
        assert item['exercise']['id'] == saved_id and calls == []
        assert client.get(item['exercise']['audio_url']).content == audio_bytes
        complete_exercise(client, item, audio_bytes,
                          {'main_idea': 'partially_demonstrated', 'details': 'partially_demonstrated'})
        item = action(client, item, 'advance'); item = action(client, item, 'exercise')
        assert client.get(f'/api/exercises/{saved_id}/audio').content == audio_bytes
    assert item['exercise']['passage_voice']['accent'] == ('australian' if expected else 'default')
    assert len(calls) == int(expected)
    assert authorized_accent(repo.lesson()) is None
    assert action(client, item, 'exercise')['exercise'] == item['exercise']
    assert len(calls) == int(expected)


def test_conflicting_request_and_later_refusal_cannot_authorize_a_passage(
        repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); client, provider, _, item = conversation(repo, tmp_path, 'discovery')
    item = reply(client, provider, item, audio_bytes, 'Can we try Australian? No, not today.')
    assert authorized_accent(repo.lesson()) is None and item['phase'] == 'REVIEW'
    assert 'You can say yes or no' in item['prompt']
    item = reply(client, provider, item, audio_bytes, 'No thanks')
    assert authorized_accent(repo.lesson()) is None and not repo.lesson().accent_confirmation
    assert repo.profile().accent_preferences.status == 'unspecified'


@pytest.mark.parametrize('text', ["Let's talk about British music.", "Let's talk about Australian wildlife."])
def test_nationality_in_a_topic_cannot_accept_or_replace_a_pending_offer(
        repository_factory, tmp_path, audio_bytes, text):
    repo = repository_factory(); client, provider, _, item = conversation(repo, tmp_path, 'offer')
    before = repo.profile().accent_preferences
    item = reply(client, provider, item, audio_bytes, text)
    assert authorized_accent(repo.lesson()) is None
    assert repo.profile().accent_preferences == before
    assert repo.profile().interests == ['travel']
    assert item['phase'] == 'REVIEW' and 'You can say yes or no' in item['prompt']
    item = reply(client, provider, item, audio_bytes, 'No thanks')
    assert authorized_accent(repo.lesson()) is None


def test_explicit_request_still_works_after_a_previous_preference_opt_out(
        repository_factory, tmp_path, audio_bytes):
    repo = repository_factory(); client, provider, _ = setup(repo, tmp_path, discovery=False)
    profile = repo.profile(); profile.completed_attempts = 7; repo.save_profile(profile)
    before = repo.profile().model_copy(deep=True)
    item = start(client)
    assert not repo.lesson().accent_discovery and repo.lesson().accent_offered is None
    item = reply(client, provider, item, audio_bytes, 'Can we use Australian for the next passage?')
    assert authorized_accent(repo.lesson()) == 'australian'
    assert 'next new passage only' in item['prompt'] and 'won’t' not in item['prompt']
    assert repo.profile() == before  # Explicit current consent does not change the saved opt-out or progress.
