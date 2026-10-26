"""AUD-91: actual upload validation -> mocked STT transport -> current offer.

No private recordings or provider requests. Raw STT uncertainty is preserved.
"""
from datetime import timedelta

import pytest

from app.accent_practice import authorized_accent, CONSENT_SECONDS
from app.lesson import Lesson, AccentConsent, now
from app.lesson_recognition import pending_recognition
from app.models import AccentPreferences, Transcription
from app.recognition import SHORT_ASSESSMENT_WARNING, reliable_recognition
from test_short_reply_rule import provider_returning
from test_accent_preference_offer import setup
from test_lesson_lifecycle import start, action, lesson_client
from test_persistence import repository_factory


@pytest.fixture(autouse=True)
def activate(monkeypatch):
    from accent_control_helpers import enable_test_controls
    enable_test_controls(monkeypatch)


def offer_pending(text='Yes, please.', **updates):
    at = now()
    lesson = Lesson(id='fixture', started_at=at, active_since=at, name='Listener',
                    focus='details', accent_offered='australian',
                    pending=Transcription(text=text, confidence=.95, uncertainty=[SHORT_ASSESSMENT_WARNING]))
    return lesson.model_copy(update=updates), at


@pytest.mark.parametrize('text', ['Yes, please.', 'Sure.', 'Okay.', 'No, thanks.'])
@pytest.mark.parametrize('phase', ['WELCOME', 'REVIEW'])
def test_closed_question_alone_can_ignore_length_warning(text, phase):
    lesson, at = offer_pending(text, phase=phase)
    if phase == 'REVIEW':
        lesson.accent_confirmation = True
        lesson.accent_consent.accent = lesson.accent_offered
    before = lesson.model_dump()
    assert pending_recognition(lesson, .65, at) == 'accepted'
    assert not reliable_recognition(lesson.pending, .65)
    assert lesson.model_dump() == before


@pytest.mark.parametrize('confidence, accepted', [(None, False), (.64, False), (.65, True), (.66, True)])
def test_confidence_boundary_is_unchanged(confidence, accepted):
    lesson, at = offer_pending()
    lesson.pending.confidence = confidence
    assert (pending_recognition(lesson, .65, at) == 'accepted') is accepted


@pytest.mark.parametrize('text', ['', ' ', 'Maybe.', 'Not sure.', 'Yes, no.', 'Football.', 'Sure, but no.'])
def test_empty_unknown_and_mixed_answers_are_not_closed_choices(text):
    lesson, at = offer_pending(text)
    assert pending_recognition(lesson, .65, at) == 'uncertain'


@pytest.mark.parametrize('change', ['missing', 'resolved', 'discovery', 'topic', 'mismatched',
                                   'topic_with_old_target', 'missing_clarification_target',
                                   'accepted', 'expired', 'consumed', 'paused', 'completed', 'old', 'future'])
def test_no_missing_expired_or_stale_offer_exception(change):
    lesson, at = offer_pending()
    if change == 'missing': lesson.accent_offered = None
    if change == 'resolved': lesson.accent_resolved = True
    if change == 'discovery': lesson.accent_discovery = True
    if change == 'topic': lesson.phase = 'REVIEW'
    if change == 'mismatched':
        lesson.phase = 'REVIEW'; lesson.accent_confirmation = True
        lesson.accent_consent.accent = 'british'
    if change == 'topic_with_old_target':
        lesson.phase = 'REVIEW'; lesson.accent_resolved = True
        lesson.accent_consent.accent = 'australian'
    if change == 'missing_clarification_target':
        lesson.phase = 'REVIEW'; lesson.accent_resolved = True; lesson.accent_confirmation = True
    if change in ('accepted', 'expired', 'consumed'): lesson.accent_consent.state = change
    if change in ('paused', 'completed'): lesson.status = change
    if change == 'old': at += timedelta(seconds=CONSENT_SECONDS)
    if change == 'future': at -= timedelta(seconds=1)
    before = lesson.model_dump()
    assert pending_recognition(lesson, .65, at) == 'uncertain'
    assert lesson.model_dump() == before


def test_other_uncertainty_and_configured_threshold_still_block():
    lesson, at = offer_pending()
    assert pending_recognition(lesson, .96, at) == 'uncertain'
    lesson.pending.uncertainty.append('Other provider concern')
    assert pending_recognition(lesson, .65, at) == 'uncertain'


def test_last_valid_instant_and_bounded_clarification():
    lesson, at = offer_pending(phase='REVIEW', accent_confirmation=True, accent_resolved=True,
                              accent_consent=AccentConsent(accent='australian'))
    assert pending_recognition(lesson, .65, at + timedelta(seconds=CONSENT_SECONDS - .001)) == 'accepted'
    assert pending_recognition(lesson, .65, at + timedelta(seconds=CONSENT_SECONDS)) == 'uncertain'


def prepared(repository_factory, tmp_path, text, probability=.95, initial_offer='australian'):
    repo = repository_factory()
    client, provider, settings = setup(repo, tmp_path)
    profile = repo.profile()
    profile.accent_preferences = AccentPreferences(status='preferred' if initial_offer else 'no_preference',
                                                  accents=[initial_offer] if initial_offer else [])
    repo.save_profile(profile)
    # Execute OpenAIProvider's real classification, mocking only its SDK transport.
    stt = provider_returning(text, .95 if probability is None else probability)
    calls = []
    create = stt.client.audio.transcriptions.create
    async def mocked_transport(**kwargs):
        calls.append(kwargs['file'][0])
        assert kwargs['file'][1] and kwargs['include'] == ['logprobs']
        result = await create(**kwargs)
        if probability is None:
            result.logprobs = None
        return result
    stt.client.audio.transcriptions.create = mocked_transport
    provider.transcribe = stt.transcribe
    return repo, client, settings, calls


def upload(client, lesson, audio_bytes):
    response = client.post(f"/api/lessons/{lesson['id']}/attempts", data={'revision': lesson['revision']},
                           files={'audio': ('response.wav', audio_bytes, 'audio/wav')})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('text, consent', [('Yes, please.', 'accepted'), ('Sure.', 'accepted'),
                                        ('Okay.', 'accepted'), ('No, thanks.', 'declined')])
@pytest.mark.parametrize('phase', ['WELCOME', 'REVIEW'])
def test_real_recording_stt_and_intent_progress_once(repository_factory, tmp_path, audio_bytes, text, consent, phase):
    repo, client, settings, calls = prepared(repository_factory, tmp_path, text)
    lesson = start(client)
    if phase == 'REVIEW':
        stored = repo.lesson(); stored.phase = 'REVIEW'; stored.accent_confirmation = True
        stored.accent_consent.accent = stored.accent_offered
        repo.save_lesson(stored, stored.revision)
        lesson = client.get('/api/lessons/current').json()
    before = repo.profile().model_dump()
    lesson = upload(client, lesson, audio_bytes)
    assert lesson['pending']['uncertainty'] == [SHORT_ASSESSMENT_WARNING]
    assert lesson['pending_recognition'] == 'accepted'
    # The verdict is recomputed on a read/resume, rather than persisted or trusted.
    assert client.get(f"/api/lessons/{lesson['id']}").json()['pending_recognition'] == 'accepted'
    assert client.post(f"/api/lessons/{lesson['id']}/resume").json()['pending_recognition'] == 'accepted'
    result = action(client, lesson, 'answer')
    assert result['phase'] == phase + '_ACK'
    assert result['pending_recognition'] is None and result['pending'] is None
    assert repo.lesson().accent_consent.state == consent
    assert authorized_accent(repo.lesson()) == ('australian' if consent == 'accepted' else None)
    assert repo.profile().model_dump() == before
    after = repo.lesson().model_dump()
    assert client.post(f"/api/lessons/{lesson['id']}/answer", json={'revision': lesson['revision']}).status_code == 409
    assert repo.lesson().model_dump() == after
    assert len(calls) == 1


@pytest.mark.parametrize('text, probability', [('Yes, please.', .64), ('Yes, please.', None), ('', .95), ('Maybe.', .95),
                                             ('Yes, no.', .95), ('Not sure.', .95)])
def test_rejected_stt_preserves_pending_and_state(repository_factory, tmp_path, audio_bytes, text, probability):
    repo, client, _, _ = prepared(repository_factory, tmp_path, text, probability)
    lesson = upload(client, start(client), audio_bytes)
    assert lesson['pending_recognition'] == 'uncertain'
    before, profile = repo.lesson().model_dump(), repo.profile().model_dump()
    response = client.post(f"/api/lessons/{lesson['id']}/answer", json={'revision': lesson['revision']})
    assert response.status_code == 422
    assert repo.lesson().model_dump() == before and repo.profile().model_dump() == profile


@pytest.mark.parametrize('change', ['missing', 'expired', 'old', 'revision'])
def test_server_rechecks_offer_after_verdict_and_rejects_forgery(repository_factory, tmp_path, audio_bytes, change, monkeypatch):
    repo, client, _, _ = prepared(repository_factory, tmp_path, 'Yes, please.')
    lesson = upload(client, start(client), audio_bytes)
    assert lesson['pending_recognition'] == 'accepted'
    path = f"/api/lessons/{lesson['id']}/answer"
    assert client.post(path, json={'revision': lesson['revision'], 'pending_recognition': 'accepted'}).status_code == 422
    if change == 'old':
        at = repo.lesson().started_at + timedelta(seconds=CONSENT_SECONDS)
        monkeypatch.setattr('app.lesson_api.now', lambda: at)
    else:
        stored = repo.lesson()
        if change == 'missing': stored.accent_offered = None
        if change == 'expired': stored.accent_consent.state = 'expired'
        repo.save_lesson(stored, stored.revision)
    stored = repo.lesson()
    before, profile = stored.model_dump(), repo.profile().model_dump()
    revision = lesson['revision'] if change == 'revision' else stored.revision
    assert client.post(path, json={'revision': revision}).status_code == (409 if change == 'revision' else 422)
    assert repo.lesson().model_dump() == before and repo.profile().model_dump() == profile


@pytest.mark.parametrize('initial_offer', [None, 'british'])
@pytest.mark.parametrize('text, consent', [('Yes, please.', 'accepted'), ('Sure.', 'accepted'),
                                        ('Okay.', 'accepted'), ('No, thanks.', 'declined')])
def test_short_reply_answers_the_actual_clarification_not_the_historical_offer(
        repository_factory, tmp_path, audio_bytes, initial_offer, text, consent):
    repo, client, _, _ = prepared(repository_factory, tmp_path, 'Maybe practice Australian English.',
                                  initial_offer=initial_offer)
    lesson = action(client, upload(client, start(client), audio_bytes), 'answer')
    assert lesson['phase'] == 'REVIEW' and 'Would you like Australian English' in lesson['prompt']
    assert repo.lesson().accent_offered == initial_offer
    assert repo.lesson().accent_resolved and repo.lesson().accent_confirmation
    assert repo.lesson().accent_consent.accent == 'australian'
    client.provider.transcribe = provider_returning(text).transcribe
    lesson = upload(client, lesson, audio_bytes)
    assert lesson['pending_recognition'] == 'accepted'
    lesson = action(client, lesson, 'answer')
    assert lesson['phase'] == 'REVIEW_ACK'
    assert repo.lesson().accent_consent.state == consent
    assert authorized_accent(repo.lesson()) == ('australian' if consent == 'accepted' else None)


def test_short_offer_expiring_during_availability_read_cannot_commit(
        repository_factory, tmp_path, audio_bytes, monkeypatch):
    repo, client, _, _ = prepared(repository_factory, tmp_path, 'Yes, please.')
    lesson = upload(client, start(client), audio_bytes)
    at = repo.lesson().started_at + timedelta(seconds=CONSENT_SECONDS - .001)
    monkeypatch.setattr('app.lesson_api.now', lambda: at)
    original = client.app.state.accent_controls.voices
    async def cross_expiry():
        nonlocal at
        available = await original()
        at += timedelta(seconds=.001)
        return available
    monkeypatch.setattr(client.app.state.accent_controls, 'voices', cross_expiry)
    before, profile = repo.lesson().model_dump(), repo.profile().model_dump()
    response = client.post(f"/api/lessons/{lesson['id']}/answer", json={'revision': lesson['revision']})
    assert response.status_code == 422
    assert repo.lesson().model_dump() == before and repo.profile().model_dump() == profile


def test_question_changed_during_availability_read_preserves_the_new_checkpoint(
        repository_factory, tmp_path, audio_bytes, monkeypatch):
    repo, client, _, _ = prepared(repository_factory, tmp_path, 'Yes, please.')
    lesson = upload(client, start(client), audio_bytes)
    original = client.app.state.accent_controls.voices
    checkpoint = None
    async def another_writer():
        nonlocal checkpoint
        available = await original()
        changed = repo.lesson()
        changed.phase = 'REVIEW'; changed.accent_resolved = True
        changed.accent_confirmation = False; changed.pending = None
        repo.save_lesson(changed, changed.revision)
        checkpoint = repo.lesson().model_dump()
        return available
    monkeypatch.setattr(client.app.state.accent_controls, 'voices', another_writer)
    profile = repo.profile().model_dump()
    response = client.post(f"/api/lessons/{lesson['id']}/answer", json={'revision': lesson['revision']})
    assert response.status_code == 409
    assert repo.lesson().model_dump() == checkpoint and repo.profile().model_dump() == profile
    assert repo.lesson().accent_consent.state == 'none'


@pytest.mark.parametrize('probability, accepted', [(.64, False), (.65, True), (.66, True)])
def test_sdk_confidence_boundary_through_the_full_answer_route(
        repository_factory, tmp_path, audio_bytes, probability, accepted):
    repo, client, _, _ = prepared(repository_factory, tmp_path, 'Sure.', probability)
    lesson = upload(client, start(client), audio_bytes)
    assert (lesson['pending_recognition'] == 'accepted') is accepted
    before = repo.lesson().model_dump()
    response = client.post(f"/api/lessons/{lesson['id']}/answer", json={'revision': lesson['revision']})
    assert response.status_code == (200 if accepted else 422)
    if not accepted:
        assert repo.lesson().model_dump() == before


@pytest.mark.parametrize('text', ['Yes, please.', 'Sure.', 'Okay.', 'No, thanks.'])
def test_short_comprehension_stays_uncertain_and_unassessable(lesson_client, audio_bytes, text):
    from test_lesson_lifecycle import exercises
    client = lesson_client
    lesson = exercises(client, audio_bytes)
    exercise = lesson['exercise']
    assert client.post(f"/api/exercises/{exercise['id']}/conversation/listened").status_code == 200
    client.provider.transcribe = provider_returning(text).transcribe
    response = client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('a.wav', audio_bytes, 'audio/wav')})
    assert response.status_code == 200
    attempt = response.json()
    assert attempt['transcription']['uncertainty'] == [SHORT_ASSESSMENT_WARNING]
    before = client.app.state.repository.profile().model_dump()
    rejected = client.post(f"/api/attempts/{attempt['id']}/assess", json={
        'confirmed': True, 'text': attempt['transcription']['text'], 'hands_free': True})
    assert rejected.status_code == 422 and rejected.json()['uncertain'] is True
    assert client.app.state.repository.profile().model_dump() == before
    assert client.app.state.repository.conversation(exercise['id']).turns == []
