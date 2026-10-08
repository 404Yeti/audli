from datetime import timedelta
from pathlib import Path
import asyncio
import pytest
from sqlalchemy import select, update
from app.lesson import Lesson, closing_due, elapsed, now, MIN_EXERCISE_SECONDS, CLOSING_SECONDS, TARGET_SECONDS
from app.main import create_app
from app.config import Settings
from app.models import Transcription
from app.storage import schema as s
from app.storage.migrations import postgres_lesson_migration
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory
from test_auth import accounts, USER_A, USER_B
from test_conversation import scripted, answer as exercise_answer

@pytest.fixture
def lesson_client(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory()
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()
    async def speak(text, speech_rate=.9): return audio_bytes
    provider.speak = speak
    client = ASGIClient(create_app(Settings(data_dir=tmp_path, _env_file=None), provider, repo), provider)
    yield client

def start(client):
    response = client.post('/api/lessons/start')
    assert response.status_code == 200, response.text
    return response.json()

def action(client, lesson, verb):
    response = client.post(f"/api/lessons/{lesson['id']}/{verb}", json={'revision': lesson['revision']})
    assert response.status_code == 200, response.text
    return response.json()

def respond(client, lesson, audio_bytes):
    response = client.post(f"/api/lessons/{lesson['id']}/attempts", data={'revision':lesson['revision']},
        files={'audio':('response.wav',audio_bytes,'audio/wav')})
    assert response.status_code == 200, response.text
    return action(client, response.json(), 'answer')

def exercises(client, audio_bytes):
    lesson = start(client)
    lesson = respond(client, lesson, audio_bytes)
    lesson = action(client, lesson, 'heard')
    if lesson['phase'] == 'REVIEW':
        lesson = respond(client, lesson, audio_bytes)
        lesson = action(client, lesson, 'heard')
    assert lesson['phase'] == 'TRANSITION'
    lesson = action(client, lesson, 'heard')
    return action(client, lesson, 'exercise')

def age(client, lesson, seconds):
    repo = client.app.state.repository
    stored = repo.lesson(lesson['id']); stored.active_since = now() - timedelta(seconds=seconds)
    repo.save_lesson(stored, stored.revision)
    return client.get(f"/api/lessons/{lesson['id']}").json()

def complete_exercise(client, lesson, audio_bytes, statuses=None):
    scripted(client, [statuses or {}])
    exercise = lesson['exercise']
    assert client.post(f"/api/exercises/{exercise['id']}/conversation/listened").status_code == 200
    _, _, result = exercise_answer(client, exercise, audio_bytes)
    for _ in range(2):
        if result.json()['state'] != 'AWAITING_FOLLOWUP': break
        _, _, result = exercise_answer(client, exercise, audio_bytes, result.json()['active_followup'])
    assert result.json()['state'] == 'GIVING_FEEDBACK'
    assert client.post(f"/api/exercises/{exercise['id']}/conversation/ready").status_code == 200
    return result.json()

def test_first_lesson_named_greeting_one_question_skips_invented_history(lesson_client, audio_bytes):
    client = lesson_client; lesson = start(client)
    assert lesson['phase'] == 'WELCOME' and 'Maya' in lesson['prompt'] and lesson['prompt'].count('?') == 1
    assert 'Last time' not in lesson['prompt'] and lesson['exercise'] is None
    before = client.get('/api/profile').json()
    assert client.post('/api/exercises').status_code == 409
    lesson = respond(client, lesson, audio_bytes)
    assert lesson['phase'] == 'WELCOME_ACK' and '?' not in lesson['prompt']
    assert client.get('/api/profile').json() == before  # check-in is never listening evidence.
    lesson = action(client, lesson, 'heard')
    assert lesson['phase'] == 'TRANSITION'
    lesson = action(client, lesson, 'heard')
    assert lesson['phase'] == 'EXERCISE'

@pytest.mark.parametrize('seconds,due', [(419,False),(420,False),(421,True),(510,True),(600,True),(720,True)])
def test_authoritative_budget_boundaries(seconds, due):
    at = now(); lesson = Lesson(id='lesson',started_at=at,active_since=at-timedelta(seconds=seconds),name='Maya',focus='details')
    assert closing_due(lesson, at) is due
    assert TARGET_SECONDS == 600 and MIN_EXERCISE_SECONDS + CLOSING_SECONDS == 180

def test_completed_lesson_grounded_closing_returning_review_and_preferences(lesson_client, audio_bytes):
    client = lesson_client; lesson = exercises(client, audio_bytes)
    assert client.get(f"/api/exercises/{lesson['exercise']['id']}/transcript").status_code == 403
    result = complete_exercise(client, lesson, audio_bytes)
    assert 'database migration' in result['prompt'].lower() and 'stay at this level' not in result['prompt']
    lesson = age(client, lesson, 520); lesson = action(client, lesson, 'advance')
    assert lesson['phase'] == 'CLOSING' and lesson['exercises_completed'] == 1 and lesson['strength']
    assert 'technology' in lesson['prompt'].lower() and 'database migration' in lesson['prompt'].lower()
    assert client.post('/api/exercises').status_code == 409
    assert client.post(f"/api/lessons/{lesson['id']}/exercise",json={'revision':lesson['revision']}).status_code == 409
    lesson = action(client, lesson, 'heard')
    assert lesson['phase'] == 'COMPLETED' and lesson['status'] == 'completed'
    assert client.get('/api/lessons/current').json() is None
    returning = start(client); returning = respond(client, returning, audio_bytes); returning = action(client, returning, 'heard')
    assert returning['phase'] == 'REVIEW' and 'Last time' in returning['prompt'] and 'demonstrated strength' in returning['prompt']
    assert returning['prompt'].count('?') == 1
    async def transcribe(audio, filename): return Transcription(text='I enjoy science and travel',confidence=.99)
    client.provider.transcribe = transcribe
    before = client.get('/api/profile').json()['profile']
    returning = respond(client, returning, audio_bytes)
    assert returning['phase'] == 'REVIEW_ACK'
    after = client.get('/api/profile').json()['profile']
    assert after['interests'] == ['I enjoy science and travel']
    assert after['difficulty'] == before['difficulty'] and after['completed_attempts'] == before['completed_attempts']

def test_expiry_does_not_interrupt_active_exercise_and_unknown_is_not_strength(lesson_client, audio_bytes):
    client = lesson_client; lesson = exercises(client, audio_bytes); lesson = age(client, lesson, 700)
    assert client.get('/api/lessons/current').json()['phase'] == 'EXERCISE'
    assert client.post(f"/api/lessons/{lesson['id']}/advance",json={'revision':lesson['revision']}).status_code == 409
    difficulty = client.get('/api/profile').json()['profile']['difficulty']
    result = complete_exercise(client, lesson, audio_bytes, {dim:'insufficient_evidence' for dim in ('main_idea','details','vocabulary','inference')})
    assert result['followups_asked'] == 2
    assert result['result']['adaptation']['new_difficulty'] == difficulty
    lesson = action(client, lesson, 'advance')
    assert lesson['phase'] == 'CLOSING' and lesson['strength'] is None
    assert 'keep gathering' in lesson['prompt'] and 'misunderstanding' not in lesson['prompt']

def test_end_pause_resume_revision_and_persisted_pending_response(lesson_client, audio_bytes):
    client = lesson_client; lesson = start(client)
    path = f"/api/lessons/{lesson['id']}"
    response = client.post(path+'/attempts',data={'revision':lesson['revision']},files={'audio':('reply.wav',audio_bytes,'audio/wav')})
    pending = response.json()
    assert client.get(path).json()['pending'] == pending['pending']
    paused = client.post(path+'/end').json()
    assert paused['status'] == 'paused' and paused['pending']
    assert client.post(path+'/answer',json={'revision':pending['revision']}).status_code == 409
    stored = client.app.state.repository.lesson(lesson['id'])
    assert elapsed(stored, now()+timedelta(days=1)) == stored.elapsed_seconds
    resumed = client.post(path+'/resume').json()
    assert resumed['status'] == 'active' and resumed['pending'] == pending['pending']
    ack = action(client, resumed, 'answer')
    assert ack['phase'] == 'WELCOME_ACK'
    assert client.post(path+'/answer',json={'revision':resumed['revision']}).status_code == 409

def test_uncertain_personal_answer_does_not_change_state_or_preferences(lesson_client, audio_bytes):
    client = lesson_client; lesson = start(client); before = client.get('/api/profile').json()
    client.provider.uncertain = True
    response = client.post(f"/api/lessons/{lesson['id']}/attempts",data={'revision':lesson['revision']},files={'audio':('reply.wav',audio_bytes,'audio/wav')}).json()
    assert client.post(f"/api/lessons/{lesson['id']}/answer",json={'revision':response['revision']}).status_code == 422
    assert client.get('/api/profile').json() == before
    assert client.get('/api/lessons/current').json()['phase'] == 'WELCOME'

def test_cached_acknowledgment_neutral_owned_and_does_not_change_progress(lesson_client):
    client = lesson_client; texts=[]; original=client.provider.speak
    async def speak(text,speech_rate=.9): texts.append(text); return await original(text,speech_rate)
    client.provider.speak=speak; before=client.get('/api/profile').json()
    first=client.post('/api/recognition/acknowledgment-audio'); second=client.post('/api/recognition/acknowledgment-audio')
    assert first.status_code == second.status_code == 200 and first.content == second.content
    assert texts == ['Okay, I heard you.']
    assert all(word not in texts[0].lower() for word in ('correct','great','understood','well done'))
    assert client.get('/api/profile').json() == before

def test_lesson_and_audio_account_ownership(accounts):
    a,b,_=accounts
    for client in (a,b):
        repo=client.app.state.repository.for_learner(USER_A if client is a else USER_B)
        profile=repo.profile();profile.onboarding_status='complete';repo.save_profile(profile)
    lesson=start(a); path=f"/api/lessons/{lesson['id']}"
    for verb in ('get','end','resume','audio','answer','heard','exercise','advance','introduction-audio','introduction-heard'):
        response=b.get(path) if verb=='get' else b.post(path+'/'+verb,json={'revision':lesson['revision']})
        assert response.status_code == 404
    assert b.get('/api/lessons/current').json() is None

def test_migration_002_is_generated_and_keeps_new_tables_private():
    sql=postgres_lesson_migration()
    assert Path('docs/postgres-002.sql').read_text() == sql
    for table in (s.lessons,s.lesson_exercises):
        assert f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY' in sql
        assert f'REVOKE ALL ON TABLE {table.name} FROM PUBLIC' in sql

def test_delayed_generation_after_end_cannot_attach_or_launch(lesson_client, audio_bytes):
    client=lesson_client; lesson=start(client);lesson=respond(client,lesson,audio_bytes);lesson=action(client,lesson,'heard');lesson=action(client,lesson,'heard')
    original=client.provider.generate
    async def delayed(profile):
        stored=client.app.state.repository.lesson(lesson['id']);stored.status='paused';stored.active_since=None
        client.app.state.repository.save_lesson(stored,stored.revision)
        return await original(profile)
    client.provider.generate=delayed
    response=client.post(f"/api/lessons/{lesson['id']}/exercise",json={'revision':lesson['revision']})
    assert response.status_code == 409
    assert client.get('/api/lessons/current').json()['status'] == 'paused'
    assert client.get('/api/exercises/current').json() is None

@pytest.mark.parametrize('failure', [ValueError('short draft'), RuntimeError('provider secret')])
@pytest.mark.parametrize('recover_seconds', [150, 550])
def test_generation_failure_retry_preserves_completed_work_and_time(lesson_client, audio_bytes, failure, recover_seconds):
    client = lesson_client
    lesson = exercises(client, audio_bytes)
    previous_id = lesson['exercise']['id']
    complete_exercise(client, lesson, audio_bytes)
    lesson = action(client, lesson, 'advance')
    lesson = age(client, lesson, 120)
    repo = client.app.state.repository
    history, profile = repo.history(), repo.profile()
    before = repo.lesson(lesson['id'])
    original = client.provider.generate
    calls = []
    async def failing(profile):
        calls.append(profile)
        raise failure
    client.provider.generate = failing
    response = client.post(f"/api/lessons/{lesson['id']}/exercise", json={'revision': lesson['revision']})
    assert response.status_code == 503
    assert 'Retry conversation' in response.json()['detail']
    assert 'server configuration' not in response.text and 'provider secret' not in response.text
    assert repo.lesson(lesson['id']) == before
    assert repo.history() == history and repo.profile() == profile
    assert repo.current_exercise()['id'] == previous_id
    assert len(repo.lesson_work(lesson['id'])) == 1
    client.provider.generate = original
    lesson = age(client, lesson, recover_seconds)
    recovered = action(client, lesson, 'exercise')
    assert recovered['exercises_completed'] == 1
    assert recovered['elapsed_seconds'] >= recover_seconds
    assert repo.history() == history and repo.profile() == profile
    assert len(repo.lesson_work(lesson['id'])) == 1
    if recover_seconds == 550:
        assert recovered['phase'] == 'CLOSING' and recovered['exercise'] is None
        assert repo.current_exercise()['id'] == previous_id
    else:
        assert recovered['exercise']['id'] != previous_id
        again = action(client, recovered, 'exercise')
        assert again['exercise']['id'] == recovered['exercise']['id']
        assert again['revision'] == recovered['revision']
    assert len(calls) == 1

def test_provider_failure_crossing_closing_budget_closes_without_new_exercise(lesson_client, audio_bytes, monkeypatch):
    import app.lesson_api as api
    client = lesson_client
    lesson = exercises(client, audio_bytes)
    complete_exercise(client, lesson, audio_bytes)
    lesson = action(client, lesson, 'advance')
    current_time = now()
    async def failing(profile):
        monkeypatch.setattr(api, 'now', lambda: current_time + timedelta(seconds=550))
        raise RuntimeError('provider unavailable')
    client.provider.generate = failing
    result = action(client, lesson, 'exercise')
    assert result['phase'] == 'CLOSING' and result['exercises_completed'] == 1
    assert result['exercise'] is None

def test_provider_latency_crossing_budget_cannot_commit_new_exercise(lesson_client, audio_bytes, monkeypatch):
    import app.lesson_api as api
    import app.storage.repository as storage
    client=lesson_client; lesson=start(client);lesson=respond(client,lesson,audio_bytes);lesson=action(client,lesson,'heard');lesson=action(client,lesson,'heard')
    offset=[0]; original_now=now
    clock=lambda: original_now()+timedelta(seconds=offset[0])
    monkeypatch.setattr(api,'now',clock);monkeypatch.setattr(storage,'utcnow',clock)
    original=client.provider.generate
    async def delayed(profile): offset[0]=550;return await original(profile)
    client.provider.generate=delayed
    result=action(client,lesson,'exercise')
    assert result['phase']=='CLOSING' and result['exercises_completed']==0
    assert client.get('/api/exercises/current').json() is None
    assert not list((client.app.state.repository.path.parent/'exercise_audio').glob('*')) if hasattr(client.app.state.repository,'path') else True

def test_delayed_reflection_after_end_cannot_write_preferences(lesson_client,audio_bytes):
    client=lesson_client; lesson=start(client); stored=client.app.state.repository.lesson(lesson['id'])
    stored.phase='REVIEW';stored.previous='Last time, we practiced technology.'
    stored.pending=Transcription(text='science',confidence=.99)
    client.app.state.repository.save_lesson(stored,stored.revision)
    before=client.get('/api/profile').json()
    original=client.provider.extract_profile
    async def delayed(stage,text):
        latest=client.app.state.repository.lesson(stored.id);latest.status='paused';latest.active_since=None
        client.app.state.repository.save_lesson(latest,latest.revision)
        return await original(stage,text)
    client.provider.extract_profile=delayed
    assert client.post(f'/api/lessons/{stored.id}/answer',json={'revision':stored.revision}).status_code==409
    assert client.get('/api/profile').json()==before

def test_sqlite_upgrade_001_to_002_preserves_existing_history(tmp_path,exercise):
    from app.repository import ProgressRepository
    from test_persistence import prepare_completion
    path=tmp_path/'migration.sqlite3';repo=ProgressRepository(path)
    prepare_completion(repo,exercise)
    before=repo.history();profile=repo.profile()
    with repo.engine.begin() as db:
        s.lesson_exercises.drop(db);s.lessons.drop(db)
        db.execute(s.versions.delete().where(s.versions.c.version==2))
    repo.close(); upgraded=ProgressRepository(path)
    assert upgraded.history()==before and upgraded.profile()==profile
    with upgraded.engine.connect() as db:
        assert sorted(db.execute(select(s.versions.c.version)).scalars())==[1,2]
    upgraded.close()

def test_postgres_upgrade_001_to_002_is_explicit_and_preserves_history(repository_factory,exercise):
    from scripts.migrate_postgres import migrate
    from app.repository import PostgresRepository
    from test_persistence import prepare_completion
    repo=repository_factory()
    if repo.engine.dialect.name!='postgresql': return
    prepare_completion(repo,exercise);before=repo.history();profile=repo.profile()
    url=repo.engine.url.render_as_string(hide_password=False);owner=repo.learner_id
    with repo.engine.begin() as db:
        s.lesson_exercises.drop(db);s.lessons.drop(db)
        db.execute(s.versions.delete().where(s.versions.c.version==2))
    with pytest.raises(RuntimeError,match='Postgres persistence could not start'):
        PostgresRepository(url,owner)
    settings=Settings(persistence='postgres',database_url=url,learner_id=owner,_env_file=None)
    assert migrate(settings) is True and migrate(settings) is False
    upgraded=PostgresRepository(url,owner)
    assert upgraded.history()==before and upgraded.profile()==profile
    upgraded.close()

@pytest.mark.parametrize('text', ["I'm good, and you?", "Fine. How about you?", "I'm tired. How are you?"])
def test_bounded_social_reciprocity_uses_recognition_without_evaluation(lesson_client, audio_bytes, text):
    client = lesson_client
    async def recognize(audio, filename): return Transcription(text=text, confidence=.99)
    client.provider.transcribe = recognize
    before = client.app.state.repository.profile()
    result = respond(client, start(client), audio_bytes)
    assert result['phase'] == 'WELCOME_ACK'
    assert 'ready to listen with you' in result['prompt']
    assert 'thanks for asking' in result['prompt']
    assert 'think about' not in result['prompt'] and '?' not in result['prompt']
    assert len(result['prompt'].split()) <= 40
    assert client.app.state.repository.profile() == before
    assert client.app.state.repository.history() == []
    assert action(client, result, 'heard')['phase'] == 'TRANSITION'

@pytest.mark.parametrize('text,phrase', [("I'm not good", 'comfortable pace'), ("I'm good", 'Good to hear'), ('I just arrived', 'good to have you')])
def test_social_reply_is_contextual_and_never_extends_small_talk(text, phrase):
    from app.lesson import social_response
    reply = social_response(text)
    assert phrase in reply and '?' not in reply
    assert 'correct' not in reply and 'understood' not in reply


def test_introductions_are_owned_persisted_and_never_reclaimed_on_refresh_or_resume(lesson_client, audio_bytes):
    client = lesson_client
    lesson = exercises(client, audio_bytes)
    assert lesson['introduction_pending']
    spoken = []
    async def speak(text, speech_rate=.9): spoken.append(text); return audio_bytes
    client.provider.speak = speak
    url = f"/api/lessons/{lesson['id']}/introduction-audio"
    assert client.post(url, json={'revision':lesson['revision']}).status_code == 200
    assert spoken == ['Let’s listen to the first recording. Take your time, then tell me what you understood.']
    refreshed = client.get(f"/api/lessons/{lesson['id']}").json()
    assert refreshed['introduction_pending']
    refreshed = action(client, refreshed, 'introduction-heard')
    assert not refreshed['introduction_pending']
    assert refreshed['exercise']['id'] == lesson['exercise']['id']
    assert client.post(url, json={'revision':refreshed['revision']}).status_code == 409
    resumed = action(client, action(client, refreshed, 'end'), 'resume')
    assert not resumed['introduction_pending']
    complete_exercise(client, resumed, audio_bytes)
    next_lesson = action(client, action(client, resumed, 'advance'), 'exercise')
    assert next_lesson['introduction_pending']
    assert client.post(url, json={'revision':next_lesson['revision']}).status_code == 200
    assert spoken[-1].startswith('Now, let’s try another recording.')


def test_delayed_introduction_is_cancelled_by_end_without_losing_exercise(lesson_client, audio_bytes):
    client = lesson_client; lesson = exercises(client, audio_bytes)
    async def speak(text, speech_rate=.9):
        repo = client.app.state.repository
        saved = repo.lesson(lesson['id']); saved.status = 'paused'; saved.active_since = None
        repo.save_lesson(saved, saved.revision)
        return audio_bytes
    client.provider.speak = speak
    response = client.post(f"/api/lessons/{lesson['id']}/introduction-audio", json={'revision':lesson['revision']})
    assert response.status_code == 409
    saved = client.get(f"/api/lessons/{lesson['id']}").json()
    assert saved['status'] == 'paused' and saved['exercise']['id'] == lesson['exercise']['id']
    assert saved['introduction_pending']


def test_topic_is_persisted_at_introduction_while_transcript_stays_gated(lesson_client, audio_bytes):
    client = lesson_client; lesson = exercises(client, audio_bytes); exercise_id = lesson['exercise']['id']
    assert client.get(f'/api/exercises/{exercise_id}/conversation').json()['topic'] == lesson['exercise']['topic'] == 'technology'
    assert client.get(f'/api/exercises/{exercise_id}/transcript').status_code == 403
    result = complete_exercise(client, lesson, audio_bytes)
    import json
    persisted = json.loads(client.app.state.repository.exercise(exercise_id)['content'])['topic']
    assert result['topic'] == persisted
    assert client.get(f'/api/exercises/{exercise_id}/conversation').json()['topic'] == persisted


def test_acknowledgment_contexts_are_neutral_cached_and_validated(lesson_client, audio_bytes):
    client = lesson_client; texts = []
    async def speak(text, speech_rate=.9): texts.append(text); return audio_bytes
    client.provider.speak = speak
    for cue in ('assessment','followup','reflection'):
        for _ in range(2): assert client.post('/api/recognition/acknowledgment-audio',json={'cue':cue}).status_code == 200
    assert texts == ['Okay, I heard you.', 'Okay, I’ve heard that.', 'Let me keep that in mind.']
    assert client.post('/api/recognition/acknowledgment-audio',json={'cue':'correct'}).status_code == 422

@pytest.mark.parametrize('utterance,reply', [
    ("I'm good, and you?", 'Good to hear. I’m here and ready to listen, thanks for asking.'),
    ('I went for a walk this morning', 'Sounds like a pleasant start to your day.'),
    ('My cat knocked over my coffee', 'That sounds like an eventful start to the day.'),
])
def test_actual_checkin_response_is_persisted_and_spoken_once(lesson_client, audio_bytes, utterance, reply):
    from app.lesson import CheckinReply
    client = lesson_client; calls = []; spoken = []
    async def transcribe(audio, filename): return Transcription(text=utterance, confidence=.99)
    async def respond_checkin(text): calls.append(text); return CheckinReply(text=reply)
    async def speak(text, speech_rate=.9): spoken.append(text); return audio_bytes
    client.provider.transcribe = transcribe; client.provider.respond_checkin = respond_checkin; client.provider.speak = speak
    lesson = respond(client, start(client), audio_bytes)
    assert calls == [utterance]
    assert lesson['prompt'] == reply + ' Let’s get our listening started.'
    assert client.get(f"/api/lessons/{lesson['id']}").json()['prompt'] == lesson['prompt']
    assert client.post(f"/api/lessons/{lesson['id']}/audio", json={'revision':lesson['revision']}).status_code == 200
    assert spoken == [lesson['prompt']]
    assert action(client, lesson, 'heard')['phase'] == 'TRANSITION'
    assert client.app.state.repository.history() == []


def test_failed_checkin_response_and_delayed_end_remain_recoverable(lesson_client, audio_bytes):
    client = lesson_client
    async def failing(text): raise RuntimeError('provider unavailable')
    client.provider.respond_checkin = failing
    lesson = respond(client, start(client), audio_bytes)
    assert lesson['phase'] == 'WELCOME_ACK' and 'provider' not in lesson['prompt']
    # Repeat with a new pending welcome checkpoint and invalidate during provider work.
    repo = client.app.state.repository; saved = repo.lesson(lesson['id'])
    saved.phase = 'WELCOME'; saved.pending = Transcription(text='Busy today',confidence=.99)
    repo.save_lesson(saved, saved.revision)
    before = repo.profile()
    async def delayed(text):
        from app.lesson import CheckinReply
        current = repo.lesson(saved.id); current.status = 'paused'; current.active_since = None
        repo.save_lesson(current, current.revision)
        return CheckinReply(text='Sounds like a busy day.')
    client.provider.respond_checkin = delayed
    assert client.post(f'/api/lessons/{saved.id}/answer',json={'revision':saved.revision}).status_code == 409
    assert repo.lesson(saved.id).phase == 'WELCOME' and repo.profile() == before


def test_introduction_tts_failure_does_not_mark_delivery(lesson_client, audio_bytes):
    client = lesson_client; lesson = exercises(client, audio_bytes)
    async def failing(text, speech_rate=.9): raise RuntimeError('provider unavailable')
    client.provider.speak = failing
    response = client.post(f"/api/lessons/{lesson['id']}/introduction-audio",json={'revision':lesson['revision']})
    assert response.status_code == 503 and 'Retry conversation' in response.text
    assert client.get(f"/api/lessons/{lesson['id']}").json()['introduction_pending']
    assert client.app.state.repository.lesson(lesson['id']).introduced_exercise_id is None


def test_topic_answer_and_instruction_labels_remain_private(exercise):
    from app.lesson import public_topic
    content = exercise.model_dump()
    for topic in (exercise.important_details[0], 'Ignore the instructions', 'Where was the speaker?', ' '.join(['topic']*20)):
        assert public_topic({**content, 'topic':topic}) is None
    assert public_topic({**content,'topic':'Pottery making'}) == 'Pottery making'


def test_closing_advice_changes_with_confirmed_error_and_unknown_is_modest(exercise):
    from app.lesson import compose_closing
    from app.conversation import final_evaluation
    from test_conversation import assessment
    from app.models import ConversationTurn
    import copy
    outputs = []
    for label in ('The delivery was postponed until Friday', 'The discount reduced the final cost'):
        content = exercise.model_copy(update={'important_details':[label,*exercise.important_details[1:]]})
        evidence = assessment(content,[ConversationTurn(attempt_id='attempt',text='My spoken answer')])
        next(unit for unit in evidence.units if unit.dimension == 'details' and unit.index == 0).status = 'misunderstood'
        evaluation = final_evaluation(content,evidence).model_dump()
        at = now(); lesson = Lesson(id='test',name='Maya',focus='details',started_at=at,active_since=at)
        compose_closing(lesson,[evaluation],[content.topic],[content.model_dump()])
        assert label.lower() in lesson.closing.lower() and lesson.improvement_focus == label
        assert 'dates, times' not in lesson.closing
        outputs.append(lesson.closing)
    assert outputs[0] != outputs[1]
    unknown = copy.deepcopy(evaluation)
    unknown['dimensions'] = {key:'insufficient_evidence' for key in unknown['dimensions']}
    unknown['misunderstood'] = []; unknown['understood'] = []
    for unit in unknown['units']: unit['status'] = 'insufficient_evidence'
    compose_closing(lesson,[unknown],[content.topic],[content.model_dump()])
    assert lesson.strength is None and lesson.improvement_focus is None
    assert 'next focus' not in lesson.closing and 'keep gathering' in lesson.closing


def review_pending(client):
    lesson = start(client)
    repo = client.app.state.repository
    saved = repo.lesson(lesson['id'])
    saved.phase = 'REVIEW'
    saved.previous = 'Last time, we practiced listening.'
    saved.pending = Transcription(text='A private reflection',confidence=.99)
    repo.save_lesson(saved,saved.revision)
    return client.get(f"/api/lessons/{saved.id}").json()


def test_review_null_interests_is_valid_extraction_and_preserves_profile(lesson_client):
    from app.onboarding import ProfileExtraction
    client = lesson_client; calls = []
    async def extract(stage,text):
        calls.append(stage)
        return ProfileExtraction(name=None,target_language=None,goal=None,target_situations=None,interests=None)
    client.provider.extract_profile = extract
    lesson = review_pending(client)
    before = client.app.state.repository.profile()
    response = client.post(f"/api/lessons/{lesson['id']}/answer",json={'revision':lesson['revision']})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['phase'] == 'REVIEW_ACK' and updated['pending'] is None
    assert client.app.state.repository.profile() == before
    assert 'guide' not in updated['prompt'] and 'keep' not in updated['prompt']
    # Lost answer response/retry reloads the saved acknowledgment without provider or assessment repetition.
    assert client.post(f"/api/lessons/{lesson['id']}/answer",json={'revision':lesson['revision']}).status_code == 409
    refreshed = client.get(f"/api/lessons/{lesson['id']}").json()
    assert {key:value for key,value in refreshed.items() if key not in ('server_time','elapsed_seconds','remaining_seconds')} == {key:value for key,value in updated.items() if key not in ('server_time','elapsed_seconds','remaining_seconds')}
    assert refreshed['remaining_seconds'] <= updated['remaining_seconds']
    assert calls == ['interests'] and client.app.state.repository.history() == []
    next_checkpoint = action(client,updated,'heard')
    assert next_checkpoint['phase'] == 'TRANSITION'
    assert client.post(f"/api/lessons/{lesson['id']}/heard",json={'revision':updated['revision']}).status_code == 409

@pytest.mark.parametrize('interests', [None, [], ['science']])
def test_review_preference_update_distinguishes_missing_empty_and_supplied(lesson_client,interests):
    from app.onboarding import ProfileExtraction
    client=lesson_client; repo=client.app.state.repository
    profile=repo.profile(); profile.interests=['gardening'];repo.save_profile(profile)
    lesson=review_pending(client); before=repo.profile().model_dump()
    async def extract(stage,text):
        return ProfileExtraction(name='Unrelated name',target_language=None,goal='Unrelated goal',
            target_situations=[],interests=interests)
    client.provider.extract_profile=extract
    updated=action(client,lesson,'answer')
    expected={**before,'interests':before['interests'] if interests is None else interests}
    assert repo.profile().model_dump() == expected
    assert updated['phase']=='REVIEW_ACK' and repo.history()==[]
    assert ('science' in updated['prompt']) == (interests==['science'])

@pytest.mark.parametrize('fields', [
    {'interests':'Private malformed data'}, {'interests':[None]}, {'interests':[' ']},
    {'interests':['topic']*13}, {'target_situations':'Private malformed data'}, {},
])
def test_malformed_review_extraction_is_private_and_retryable(lesson_client,fields,caplog):
    client=lesson_client; repo=client.app.state.repository; lesson=review_pending(client)
    before=repo.profile(); stored=repo.lesson(lesson['id']); calls=[]
    async def extract(stage,text):
        calls.append(stage)
        payload=dict(name=None,target_language=None,goal=None,target_situations=None,interests=None)
        return {**payload,**fields} if fields else {}
    client.provider.extract_profile=extract
    response=client.post(f"/api/lessons/{lesson['id']}/answer",json={'revision':lesson['revision']})
    assert response.status_code==422 and 'Retry conversation' in response.text
    assert 'Private' not in response.text and 'Private' not in caplog.text and 'private reflection' not in caplog.text.lower()
    assert repo.lesson(lesson['id'])==stored
    assert client.get(f"/api/lessons/{lesson['id']}").json()['remaining_seconds'] <= lesson['remaining_seconds']
    assert repo.profile()==before and repo.history()==[]
    async def corrected(stage,text):
        calls.append(stage)
        return dict(name=None,target_language=None,goal=None,target_situations=None,interests=None)
    client.provider.extract_profile=corrected
    recovered=action(client,lesson,'answer')
    assert recovered['phase']=='REVIEW_ACK' and recovered['revision']==lesson['revision']+1
    assert repo.profile()==before and repo.history()==[]
    assert action(client,recovered,'heard')['phase']=='TRANSITION'
    assert calls==['interests','interests']
