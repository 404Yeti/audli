"""Consent is an owned, expiring one-passage grant, never a preference."""
from datetime import timedelta
from uuid import uuid4
import pytest
from app.accent_practice import authorized_accent, resolve, expire, intent
from app.lesson import Lesson, now
from app.models import AccentPreferences, Transcription
from app.config import Settings
from test_accent_practice import VOICE
from test_persistence import repository_factory
from test_lesson_lifecycle import start, respond, action
from conftest import ASGIClient, FakeProvider
from app.main import create_app


def checkpoint():
    return Lesson(id=str(uuid4()), name='Maya', focus='details', started_at=now(), active_since=now(), accent_offered='british')


def test_saved_preference_and_legacy_selection_never_authorize():
    item=checkpoint(); item.passage_accent='british'
    assert authorized_accent(item) is None
    legacy=item.model_dump(); legacy.pop('accent_consent'); legacy.pop('accent_confirmation'); legacy.pop('accent_topic_review_pending')
    assert authorized_accent(Lesson.model_validate(legacy)) is None


@pytest.mark.parametrize('reply', ['Maybe', 'Yes, I visited Britain yesterday', 'I prefer British',
                                  'British accents are difficult', 'Perhaps Irish', 'I want British',
                                  'I want to practice British tomorrow', 'I practice British every day',
                                  'Try British or Irish', 'I want to listen to British music'])
def test_ambiguous_or_preference_statement_does_not_grant_consent(reply):
    item=checkpoint()
    resolve(item, reply, {'british':VOICE})
    assert authorized_accent(item) is None
    assert item.accent_resolved


def test_scope_expiration_consumption_and_pause():
    item=checkpoint(); resolve(item,'Yes',{'british':VOICE})
    assert authorized_accent(item)=='british'
    at=item.accent_consent.accepted_at
    assert authorized_accent(item,at+timedelta(seconds=599))=='british'
    assert authorized_accent(item,at+timedelta(seconds=600)) is None
    assert authorized_accent(item,at-timedelta(seconds=1)) is None
    item.exercises_completed+=1
    assert authorized_accent(item) is None
    item.exercises_completed-=1; item.accent_consent.state='consumed'
    assert authorized_accent(item) is None
    item.accent_consent.state='accepted'; expire(item); item.status='active'
    assert authorized_accent(item) is None
    assert item.accent_consent.state=='expired'


@pytest.mark.parametrize('reply', ['No thanks', 'Cancel', 'I changed my mind', 'Use the default voice',
                                  'Try a Jamaican accent instead'])
def test_pre_passage_conversation_cancels_or_defaults(repository_factory,tmp_path,audio_bytes,reply):
    repo=repository_factory(); profile=repo.profile(); profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['british']);repo.save_profile(profile)
    before=repo.profile().model_dump()
    provider=FakeProvider()
    async def transcribe(*args):return Transcription(text='Yes',confidence=.99)
    provider.transcribe=transcribe
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE}),provider,repo),provider)
    item=start(client); stored=repo.lesson();stored.previous='Last time we practiced listening.';stored.topic_review_due=True  # Legacy response-taking checkpoint.
    repo.save_lesson(stored,stored.revision);item=client.get('/api/lessons/current').json()
    item=respond(client,item,audio_bytes)
    assert authorized_accent(repo.lesson())=='british'
    item=action(client,item,'heard')
    assert item['phase']=='REVIEW' and 'enjoy listening' in item['prompt']
    async def reconsider(*args):return Transcription(text=reply,confidence=.99)
    provider.transcribe=reconsider
    item=respond(client,item,audio_bytes)
    assert authorized_accent(repo.lesson()) is None
    item=action(client,item,'heard');item=action(client,item,'heard');item=action(client,item,'exercise')
    assert item['exercise']['passage_voice']['accent']=='default'
    assert repo.profile().model_dump()==before
    assert repo.lesson().accent_resolved
    other=repository_factory('00000000-0000-0000-0000-000000000099')
    assert other.lesson(item['id']) is None


def test_session_end_invalidates_unconsumed_grant(repository_factory,tmp_path,audio_bytes):
    repo=repository_factory(); profile=repo.profile();profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['british']);repo.save_profile(profile)
    provider=FakeProvider()
    async def transcribe(*args):return Transcription(text='Yes',confidence=.99)
    provider.transcribe=transcribe
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE}),provider,repo),provider)
    item=respond(client,start(client),audio_bytes)
    assert authorized_accent(repo.lesson())=='british'
    assert client.post(f"/api/lessons/{item['id']}/end").status_code==200
    item=action(client,client.get('/api/lessons/current').json(),'resume')
    assert authorized_accent(repo.lesson()) is None
    assert repo.lesson().accent_consent.state=='expired'


def test_unsupported_combined_request_is_not_substituted():
    assert intent('Try British or a Jamaican accent', 'british')==('unavailable',None)
    item=checkpoint(); resolve(item,'Try Irish instead',{'british':VOICE})
    assert authorized_accent(item) is None


def test_cancel_then_explicit_request_can_grant_new_scope():
    item=checkpoint();resolve(item,'No thanks',{'british':VOICE})
    assert authorized_accent(item) is None
    assert 'usual passage voice' in resolve(item,'Sure',{'british':VOICE})
    assert authorized_accent(item) is None
    resolve(item,'Can we practice British?',{'british':VOICE})
    assert authorized_accent(item)=='british'

@pytest.mark.parametrize('state', ['missing', 'expired', 'consumed', 'wrong_scope', 'invalid_time'])
def test_server_defaults_with_saved_interest_but_no_valid_grant(repository_factory,tmp_path,state):
    from app.lesson import AccentConsent
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['british']);repo.save_profile(profile)
    provider=FakeProvider()
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE}),provider,repo),provider)
    item=start(client);stored=repo.lesson();stored.phase='EXERCISE';stored.passage_accent='british'
    if state!='missing':
        stored.accent_consent=AccentConsent(state='accepted',accent='british',accepted_at=now(),exercise_number=0)
        if state=='expired':stored.accent_consent.accepted_at-=timedelta(seconds=600)
        if state=='consumed':stored.accent_consent.state='consumed'
        if state=='wrong_scope':stored.accent_consent.exercise_number=1
        if state=='invalid_time':stored.accent_consent.accepted_at=None
    repo.save_lesson(stored,stored.revision)
    item=client.get('/api/lessons/current').json();item=action(client,item,'exercise')
    assert item['exercise']['passage_voice']['accent']=='default'
    assert repo.profile().accent_preferences.accents==['british']


def test_repository_rejects_accent_audio_without_consent(repository_factory,exercise):
    from app.models import PassageVoice
    repo=repository_factory();repo.profile()
    exercise.passage_voice=PassageVoice(accent='british',provider='elevenlabs',voice_id=VOICE.voice_id,validation=VOICE.validation)
    with pytest.raises(ValueError,match='consent'):
        repo.save_exercise('unauthorized',exercise,repo.profile().difficulty,'unauthorized.mp3',audio=b'fixture')
    assert repo.current_exercise() is None


def test_repository_rechecks_deadline_before_committing(repository_factory,exercise,monkeypatch):
    from app.models import PassageVoice
    repo=repository_factory();profile=repo.profile()
    item=checkpoint();item.phase='EXERCISE';resolve(item,'Yes',{'british':VOICE})
    repo.create_lesson(item)
    exercise.passage_voice=PassageVoice(accent='british',provider='elevenlabs',voice_id=VOICE.voice_id,validation=VOICE.validation)
    accepted=item.accent_consent.accepted_at
    monkeypatch.setattr('app.lesson.now',lambda:accepted+timedelta(seconds=600))
    with pytest.raises(ValueError,match='consent'):
        repo.save_exercise('late',exercise,profile.difficulty,'late.mp3',audio=b'fixture',lesson=item)
    assert repo.current_exercise() is None
    assert repo.lesson().exercise_id is None


def test_legacy_learner_can_explicitly_request_practice_during_discovery(repository_factory,tmp_path,audio_bytes):
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete';repo.save_profile(profile)
    provider=FakeProvider()
    async def transcribe(*args):return Transcription(text='Can we practice British?',confidence=.99)
    provider.transcribe=transcribe
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE}),provider,repo),provider)
    item=start(client);assert repo.lesson().accent_discovery
    respond(client,item,audio_bytes)
    assert authorized_accent(repo.lesson())=='british'
    assert repo.profile().accent_preferences.status=='unspecified'


@pytest.mark.parametrize('confirmation', ['Yes', 'No thanks'])
def test_returning_learner_keeps_consent_through_recap_without_topic_question(repository_factory,tmp_path,audio_bytes,confirmation):
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['british']);repo.save_profile(profile)
    provider=FakeProvider()
    async def transcribe(*args):return Transcription(text=confirmation,confidence=.99)
    provider.transcribe=transcribe
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE}),provider,repo),provider)
    item=start(client);stored=repo.lesson();stored.previous='Last time we listened to a meeting.'
    repo.save_lesson(stored,stored.revision);item=client.get('/api/lessons/current').json()
    item=respond(client,item,audio_bytes);item=action(client,item,'heard')
    accepted_at=repo.lesson().accent_consent.accepted_at
    assert item['phase']=='REVIEW_ACK' and '?' not in item['prompt']
    assert repo.lesson().reflection is None
    assert authorized_accent(repo.lesson())==('british' if confirmation=='Yes' else None)
    assert repo.lesson().accent_consent.accepted_at==accepted_at
    item=action(client,item,'heard');assert item['phase']=='TRANSITION'


@pytest.fixture(autouse=True)
def activated_test_feature(monkeypatch):
    from accent_control_helpers import enable_test_controls
    enable_test_controls(monkeypatch)
