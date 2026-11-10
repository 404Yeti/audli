"""The learner-facing Daily Challenge route and card data: what a learner sees, what stays out of Discover, completion credit through the real answer flow,
and that nothing private leaks. The scheduler runs against the real clock here so the route's own "today" lines up. SQLite always, PostgreSQL when configured."""
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.listening import daily as dl
from app.main import create_app
from app.storage.repository import utcnow
from conftest import ASGIClient, FakeProvider
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_daily import Clock, VariedWriter, _pg_subset, day, lab  # noqa: F401  (fixtures; _pg_subset keeps the PostgreSQL run to database-dependent tests)
from test_listening_journey import by_kind
from test_listening_library import answer_all, open_lesson

SECRET = ('correct_option_id', 'explanation', 'accepted', 'correct_order', 'object_key', 'sha256', 'recording_key', 'fingerprint', 'gate_report', 'rejected')


def client_for(make_repository, tmp_path, learner, daily=True):
    repo = make_repository(learner)
    app = create_app(Settings(data_dir=tmp_path, provider='demo', listening_enabled=True, listening_daily_enabled=daily, _env_file=None), FakeProvider(), repository=repo)
    return ASGIClient(app, None)


@pytest.fixture
def live(day, make_repository, tmp_path):
    d = day.build(clock=utcnow)
    dl.tick(d)
    day.generate(d)
    dl.tick(d)
    today = next(r for r in day.rows() if r['state'] == 'active')
    return SimpleNamespace(day=day, lesson=today['lesson_id'], date=today['challenge_date'], learner=client_for(make_repository, tmp_path, day.other),
                           factory=make_repository, tmp=tmp_path)


def test_the_route_does_not_exist_when_the_feature_is_off(day, make_repository, tmp_path):
    assert client_for(make_repository, tmp_path, day.other, daily=False).get('/api/listening/daily').status_code == 404


def test_today_shows_the_shared_challenge_with_no_private_data_and_it_is_not_in_discover(live):
    body = live.learner.get('/api/listening/daily').json()
    assert body['status'] == 'ready' and body['date'] == live.date and body['lesson']['lesson_id'] == live.lesson
    assert {'title', 'cefr', 'topic', 'accents', 'duration_ms', 'status'} <= set(body['lesson']) and body['lesson']['status'] == 'new'
    assert body['completed_today'] is False and body['streak']['current'] == 0
    assert not [w for w in SECRET if w in str(body)]
    assert live.lesson not in {i['lesson_id'] for i in live.learner.get('/api/listening/lessons').json()}            # not until its day is over
    assert live.learner.get(f'/api/listening/lessons/{live.lesson}/attempts/current').status_code == 200            # but playable by the card's link


def test_starting_resuming_and_finishing_updates_the_card_and_credits_the_day_once(live):
    view = open_lesson(live.learner, live.lesson)
    assert live.learner.get('/api/listening/daily').json()['lesson']['status'] == 'in_progress'
    answer_all(live.learner, view, text='two dollars')
    first = live.learner.get('/api/listening/daily').json()
    assert first['completed_today'] is True and first['lesson']['status'] == 'completed' and first['streak'] == {'current': 1, 'longest': 1, 'days_completed': 1}
    again = open_lesson(live.learner, live.lesson)                                                                   # replaying the same day earns nothing more
    answer_all(live.learner, again, text='two dollars')
    assert live.learner.get('/api/listening/daily').json()['streak'] == first['streak']
    assert live.learner.get('/api/listening/daily').json() == live.learner.get('/api/listening/daily').json()      # reading is repeatable and writes nothing


def test_a_second_learner_has_their_own_progress_on_the_same_challenge(live):
    other = client_for(live.factory, live.tmp, live.day.owner)
    open_lesson(live.learner, live.lesson)
    assert other.get('/api/listening/daily').json()['lesson']['status'] == 'new'


def test_when_nothing_is_live_the_card_is_unavailable_and_shows_no_lesson(day, make_repository, tmp_path):
    body = client_for(make_repository, tmp_path, day.other).get('/api/listening/daily').json()
    assert body['status'] == 'unavailable' and body['lesson'] is None and body['streak']['current'] == 0
