from uuid import uuid4
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.lesson import Lesson, now
from app.planner import Roadmap
from app.storage.planner import due
from app.storage.migrations import postgres_planner_migration
from test_persistence import repository_factory
from test_lesson_lifecycle import lesson_client


def complete(repo):
    lesson = Lesson(id=str(uuid4()), name='Maya', focus='details', started_at=now(), active_since=now())
    repo.create_lesson(lesson)
    lesson.status = 'completed'; lesson.phase = 'COMPLETED'; lesson.completed_at = now(); lesson.active_since = None
    repo.save_lesson(lesson, 0)
    return lesson


def ready(repo):
    profile = repo.profile(); profile.onboarding_status = 'complete'; profile.interests = ['science', 'travel']; repo.save_profile(profile)


def test_roadmap_lifecycle_and_restart_preserve_history(repository_factory):
    repo = repository_factory(); ready(repo)
    first = repo.roadmap()
    assert len(first.upcoming) == 3 and first.upcoming[0].topic == 'science: a short update'
    assert repo.roadmap() == first
    lesson = complete(repo)
    assert len(repo.roadmap().completed_ids) == 1
    with pytest.raises(ValueError): repo.save_lesson(lesson, 0)
    complete(repo); complete(repo)
    plan = repo.roadmap(); assert due(plan) == 3
    difficulty = repo.profile().difficulty
    answered = repo.roadmap(revision=plan.revision, feedback={'status':'answered', 'response':'More travel and main ideas please', 'interests':['travel'], 'focus':'main_idea'})
    assert due(answered) is None
    assert all(item.topic.startswith('travel: ') and item.focus == 'main_idea' and item.difficulty == difficulty for item in answered.upcoming)
    with pytest.raises(ValueError): repo.roadmap(revision=plan.revision, feedback={'status':'skipped'})
    profile = repo.profile(); profile.onboarding_status = 'in_progress'; profile.goal = 'New goal'; repo.save_profile(profile)
    restarted = repo.roadmap(); assert len(restarted.completed_ids) == 3 and restarted.feedback == answered.feedback
    assert 'New goal' in restarted.upcoming[0].objective
    ready(repo)
    for _ in range(3): complete(repo)
    plan = repo.roadmap(); assert due(plan) == 6
    assert due(repo.roadmap(revision=plan.revision, feedback={'status':'skipped'})) is None
    assert repository_factory().roadmap() == repo.roadmap()


def test_isolation_active_lesson_and_no_content(repository_factory):
    repo = repository_factory(); ready(repo)
    for _ in range(3): complete(repo)
    other = repository_factory(str(uuid4())); ready(other)
    assert other.roadmap().completed_ids == []
    plan = repo.roadmap()
    lesson = Lesson(id=str(uuid4()), name='Maya', focus='details', started_at=now(), active_since=now())
    repo.create_lesson(lesson)
    with pytest.raises(ValueError, match='Finish or resume'): repo.roadmap(revision=plan.revision, feedback={'status':'skipped'})
    assert due(repo.roadmap()) == 3 and len(repo.roadmap().completed_ids) == 3
    assert repo.current_exercise() is None
    with pytest.raises(ValidationError): Roadmap.model_validate({'script':'secret'})


def test_api_checkin_saved_optional_uncertain_and_replay(lesson_client, audio_bytes):
    client = lesson_client; repo = client.app.state.repository
    for _ in range(3): complete(repo)
    plan = client.get('/api/plan').json()
    assert plan['checkin']['available'] and len(plan['upcoming']) == 3
    revision = plan['revision']
    for _ in range(2):
        assert client.post('/api/plan/checkin/audio', json={'revision':revision}).status_code == 200
    assert client.get('/api/plan').json()['revision'] == revision
    client.provider.uncertain = True
    response = client.post('/api/plan/checkin/attempts', data={'revision':revision}, files={'audio':('answer.wav',audio_bytes,'audio/wav')})
    assert response.status_code == 200, response.text
    uncertain = response.json()
    assert client.post('/api/plan/checkin/answer', json={'revision':uncertain['revision']}).status_code == 422
    assert client.post('/api/plan/checkin/skip', json={'revision':uncertain['revision']}).json()['checkin']['status'] == 'skipped'
    assert client.post('/api/plan/checkin/skip', json={'revision':uncertain['revision']}).status_code == 409
    assert len(repo.roadmap().completed_ids) == 3


def test_planner_postgres_migration():
    sql = postgres_planner_migration()
    assert Path('docs/postgres-004.sql').read_text() == sql
    assert 'ENABLE ROW LEVEL SECURITY' in sql and 'REVOKE ALL' in sql and 'FOREIGN KEY(user_id)' in sql


def test_natural_answer_updates_plan_only_and_invalid_upload(lesson_client, audio_bytes):
    client = lesson_client; repo = client.app.state.repository
    for _ in range(3): complete(repo)
    before = repo.profile()
    async def transcribe(data, filename):
        return {'text': 'The pace is fine. I would love to hear about travel and focus on the main idea.', 'confidence':.99, 'uncertainty':[]}
    async def extract(stage, text):
        return {'name':None, 'target_language':None, 'goal':None, 'target_situations':None, 'interests':['travel']}
    client.provider.transcribe = transcribe; client.provider.extract_profile = extract
    plan = client.get('/api/plan').json()
    bad = client.post('/api/plan/checkin/attempts', data={'revision':plan['revision']}, files={'audio':('fake.wav',b'not audio','audio/wav')})
    assert bad.status_code == 422
    attempt = client.post('/api/plan/checkin/attempts', data={'revision':plan['revision']}, files={'audio':('answer.wav',audio_bytes,'audio/wav')}).json()
    result = client.post('/api/plan/checkin/answer', json={'revision':attempt['revision']})
    assert result.status_code == 200, result.text
    assert result.json()['checkin']['status'] == 'answered'
    assert all(item['topic'].startswith('travel: ') and item['focus'] == 'main_idea' for item in result.json()['upcoming'])
    assert repo.profile() == before
    assert repo.roadmap().pending is None
    assert client.post('/api/plan/checkin/audio', json={'revision':result.json()['revision']}).status_code == 409


from test_auth import accounts, USER_A
from conftest import ASGIClient


def test_planner_authentication_isolation(accounts):
    a, b, _ = accounts
    assert a.get('/api/plan').status_code == 200
    owner = a.app.state.repository.for_learner(USER_A); ready(owner)
    for _ in range(3): complete(owner)
    plan = a.get('/api/plan').json()
    assert plan['completed_lessons'] == 3
    assert b.get('/api/plan').json()['completed_lessons'] == 0
    assert b.post('/api/plan/checkin/skip', json={'revision':plan['revision']}).status_code == 409
    assert ASGIClient(a.app, a.provider).get('/api/plan').status_code == 401
    assert ASGIClient(a.app, a.provider).post('/api/plan/checkin/skip', json={'revision':plan['revision']}).status_code == 401


def test_estimates_follow_evidence_and_on_demand_generation_uses_outline(lesson_client, audio_bytes):
    client = lesson_client; repo = client.app.state.repository
    profile = repo.profile(); profile.interests = ['travel']; profile.focus = 'inference'; profile.difficulty.speech_rate = .8
    repo.save_profile(profile)
    plan = client.get('/api/plan').json()
    assert all(item['difficulty']['speech_rate'] == .8 and item['focus'] == 'inference' for item in plan['upcoming'])
    assert repo.current_exercise() is None
    lesson = client.post('/api/lessons/start').json()
    stored = repo.lesson(lesson['id'])
    assert stored.plan_number == 1 and stored.plan_topic == 'travel: a short update' and stored.focus == 'inference'
    # Reach generation using a checkpoint, without calling any live provider.
    stored.phase = 'EXERCISE'; repo.save_lesson(stored, stored.revision)
    captured = []
    original = client.provider.generate
    async def generate(value):
        captured.append(value.model_copy(deep=True))
        return await original(value)
    client.provider.generate = generate
    result = client.post(f'/api/lessons/{stored.id}/exercise', json={'revision':stored.revision})
    assert result.status_code == 200, result.text
    assert captured[0].interests == ['travel: a short update'] and captured[0].focus == 'inference'
    assert client.get('/api/plan').json()['completed_lessons'] == 0
    assert client.get(f"/api/exercises/{result.json()['exercise']['id']}/transcript").status_code == 403


def test_restart_api_preserves_completed_history_and_checkin(lesson_client):
    client = lesson_client; repo = client.app.state.repository
    for _ in range(3): complete(repo)
    plan = repo.roadmap(); repo.roadmap(revision=plan.revision, feedback={'status':'skipped'})
    before = repo.profile(); history = repo.accent_lesson_history()
    response = client.post('/api/onboarding/restart', json={'confirmation':'RESTART ONBOARDING','revision':before.onboarding.revision})
    assert response.status_code == 200, response.text
    after = repo.profile()
    assert after.onboarding_status == 'not_started' and after.onboarding.revision == before.onboarding.revision + 1
    assert after.listening_profile == before.listening_profile and after.difficulty == before.difficulty
    assert after.completed_attempts == before.completed_attempts and repo.accent_lesson_history() == history
    assert len(repo.roadmap().completed_ids) == 3 and due(repo.roadmap()) is None
    assert client.post('/api/onboarding/restart', json={'confirmation':'RESTART ONBOARDING','revision':before.onboarding.revision}).status_code == 409
    assert client.get('/api/plan').json()['readiness'] == 'onboarding'
    assert client.post('/api/onboarding/start', json={'revision':after.onboarding.revision}).status_code == 200


def test_restart_does_not_interrupt_a_lesson(lesson_client):
    client = lesson_client; repo = client.app.state.repository
    lesson = client.post('/api/lessons/start').json(); before = repo.profile()
    assert client.post('/api/onboarding/restart', json={'confirmation':'RESTART ONBOARDING','revision':before.onboarding.revision}).status_code == 409
    assert repo.profile() == before and repo.lesson().id == lesson['id']
