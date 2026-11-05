"""Audli 2.0 Milestone 03: the lesson library, saved sessions and honest progress.

Everything here is real results from real attempts: there is no streak, level or mastery anywhere in the API, and the tests check
that nothing of the sort can appear. Runs on SQLite always and on real PostgreSQL when AUDLI_TEST_POSTGRES_URL is set.
"""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, func

from app.config import Settings
from app.listening import schema as l
from app.listening.objects import LocalObjectStore
from app.listening.providers import FakeAssessor
from app.listening.seed import seed_all
from app.main import create_app
from conftest import ASGIClient, FakeProvider
from test_auth import AccountClient, auth_response, settings_for
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_journey import by_kind, post_answer, spoken_action, upload, work

SECRET_WORDS = ('correct_option_id', 'explanation', 'accepted', 'correct_order', 'listening-tmp', 'recording_key', 'transcript', 'sha256', 'object_key')
FORBIDDEN_CLAIMS = ('streak', 'mastery', 'proficiency', 'level_up', 'xp', 'score_percent', 'rank')


def prepare_all(repo, tmp_path):
    from app.listening.api import prepare
    prepare(repo.engine)
    objects = LocalObjectStore(tmp_path / 'listening_objects', b'k' * 32)
    seed_all(repo.engine, objects)
    return objects


@pytest.fixture
def library(make_repository, tmp_path):
    owner = str(uuid4())
    repo = make_repository(owner)
    objects = prepare_all(repo, tmp_path)
    app = create_app(Settings(data_dir=tmp_path, provider='demo', listening_enabled=True, _env_file=None), FakeProvider(), repository=repo)
    return SimpleNamespace(client=ASGIClient(app, None), repo=repo, engine=repo.engine, objects=objects, owner=owner, tmp=tmp_path)


@pytest.fixture
def two(make_repository, tmp_path):
    repo = make_repository(None)
    objects = prepare_all(repo, tmp_path)
    app = create_app(settings_for(tmp_path, listening_enabled=True), FakeProvider(), repository=repo,
                     auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
    return SimpleNamespace(a=AccountClient(app, None, 'valid-a'), b=AccountClient(app, None, 'valid-b'), repo=repo, engine=repo.engine, objects=objects, tmp=tmp_path)


def catalog(client):
    response = client.get('/api/listening/lessons')
    assert response.status_code == 200
    return {item['slug']: item for item in response.json()}


def open_lesson(client, lesson_id, request_id=None):
    return client.post(f'/api/listening/lessons/{lesson_id}/attempts', json={'client_request_id': request_id or uuid4().hex}).json()


def answer_all(client, view, spoken='skip', sequence=None, text='herbs', option='b'):
    post_answer(client, view, 'multiple_choice', option_id=option)
    if spoken == 'skip':
        spoken_action(client, view, 'skip')
    post_answer(client, view, 'fill_blank', text=text)
    ids = [o['id'] for o in by_kind(view)['sequencing']['options']]
    return post_answer(client, view, 'sequencing', order=sequence or ids).json()


RESTAURANT, PHARMACY = 'ordering-food-at-a-restaurant', 'at-the-pharmacy'


def test_the_library_lists_every_published_lesson_with_level_duration_and_a_new_status(library):
    items = catalog(library.client)
    assert list(items) == [PHARMACY, RESTAURANT]                              # easier first
    for item, level in ((items[PHARMACY], 'A2'), (items[RESTAURANT], 'B1')):
        assert item['cefr'] == level and item['duration_ms'] > 30_000 and item['accents'] == ['american']
        assert (item['status'], item['attempt_id'], item['answered'], item['correct'], item['completed_count']) == ('new', None, 0, None, 0)
    text = library.client.get('/api/listening/lessons').text
    for word in SECRET_WORDS:
        assert word not in text, word


def test_leaving_and_coming_back_resumes_the_same_attempt_without_losing_answers(library):
    items = catalog(library.client)
    view = open_lesson(library.client, items[PHARMACY]['lesson_id'])
    assert post_answer(library.client, view, 'multiple_choice', option_id='b').status_code == 200
    mid = catalog(library.client)[PHARMACY]
    assert (mid['status'], mid['answered'], mid['total'], mid['attempt_id']) == ('in_progress', 1, 4, view['attempt']['id'])
    assert catalog(library.client)[RESTAURANT]['status'] == 'new'                # another lesson is untouched
    # "Continue" then a second tab, a retry with a new request id and a double click: still one attempt, answers intact.
    for _ in range(3):
        again = open_lesson(library.client, items[PHARMACY]['lesson_id'])
        assert again['attempt']['id'] == view['attempt']['id']
        assert by_kind(again)['multiple_choice']['feedback']['is_correct'] is True
    current = library.client.get(f"/api/listening/lessons/{items[PHARMACY]['lesson_id']}/attempts/current").json()
    assert current['attempt_id'] == view['attempt']['id']
    with library.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.attempts)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(l.responses)).scalar_one() == 1
    assert post_answer(library.client, view, 'multiple_choice', option_id='b').status_code == 200   # a repeated submit is a replay
    with library.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.responses)).scalar_one() == 1


def test_the_whole_a2_lesson_works_and_a_finished_lesson_reports_real_results_only(library):
    lesson_id = catalog(library.client)[PHARMACY]['lesson_id']
    view = open_lesson(library.client, lesson_id)
    assert [e['kind'] for e in view['exercises']] == ['multiple_choice', 'spoken', 'fill_blank', 'sequencing']
    post_answer(library.client, view, 'multiple_choice', option_id='b')
    assert upload(library.client, view).status_code == 200
    post_answer(library.client, view, 'fill_blank', text='Honey')
    done = post_answer(library.client, view, 'sequencing', order=['cough', 'allergy', 'dose', 'pay']).json()
    assert done['attempt']['status'] == 'completed' and done['attempt']['correct'] == 3
    item = catalog(library.client)[PHARMACY]
    assert (item['status'], item['correct'], item['scored'], item['spoken_status'], item['completed_count']) == ('completed', 3, 3, 'pending', 1)
    assert work(library) == ['done']
    assert catalog(library.client)[PHARMACY]['spoken_status'] == 'assessed'


def test_progress_is_built_only_from_finished_results(library):
    empty = library.client.get('/api/listening/progress').json()
    assert empty == {'lessons_available': 2, 'lessons_completed': 0, 'attempts_completed': 0, 'questions_correct': 0, 'questions_scored': 0, 'recent': []}
    items = catalog(library.client)
    first = open_lesson(library.client, items[PHARMACY]['lesson_id'])
    answer_all(library.client, first, sequence=['cough', 'allergy', 'dose', 'pay'], text='honey')           # 3 of 3
    second = open_lesson(library.client, items[RESTAURANT]['lesson_id'])
    answer_all(library.client, second, option='a', text='spices')                           # sequencing as first shown: wrong too
    open_lesson(library.client, items[PHARMACY]['lesson_id'])                              # practice again: a new, unfinished attempt
    progress = library.client.get('/api/listening/progress').json()
    assert progress['lessons_completed'] == 2 and progress['attempts_completed'] == 2
    assert (progress['questions_correct'], progress['questions_scored']) == (3, 6)
    assert [(r['title'], r['status']) for r in progress['recent']] == [('At the pharmacy', 'in_progress'), ('Ordering food at a restaurant', 'completed'),
                                                                        ('At the pharmacy', 'completed')]
    by_status = {(r['title'], r['status']): r for r in progress['recent']}
    assert by_status[('At the pharmacy', 'completed')]['correct'] == 3 and by_status[('At the pharmacy', 'completed')]['scored'] == 3
    assert by_status[('Ordering food at a restaurant', 'completed')]['correct'] == 0
    assert by_status[('At the pharmacy', 'in_progress')]['correct'] is None and by_status[('At the pharmacy', 'in_progress')]['scored'] is None
    assert catalog(library.client)[PHARMACY]['status'] == 'in_progress' and catalog(library.client)[PHARMACY]['completed_count'] == 1
    text = library.client.get('/api/listening/progress').text.lower()
    for word in FORBIDDEN_CLAIMS + SECRET_WORDS:
        assert word not in text, word
    assert set(progress) == {'lessons_available', 'lessons_completed', 'attempts_completed', 'questions_correct', 'questions_scored', 'recent'}


def test_recent_activity_is_newest_first_and_bounded(library):
    items = catalog(library.client)
    for n in range(10):
        view = open_lesson(library.client, items[PHARMACY]['lesson_id'])
        answer_all(library.client, view)
    recent = library.client.get('/api/listening/progress').json()['recent']
    assert len(recent) == 8 and [r['when'] for r in recent] == sorted((r['when'] for r in recent), reverse=True)


def test_accounts_see_only_their_own_library_and_progress(two):
    a_items = {i['slug']: i for i in two.a.get('/api/listening/lessons').json()}
    view = open_lesson(two.a, a_items[PHARMACY]['lesson_id'])
    answer_all(two.a, view, sequence=['cough', 'allergy', 'dose', 'pay'])
    assert {i['slug']: i['status'] for i in two.a.get('/api/listening/lessons').json()} == {PHARMACY: 'completed', RESTAURANT: 'new'}
    b = two.b.get('/api/listening/lessons')
    assert {i['slug']: (i['status'], i['attempt_id'], i['completed_count']) for i in b.json()} == {PHARMACY: ('new', None, 0), RESTAURANT: ('new', None, 0)}
    assert view['attempt']['id'] not in b.text
    progress_b = two.b.get('/api/listening/progress').json()
    assert (progress_b['lessons_completed'], progress_b['questions_scored'], progress_b['recent']) == (0, 0, [])
    assert two.a.get('/api/listening/progress').json()['lessons_completed'] == 1
    assert two.b.get(f"/api/listening/attempts/{view['attempt']['id']}").status_code == 404
    assert two.b.get('/api/listening/lessons').status_code == 200 and ASGIClient.get(two.b, '/api/listening/progress').status_code == 200


def test_the_library_and_progress_need_a_signed_in_learner(two):
    anonymous = ASGIClient(two.a.app, None)
    assert anonymous.get('/api/listening/lessons').status_code == 401
    assert anonymous.get('/api/listening/progress').status_code == 401


def test_a_failed_spoken_answer_shows_in_the_library_as_failed_not_as_a_score(library):
    lesson_id = catalog(library.client)[PHARMACY]['lesson_id']
    view = open_lesson(library.client, lesson_id)
    answer_all(library.client, view, spoken='record', text='honey')
    assert upload(library.client, view).status_code == 200
    from app.listening.providers import ProviderPermanent
    from app.storage.repository import utcnow
    work(library, assessor=FakeAssessor(fail=ProviderPermanent('400')), now=utcnow() + timedelta(seconds=1))
    item = catalog(library.client)[PHARMACY]
    assert (item['status'], item['spoken_status'], item['correct']) == ('completed', 'failed', 2)
