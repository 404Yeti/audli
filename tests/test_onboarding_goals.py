"""Goal rescue uses synthetic extraction; no evaluator accuracy claims."""
import pytest
from app.models import LearnerProfile
from app.onboarding import ProfileExtraction, apply_extraction, onboarding_prompt
from test_onboarding import start, spoken, configure
from test_auth import accounts
from test_persistence import repository_factory

@pytest.mark.parametrize('goal,situations', [
    ('Understand work calls', ['work calls']), ('Improve writing', None),
    ('Practice speaking', None), ('Win a marathon', None), (None, None),
    ('Learn grammar', []),
])
def test_voice_goals_never_require_a_perfect_listening_answer(goal, situations):
    profile = LearnerProfile()
    profile.onboarding.stage = 'needs'
    profile.onboarding_status = 'in_progress'
    result = apply_extraction(profile, ProfileExtraction(name=None, target_language=None,
        goal=goal, target_situations=situations, interests=None))
    assert result.onboarding.stage == 'interests'
    assert result.goal == (goal or 'Understand everyday English')
    assert result.target_situations == (situations or ['everyday conversations'])
    assert not result.onboarding.clarification_paused
    assert result.completed_attempts == profile.completed_attempts
    if not situations:
        assert 'I focus on listening' in onboarding_prompt(result)

@pytest.mark.parametrize('text,skip', [('仕事の会話を理解したい — café 👂',False),('',True)])
def test_typed_or_skipped_goals_persist_and_enter_lesson(accounts, audio_bytes, text, skip):
    a,b,_ = accounts
    configure(a)
    state = spoken(a, start(a), audio_bytes)
    token = state['goal_question_revision']
    response = a.post('/api/onboarding/goal', json={'question_revision':token,'text':text,'skip':skip})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['stage'] == 'interests' and saved['pending'] is None
    assert saved['profile']['goal'] == (text or 'Understand everyday English')
    assert a.get('/api/onboarding').json() == saved
    assert b.post('/api/onboarding/goal',json={'question_revision':token,'text':'steal'}).status_code == 409
    assert a.post('/api/onboarding/goal',json={'question_revision':token,'text':text,'skip':skip}).json() == saved
    state = spoken(a,saved,audio_bytes)
    assert a.post('/api/onboarding/goal',json={'question_revision':token,'text':'late'}).status_code == 409
    state = spoken(a,state,audio_bytes,'skip')
    assert state['stage'] == 'review'
    state = a.post('/api/onboarding/complete',json={'revision':state['revision']}).json()
    assert state['destination'] == 'session_ready'
    assert a.post('/api/lessons/start',json={}).status_code == 200

@pytest.mark.parametrize('voice_first',[False,True])
def test_explicit_goal_wins_race_with_voice(accounts,audio_bytes,voice_first):
    a,_,_ = accounts
    configure(a)
    state = spoken(a,start(a),audio_bytes)
    token = state['goal_question_revision']
    if voice_first:
        state = spoken(a,state,audio_bytes)
    saved = a.post('/api/onboarding/goal',json={'question_revision':token,'text':'旅行先の英語'}).json()
    assert saved['profile']['goal'] == '旅行先の英語'
    assert saved['stage'] == 'interests'
    assert a.post('/api/onboarding/answer',json={'revision':state['revision'],'text':'voice','confirmed':True}).status_code == 409


def test_failed_transcription_and_paused_legacy_goal_can_skip(accounts,audio_bytes):
    a,_,_=accounts
    configure(a)
    state=spoken(a,start(a),audio_bytes)
    async def fail(*args):
        raise RuntimeError('transcription unavailable')
    a.provider.transcribe=fail
    assert a.post('/api/onboarding/attempts', data={'revision':state['revision']},files={'audio':('x.wav',audio_bytes,'audio/wav')}).status_code == 503
    saved=a.post('/api/onboarding/goal',json={'question_revision':state['goal_question_revision'],'skip':True}).json()
    assert saved['stage']=='interests'

@pytest.mark.parametrize('payload', [{'text':' '},{'text':'x'*501},{'text':'bad\u0000goal'},{'text':'ok','extra':True}])
def test_invalid_goal_input_does_not_change_checkpoint(accounts,audio_bytes,payload):
    a,_,_=accounts
    configure(a)
    state=spoken(a,start(a),audio_bytes)
    assert a.post('/api/onboarding/goal',json={'question_revision':state['goal_question_revision'],**payload}).status_code == 422
    assert a.get('/api/onboarding').json() == state


def test_legacy_paused_goal_recovery_preserves_existing_preferences(accounts,repository_factory):
    from test_auth import USER_A
    a,_,_=accounts
    repo=repository_factory(USER_A)
    profile=repo.profile()
    profile.goal='Understand family calls'
    profile.target_situations=['family calls']
    profile.onboarding_status='in_progress'
    profile.onboarding.stage='needs'
    profile.onboarding.clarification='target_situations'
    profile.onboarding.clarification_count=2
    profile.onboarding.clarification_paused=True
    repo.save_onboarding(profile,profile.onboarding.revision)
    state=a.get('/api/onboarding').json()
    assert state['goal_entry_available']
    saved=a.post('/api/onboarding/goal',json={'question_revision':state['goal_question_revision'],'skip':True}).json()
    assert saved['stage']=='interests' and not saved['clarification_paused']
    assert saved['profile']['goal']=='Understand family calls'
    assert saved['profile']['target_situations']==['family calls']
