from uuid import uuid4
import pytest
from app.homework_voice import spoken_decision
from app.lesson import heard, now
from test_homework import client_for
from test_lesson_lifecycle import lesson_client, start, respond, action
from test_planner import ready, complete
from test_persistence import repository_factory
from test_auth import accounts
from concurrent.futures import ThreadPoolExecutor


def test_onboarding_interests_seed_first_lesson_without_topic_question(lesson_client, audio_bytes):
    client = lesson_client; repo = client.app.state.repository
    ready(repo)
    lesson = start(client)
    assert repo.lesson(lesson['id']).plan_topic.startswith('science: ')
    assert repo.lesson(lesson['id']).topic_review_due is False
    lesson = respond(client, lesson, audio_bytes)
    lesson = action(client, lesson, 'heard')
    assert lesson['phase'] == 'TRANSITION'
    assert 'What would you enjoy' not in lesson['prompt']


def test_previous_lesson_is_a_recap_without_new_topic_prompt(lesson_client, audio_bytes):
    client = lesson_client; repo = client.app.state.repository
    ready(repo); complete(repo)
    value = start(client)
    saved = repo.lesson(value['id']); saved.previous = 'Last time, we practiced listening.'; repo.save_lesson(saved, saved.revision)
    value = client.get(f"/api/lessons/{value['id']}").json()
    value = respond(client, value, audio_bytes); value = action(client, value, 'heard')
    assert value['phase'] == 'REVIEW_ACK' and '?' not in value['prompt']
    assert action(client, value, 'heard')['phase'] == 'TRANSITION'


def test_three_new_completions_claim_once_and_keep_preferences_without_adaptation(lesson_client):
    client = lesson_client; repo = client.app.state.repository; ready(repo)
    first = complete(repo)
    assert not client.post(f'/api/lessons/{first.id}/topic-checkin').json()['offered']
    complete(repo); third = complete(repo)
    before = repo.profile().model_copy(deep=True)
    offered = client.post(f'/api/lessons/{third.id}/topic-checkin').json()
    assert offered['offered'] and offered['plan']['checkin']['after_lesson'] == 3
    assert not client.post(f'/api/lessons/{third.id}/topic-checkin').json()['offered']
    assert not client.post(f'/api/lessons/{first.id}/topic-checkin').json()['offered']
    # Reloads reconcile only; they never claim or speak the next invitation.
    assert client.get('/api/plan').json()['checkin']['available']
    result = client.post('/api/plan/checkin/text', json={'revision': offered['plan']['revision'], 'text': 'keep them'})
    assert result.status_code == 200 and result.json()['checkin']['status'] == 'skipped'
    assert repo.profile() == before
    for _ in range(3): sixth = complete(repo)
    assert client.post(f'/api/lessons/{sixth.id}/topic-checkin').json()['offered']
    assert client.post(f'/api/lessons/{uuid4()}/topic-checkin').status_code == 404


@pytest.mark.parametrize('text,accept,topic', [('yes please', True, None), ('no thanks', False, None), ('yes about science', True, 'science'), ("I'd like to travel", True, 'travel')])
def test_typed_homework_uses_spoken_intent_and_idempotent_assignment(tmp_path, text, accept, topic):
    assert spoken_decision(text).model_dump() == {'accept': accept, 'topic': topic}
    with client_for(tmp_path) as client:
        repo = client.app.state.repository; lesson = complete(repo); before = repo.profile(); plan = repo.roadmap()
        offered = client.post(f'/api/lessons/{lesson.id}/homework').json()
        path = f"/api/homework/{offered['id']}/decision/text"
        saved = client.post(path, json={'text': text}).json()
        assert saved['status'] == ('unavailable' if topic == 'travel' else 'assigned' if accept else 'skipped')
        assert client.post(path, json={'text': text}).json()['id'] == saved['id']
        assert repo.profile() == before and repo.roadmap() == plan
        assert repo.lesson(lesson.id).status == 'completed'
        assert client.get('/api/homework').json()['assignments'][0]['id'] == saved['id']


def test_ambiguous_typed_reply_never_discovers_or_changes_completed_lesson(tmp_path):
    with client_for(tmp_path) as client:
        repo = client.app.state.repository; lesson = complete(repo)
        offered = client.post(f'/api/lessons/{lesson.id}/homework').json()
        result = client.post(f"/api/homework/{offered['id']}/decision/text", json={'text': 'yes but no thanks'})
        assert result.status_code == 422
        assert client.app.state.homework_engine.provider.calls == 0
        assert repo.lesson(lesson.id).status == 'completed'


def test_checkin_claim_is_atomic(repository_factory):
    repo = repository_factory(); ready(repo)
    for _ in range(3): lesson = complete(repo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: repo.roadmap(claim_lesson=lesson.id), range(2)))
    assert sum(item is not None for item in claims) == 1


def test_answered_topic_preferences_persist_and_same_lesson_cannot_offer_again(lesson_client, monkeypatch):
    client = lesson_client
    repo = client.app.state.repository
    ready(repo)
    for _ in range(3):
        lesson = complete(repo)
    offered = client.post(f'/api/lessons/{lesson.id}/topic-checkin').json()
    assert offered['offered']

    async def extract(stage, text):
        return {'name': None, 'target_language': None, 'goal': None, 'target_situations': None, 'interests': ['travel']}

    monkeypatch.setattr(client.provider, 'extract_profile', extract)
    response = client.post('/api/plan/checkin/text', json={
        'revision': offered['plan']['revision'], 'text': 'More travel please',
    })
    assert response.status_code == 200, response.text
    feedback = repo.roadmap().feedback[-1]
    assert feedback.response == 'More travel please'
    assert feedback.interests == ['travel']
    for _ in range(3):
        assert not client.get('/api/plan').json()['checkin']['available']
        assert not client.post(f'/api/lessons/{lesson.id}/topic-checkin').json()['offered']
    assert repo.roadmap().feedback[-1] == feedback


def test_topic_claim_is_owned_and_never_claims_active_or_unrelated_lessons(accounts):
    from test_auth import USER_A, USER_B
    a, b, _ = accounts
    repo = a.app.state.repository.for_learner(USER_A); ready(repo)
    for _ in range(3): lesson = complete(repo)
    other = a.app.state.repository.for_learner(USER_B); ready(other)
    assert b.post(f'/api/lessons/{lesson.id}/topic-checkin').status_code == 404
    assert a.post(f'/api/lessons/{lesson.id}/topic-checkin').json()['offered']
