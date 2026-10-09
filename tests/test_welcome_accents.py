"""Preferences and onboarding checkpoints, never listening assessment evidence."""
import pytest
from uuid import uuid4
from pydantic import ValidationError
from app.accents import extract, lesson_preference
from app.models import AccentPreferences, LearnerProfile, Transcription
from app.lesson import Lesson, now, prompt
from test_auth import accounts, USER_A, USER_B
from test_persistence import repository_factory
from test_onboarding import configure, start, spoken
from test_lesson_lifecycle import lesson_client, action, respond

@pytest.mark.parametrize('text,accents,status', [
    ('Scottish', ['scottish'], 'preferred'),
    ('Scottish and Australian', ['australian','scottish'], 'preferred'),
    ('fast Indian English', ['indian'], 'preferred'),
    ('Singaporean English at work, especially fast speech', [], 'preferred'),
    ('No preference', [], 'no_preference'), ('No', [], 'no_preference'),
    ("I’m not sure", [], 'no_preference'), ('skip', [], 'no_preference'),
])
def test_accent_contract_preserves_response(text, accents, status):
    preference = extract(text)
    assert preference.response == text and preference.accents == accents
    assert preference.status == status and preference.source == 'learner_stated'


def test_strict_accent_schema_and_incomplete_answer():
    assert extract('yes') is None
    with pytest.raises(ValidationError): AccentPreferences(accents=['invented'])
    with pytest.raises(ValidationError): AccentPreferences(response='x'*8001)
    with pytest.raises(ValidationError): AccentPreferences(measured_weakness=True)
    assert lesson_preference('Fine thanks') is None
    assert lesson_preference('No thanks', 'scottish').status == 'no_preference'


def test_welcome_checkpoint_restarts_and_cannot_listen_before_finished(accounts, repository_factory, audio_bytes):
    a, b, _ = accounts
    initial = a.get('/api/onboarding').json()
    state = a.post('/api/onboarding/start', json={'revision':initial['revision']}).json()
    assert state['welcome'] == 'introduction' and 'listening buddy' in state['prompt']
    assert a.post('/api/onboarding/attempts', data={'revision':state['revision']},
        files={'audio':('x.wav',audio_bytes,'audio/wav')}).status_code == 409
    assert repository_factory(USER_A).profile().onboarding.welcome == 'introduction'
    prompts=[]
    async def speak(text, speech_rate=.9): prompts.append(text); return audio_bytes
    a.provider.speak=speak
    assert a.post('/api/onboarding/audio',json={'revision':state['revision']}).status_code==200
    assert a.get('/api/onboarding').json()==state  # TTS readiness is not completion.
    status=a.post('/api/onboarding/heard',json={'revision':state['revision']}).json()
    assert status['welcome']=='status'
    for label in ['Audli is speaking','Listening to you','Thinking']: assert label in status['prompt']
    assert 'don’t need to watch' in status['prompt']
    assert a.post('/api/onboarding/heard',json={'revision':state['revision']}).status_code==409
    identity=a.post('/api/onboarding/heard',json={'revision':status['revision']}).json()
    assert identity['welcome'] is None and identity['stage']=='identity'
    assert a.post('/api/onboarding/start',json={'revision':0}).json()==identity
    assert b.get('/api/onboarding').json()['profile']['onboarding_status']=='not_started'
    assert repository_factory(USER_A).profile().onboarding.welcome is None


@pytest.mark.parametrize('legacy_status,stage', [('complete','identity'),('in_progress','needs'),('profile_saved','review')])
def test_existing_learners_never_replay_welcome(accounts, repository_factory, legacy_status, stage):
    a,_,_=accounts
    repo=repository_factory(USER_A); profile=repo.profile()
    profile.onboarding_status=legacy_status; profile.onboarding.stage=stage; repo.save_profile(profile)
    state=a.post('/api/onboarding/start',json={'revision':0}).json()
    assert state['welcome'] is None and state['stage']==stage
    assert state['profile']['accent_preferences']['status']=='unspecified'


def test_accent_partial_clarification_uncertainty_and_account_persistence(accounts, repository_factory, audio_bytes):
    a,b,_=accounts; configure(a); state=start(a)
    for _ in range(3): state=spoken(a,state,audio_bytes)
    assert state['stage']=='accents'
    before=a.get('/api/profile').json()['profile']
    for _ in range(3): state=spoken(a,state,audio_bytes,'yes')
    assert state['clarification_paused'] and state['stage']=='accents'
    assert repository_factory(USER_A).profile().onboarding.clarification_paused
    state=a.post('/api/onboarding/retry-clarification',json={'revision':state['revision']}).json()
    async def uncertain(*args): return Transcription(text='Scottish',confidence=.2,uncertainty=['unclear'])
    a.provider.transcribe=uncertain
    state=a.post('/api/onboarding/attempts',data={'revision':state['revision']},files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    assert a.post('/api/onboarding/answer',json={'revision':state['revision'],'text':'Scottish','confirmed':True,'hands_free':True}).status_code==422
    assert repository_factory(USER_A).profile().accent_preferences.status=='unspecified'
    state=a.post('/api/onboarding/answer',json={'revision':state['revision'],'text':'Scottish and fast Australian English','confirmed':True}).json()
    assert state['stage']=='review'
    saved=repository_factory(USER_A).profile()
    assert saved.accent_preferences.response=='Scottish and fast Australian English'
    assert saved.accent_preferences.accents==['australian','scottish']
    assert repository_factory(USER_B).profile().accent_preferences.status=='unspecified'
    assert b.get('/api/profile').json()['profile']['accent_preferences']['status']=='unspecified'
    for key in ['difficulty','listening_profile','focus','completed_attempts']:
        assert state['profile'][key]==before[key]


def test_occasionally_rotate_suggestions_with_one_existing_review_question(repository_factory):
    repo=repository_factory(); profile=repo.profile()
    profile.accent_preferences=extract('Scottish and Australian'); repo.save_profile(profile)
    suggestions=[]
    for i in range(6):
        at=now(); lesson=Lesson(id=str(uuid4()),started_at=at,active_since=at,name='Maya',focus='details',previous='Last time, we practiced details.' if i else None)
        repo.create_lesson(lesson); suggestions.append(lesson.suggested_accent)
        lesson.phase='REVIEW'
        assert prompt(lesson).count('?')==1
        if lesson.suggested_accent: assert 'aren’t available yet' in prompt(lesson)
        lesson.status='completed'; lesson.phase='COMPLETED'; repo.save_lesson(lesson,lesson.revision)
    assert suggestions==[None,'australian',None,'scottish',None,'australian']


@pytest.mark.parametrize('phase,text,status,accents',[
    ('WELCOME','Could we try an Australian accent today?', 'preferred',['australian']),
    ('REVIEW','Scottish and fast Indian English please', 'preferred',['scottish','indian']),
    ('REVIEW','A Singaporean accent please', 'preferred',[]),
    ('REVIEW','No thanks', 'no_preference',[]),
    ('WELCOME','Please stop suggesting accents', 'no_preference',[]),
])
def test_lesson_accent_requests_are_honest_and_persisted(lesson_client,audio_bytes,phase,text,status,accents):
    client=lesson_client; repo=client.app.state.repository
    lesson=client.post('/api/lessons/start').json(); stored=repo.lesson(lesson['id'])
    stored.phase=phase; stored.suggested_accent='scottish'; repo.save_lesson(stored,stored.revision)
    lesson=client.get(f"/api/lessons/{lesson['id']}").json()
    async def transcribe(*args): return Transcription(text=text,confidence=.95)
    client.provider.transcribe=transcribe
    before=repo.profile()
    updated=respond(client,lesson,audio_bytes)
    assert updated['phase']==phase+'_ACK'
    if status=='preferred': assert 'aren’t available yet' in updated['prompt'] and 'usual voice' in updated['prompt']
    else: assert 'won’t suggest' in updated['prompt']
    profile=repo.profile(); assert profile.accent_preferences.response==text
    assert profile.accent_preferences.status==status and profile.accent_preferences.accents==accents
    assert profile.difficulty==before.difficulty and profile.listening_profile==before.listening_profile
    assert profile.completed_attempts==before.completed_attempts and repo.history()==[]
    restored=client.get(f"/api/lessons/{lesson['id']}").json()
    for key in ['phase','revision','prompt','pending']: assert restored[key]==updated[key]

@pytest.mark.parametrize('text', ['Indian food', 'Australian wildlife', 'American history', 'Scottish movies', 'English documentaries about Indian food'])
def test_nationality_topics_are_not_accent_preferences(text):
    assert lesson_preference(text) is None


def test_tentative_accent_response_keeps_named_varieties():
    preference = extract('Not sure, maybe Scottish and Australian')
    assert preference.accents == ['australian', 'scottish'] and preference.status == 'preferred'
    assert preference.response == 'Not sure, maybe Scottish and Australian'


@pytest.mark.parametrize('text', ['A Singaporean accent please', 'Scottish and fast Indian English please', 'No thanks'])
def test_accent_only_review_never_clears_topics_or_depends_on_extraction(lesson_client, audio_bytes, text):
    client=lesson_client; repo=client.app.state.repository
    profile=repo.profile(); profile.interests=['science']; repo.save_profile(profile)
    lesson=client.post('/api/lessons/start').json(); stored=repo.lesson(lesson['id'])
    stored.phase='REVIEW'; stored.suggested_accent='scottish'; repo.save_lesson(stored, stored.revision)
    lesson=client.get(f"/api/lessons/{lesson['id']}").json()
    async def transcribe(*args): return Transcription(text=text, confidence=.95)
    async def fail(*args): raise AssertionError('Accent-only speech must not require a topic extractor')
    client.provider.transcribe=transcribe; client.provider.extract_profile=fail
    updated=respond(client, lesson, audio_bytes)
    assert updated['phase']=='REVIEW_ACK' and repo.profile().interests==['science']
    assert repo.profile().accent_preferences.response==text
    next_phase=action(client,updated,'heard')
    assert next_phase['phase']=='TRANSITION'


@pytest.mark.parametrize('failure', [False, True])
def test_mixed_topic_accent_review_preserves_availability_and_topics(lesson_client,audio_bytes,failure):
    from app.onboarding import ProfileExtraction
    client=lesson_client; repo=client.app.state.repository
    profile=repo.profile(); profile.interests=['gardening']; repo.save_profile(profile)
    lesson=client.post('/api/lessons/start').json(); stored=repo.lesson(lesson['id'])
    stored.phase='REVIEW'; repo.save_lesson(stored, stored.revision)
    lesson=client.get(f"/api/lessons/{lesson['id']}").json()
    async def transcribe(*args): return Transcription(text='Science, with a Scottish accent please',confidence=.95)
    async def topics(*args):
        if failure: raise ValueError('Synthetic extractor outage')
        return ProfileExtraction(name=None,target_language=None,goal=None,target_situations=None,interests=['science'])
    client.provider.transcribe=transcribe; client.provider.extract_profile=topics
    updated=respond(client,lesson,audio_bytes)
    assert updated['phase']=='REVIEW_ACK' and 'usual voice' in updated['prompt']
    assert repo.profile().accent_preferences.accents==['scottish']
    assert repo.profile().interests==(['gardening'] if failure else ['science'])


@pytest.mark.parametrize('text', ['Could we try a Scottish recording?', 'I would like to hear Australian', 'fast Indian English'])
def test_spontaneous_speech_requests_are_recognized(text):
    assert lesson_preference(text).status=='preferred'


def test_authenticated_lesson_preferences_do_not_cross_accounts(accounts,repository_factory,audio_bytes):
    a,b,_=accounts
    for owner in (USER_A,USER_B):
        repo=repository_factory(owner); profile=repo.profile()
        profile.onboarding_status='complete'; repo.save_profile(profile)
    lesson=a.post('/api/lessons/start').json()
    async def transcribe(*args): return Transcription(text='Scottish accent please',confidence=.95)
    a.provider.transcribe=transcribe
    pending=a.post(f"/api/lessons/{lesson['id']}/attempts",data={'revision':lesson['revision']},files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    assert b.post(f"/api/lessons/{lesson['id']}/answer",json={'revision':pending['revision']}).status_code==404
    assert a.post(f"/api/lessons/{lesson['id']}/answer",json={'revision':pending['revision']}).status_code==200
    assert repository_factory(USER_A).profile().accent_preferences.accents==['scottish']
    assert repository_factory(USER_B).profile().accent_preferences.status=='unspecified'


def test_pre_feature_json_snapshots_load_without_backfill(repository_factory):
    from sqlalchemy import select,update
    from app.storage import schema as tables
    repo=repository_factory(); profile=repo.profile(); profile.onboarding_status='complete'; repo.save_profile(profile)
    with repo.transaction() as db:
        data=db.execute(select(tables.profiles.c.data).where(tables.profiles.c.user_id==repo.learner_id)).scalar_one()
        data.pop('accent_preferences'); data['onboarding'].pop('welcome'); data['onboarding'].pop('accents_asked')
        db.execute(update(tables.profiles).where(tables.profiles.c.user_id==repo.learner_id).values(data=data))
    old=repository_factory(repo.learner_id).profile()
    assert old.onboarding_status=='complete' and old.onboarding.welcome is None
    assert old.accent_preferences.status=='unspecified'
