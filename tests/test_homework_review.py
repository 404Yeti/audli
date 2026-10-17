from uuid import uuid4
from sqlalchemy import select
import pytest
from app.homework_review import prepare, answer
from app.lesson import Lesson, now
from app.storage import homework as store, schema as s
from test_persistence import repository_factory
from test_homework import video, client_for
from app.services.youtube import parse_video
from test_planner import ready, complete


def active(repo):
    lesson = Lesson(id=str(uuid4()), name='Maya', focus='details', phase='TRANSITION', started_at=now(), active_since=now())
    repo.create_lesson(lesson)
    return lesson


def assigned(repo):
    previous = complete(repo)
    assignment = store.offer(repo, previous.id)
    _, token = store.reserve(repo, assignment.id, True, 'science', 5)
    return store.finish(repo, assignment.id, token, [parse_video(video(), 'science')])


def test_review_is_ungraded_bounded_owned_and_idempotent(repository_factory):
    repo = repository_factory(); ready(repo)
    assignment = assigned(repo); lesson = active(repo)
    profile = repo.profile(); plan = repo.roadmap(); before = repo.lesson(lesson.id)
    review = prepare(repo, lesson.id)
    assert review.phase == 'watched' and prepare(repo, lesson.id) == review
    review = answer(repo, lesson.id, review.revision, 'yes')
    assert review.phase == 'summary'
    assert answer(repo, lesson.id, 0, 'yes') == review  # repeated delivered event
    review = answer(repo, lesson.id, review.revision, 'I think it described a new idea.')
    assert review.phase == 'detail'
    review = answer(repo, lesson.id, review.revision, 'I remember an example about a city.')
    assert review.phase == 'done' and review.grounding == 'unverified_self_report'
    assert store.get(repo, assignment.id).status == 'discussed'
    assert answer(repo, lesson.id, review.revision, 'repeat') == review
    assert repo.profile() == profile and repo.roadmap() == plan and repo.lesson(lesson.id) == before
    with repo.engine.connect() as db:
        rows = db.execute(select(s.reflections).where(s.reflections.c.user_id == repo.learner_id)).mappings().all()
        assert len(rows) == 1 and len(rows[0]['data']['answers']) == 2
    other = repository_factory(str(uuid4())); ready(other)
    with pytest.raises(LookupError): prepare(other, lesson.id)
    with pytest.raises(LookupError): answer(other, lesson.id, 0, 'yes')
    lesson.status = 'completed'; lesson.phase = 'COMPLETED'; lesson.completed_at = now()
    repo.save_lesson(lesson, lesson.revision)
    assert prepare(repo, active(repo).id) is None


@pytest.mark.parametrize('reply', ['not yet', 'no thanks', 'skip', 'maybe next time'])
def test_not_watched_or_skipped_never_penalizes(repository_factory, reply):
    repo = repository_factory(); ready(repo); assignment = assigned(repo); lesson = active(repo)
    profile = repo.profile()
    review = prepare(repo, lesson.id)
    assert answer(repo, lesson.id, review.revision, reply).phase == 'done'
    assert store.get(repo, assignment.id).status == 'skipped'
    assert repo.profile() == profile


def test_uncertain_review_clarifies_once_then_skips(repository_factory):
    repo = repository_factory(); ready(repo); assignment = assigned(repo); lesson = active(repo)
    review = prepare(repo, lesson.id)
    review = answer(repo, lesson.id, review.revision, 'maybe')
    assert review.phase == 'clarify'
    assert prepare(repo, lesson.id) == review
    review = answer(repo, lesson.id, review.revision, 'unreliable', False)
    assert review.phase == 'done' and store.get(repo, assignment.id).status == 'skipped'


def test_summary_uncertainty_can_recover_without_invented_corrections(repository_factory):
    repo = repository_factory(); ready(repo); assigned(repo); lesson = active(repo)
    review = prepare(repo, lesson.id)
    review = answer(repo, lesson.id, review.revision, 'yes')
    review = answer(repo, lesson.id, review.revision, '', False)
    assert review.phase == 'clarify'
    assert 'remember' in review.prompt and 'correct' not in review.prompt
    assert answer(repo, lesson.id, review.revision, 'A useful idea.').phase == 'detail'


def test_no_assignment_and_self_reported_completed_assignment(repository_factory):
    repo = repository_factory(); ready(repo); lesson = active(repo)
    assert prepare(repo, lesson.id) is None
    lesson.status = 'completed'; lesson.phase = 'COMPLETED'; lesson.completed_at = now(); repo.save_lesson(lesson, 0)
    assignment = assigned(repo); store.change_status(repo, assignment.id, 'completed')
    assert prepare(repo, active(repo).id).phase == 'watched'


def test_http_review_and_completed_lesson_closing_audio(tmp_path, audio_bytes, monkeypatch):
    from app.models import Transcription
    from conftest import FakeProvider
    async def transcribe(self, data, filename):
        return Transcription(text='Yes', confidence=.99, uncertainty=['Too little recognized speech to evaluate reliably.'])
    async def speak(self, text, *args): return audio_bytes
    monkeypatch.setattr(FakeProvider, 'transcribe', transcribe)
    monkeypatch.setattr(FakeProvider, 'speak', speak)
    with client_for(tmp_path) as client:
        repo = client.app.state.repository; assignment = assigned(repo); lesson = active(repo)
        path = f'/api/lessons/{lesson.id}/homework-review'
        assert client.post(path, json={}).json()['phase'] == 'watched'
        assert client.post(path+'/audio', json={'revision':0}).status_code == 200
        result = client.post(path+'/attempts', data={'revision':'0'}, files={'audio':('choice.wav',audio_bytes,'audio/wav')})
        assert result.status_code == 200 and result.json()['phase'] == 'summary'
        assert client.post(path+'/skip', json={'revision':1}).json()['phase'] == 'done'
        assert store.get(repo, assignment.id).status == 'skipped'
        assert client.post(f'/api/lessons/{lesson.id}/farewell-audio').status_code == 409
        assert client.post(f'/api/lessons/{assignment.lesson_id}/farewell-audio').status_code == 200
    with client_for(tmp_path/'disabled', enabled=False) as client:
        assert client.post('/api/lessons/unknown/homework-review', json={}).status_code == 404
