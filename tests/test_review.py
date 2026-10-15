"""Review reads final owned evidence without changing learning state or calling providers."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, update, event

from app.main import create_app
from app.config import Settings
from app.models import EvidenceAssessment, LearnerProfile
from app.evaluation import expected_units
from app.conversation import final_evaluation
from app.review import display_zone, timestamp
from app.storage import schema as s
from app.storage.repository import evaluation_values
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory  # noqa: F401
from test_auth import accounts, USER_A, USER_B  # noqa: F401

UTC = timezone.utc


def dt(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def seed(repo, exercise, *, at='2026-10-10T12:00:00Z', kind='lesson', status='completed',
         outcomes=None, count=1, version='v0.2', level='B1', audio=False, metadata=True):
    """Versioned historical fixtures, including imperfect records production reads must retain."""
    entry_id = str(uuid4())
    at = dt(at) if at else None
    exercises = []
    content = exercise.model_copy(deep=True)
    content.vocabulary_level = level
    content.passage_voice = None
    data = content.model_dump(mode='json')
    data['passage_voice'] = {'accent': 'default', 'provider': 'default', 'voice_id': 'default', 'fallback': False}
    difficulty = LearnerProfile().difficulty.model_dump()
    difficulty['vocabulary_level'] = level
    with repo.transaction() as db:
        if kind == 'lesson':
            db.execute(s.lessons.insert().values(id=entry_id, user_id=repo.learner_id, status=status, revision=0,
                started_at=at or dt('2026-10-09T00:00:00Z'), data={'completed_at': at.isoformat() if at and status == 'completed' else None}))
        for i in range(count):
            exercise_id, session_id, attempt_id = str(uuid4()), entry_id if kind == 'earlier_practice' else str(uuid4()), str(uuid4())
            exercises.append(exercise_id)
            db.execute(s.sessions.insert().values(id=session_id, user_id=repo.learner_id, status='completed',
                started_at=dt('2026-10-01T00:00:00Z'), completed_at=at or dt('2026-10-10T12:00:00Z'), starting_difficulty=difficulty))
            db.execute(s.exercises.insert().values(id=exercise_id, user_id=repo.learner_id, session_id=session_id,
                content=data if metadata else {'script': data['script']}, difficulty=difficulty,
                audio_name=exercise_id + '.wav', created_at=dt('2026-10-01T00:00:00Z')))
            if kind == 'lesson':
                db.execute(s.lesson_exercises.insert().values(exercise_id=exercise_id, lesson_id=entry_id, user_id=repo.learner_id))
            db.execute(s.attempts.insert().values(id=attempt_id, exercise_id=exercise_id, transcription={'text': 'She change meeting Monday'},
                recognized_text='She change meeting Monday', confidence=.99, transcription_source='openai',
                confirmed_text='She change meeting Monday', status='evaluated', created_at=dt('2026-10-01T00:00:00Z')))
            units = [{'dimension': dim, 'index': j, 'status': (outcomes or {}).get((dim, j), 'demonstrated'),
                'evidence': '' if (outcomes or {}).get((dim, j)) == 'insufficient_evidence' else 'She change meeting Monday',
                'question': 'What happened?' if (outcomes or {}).get((dim, j)) == 'insufficient_evidence' else ''}
                for dim, labels in expected_units(content).items() for j in range(len(labels))]
            result = final_evaluation(content, EvidenceAssessment(units=units, feedback='Saved assessment feedback.', transcription_concern=False, concern_reason=''))
            values = evaluation_values(result)
            if version == 'v0.1':
                values['data'].pop('version')
                values['version'] = 'v0.1'
                for unit in values['data']['units']:
                    unit['status'] = {'demonstrated': 'understood', 'partially_demonstrated': 'partial', 'insufficient_evidence': 'missed', 'misunderstood': 'misunderstood'}[unit['status']]
            db.execute(s.evaluations.insert().values(attempt_id=attempt_id, **values))
            if audio:
                repo.save_audio(db, exercise_id + '.wav', b'retained original bytes')
    return entry_id, exercises


@pytest.fixture
def review_client(tmp_path, repository_factory):
    repo = repository_factory()
    provider = FakeProvider()
    async def forbidden(*args, **kwargs):
        raise AssertionError('Review called a provider')
    provider.generate = provider.evaluate = provider.assess = provider.speech = provider.speak = provider.transcribe = forbidden
    app = create_app(Settings(data_dir=tmp_path / 'cache', _env_file=None), provider, repository=repo)
    return ASGIClient(app, provider), repo


def test_completed_lessons_not_passages_or_followups_and_no_writes(review_client, exercise):
    client, repo = review_client
    first, passages = seed(repo, exercise, count=3)
    seed(repo, exercise, at='2026-10-10T20:00:00Z')
    seed(repo, exercise, at='2026-10-11T00:00:00Z', kind='earlier_practice')
    unfinished, _ = seed(repo, exercise, status='paused')
    before = repo.profile()
    with repo.engine.connect() as db:
        root = db.execute(select(s.attempts).where(s.attempts.c.exercise_id == passages[0])).mappings().one()
    with repo.transaction() as db:
        db.execute(s.attempts.insert().values(id=str(uuid4()), exercise_id=passages[0], transcription={'text': 'followup'},
            recognized_text='followup', transcription_source='openai', status='transcribed', created_at=dt('2026-10-12T00:00:00Z')))
    response = client.get('/api/review?timezone=UTC')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['summary'] == {'completed_lessons': 3, 'structured_lessons': 2, 'earlier_practice': 1,
        'practice_days': 2, 'undated_lessons': 0, 'timezone_used': 'UTC', 'timezone_fallback': False, 'current_practice_level': None}
    assert next(item for item in data['lessons'] if item['id'] == first)['assessed_passages'] == 3
    assert [item['id'] for item in data['assessed_practice']] == [unfinished]
    assert repo.profile() == before
    assert 'consistently demonstrated' in data['insight']
    assert all(pattern['observations'] == 3 for pattern in data['patterns'])
    assert 'script' not in response.text and 'She change meeting Monday' not in response.text
    assert 'overall' not in response.text
    report = client.get(f'/api/review/lesson/{first}').json()
    assert len(report['passages']) == 3
    assert all(p['units'][0]['evidence'] == 'She change meeting Monday' for p in report['passages'])
    assert root['status'] == 'evaluated'


@pytest.mark.parametrize('zone,times,days', [
    ('America/Los_Angeles', ['2026-10-10T06:59:00Z', '2026-10-10T07:01:00Z'], 2),
    ('Asia/Tokyo', ['2026-10-10T14:59:00Z', '2026-10-10T15:01:00Z'], 2),
    ('America/New_York', ['2026-11-01T05:30:00Z', '2026-11-01T06:30:00Z'], 1),
    ('America/New_York', ['2026-03-08T06:59:00Z', '2026-03-08T07:01:00Z'], 1),
    ('UTC', ['2026-10-10T23:59:00Z', '2026-10-11T00:01:00Z'], 2),
])
def test_completion_local_days_and_dst(review_client, exercise, zone, times, days):
    client, repo = review_client
    for at in times:
        seed(repo, exercise, at=at)
    assert client.get('/api/review', params={'timezone': zone}).json()['summary']['practice_days'] == days


@pytest.mark.parametrize('zone', [None, 'Invalid/Timezone', '../UTC', ''])
def test_explicit_utc_fallback(review_client, zone):
    client, _ = review_client
    data = client.get('/api/review', params={'timezone': zone} if zone is not None else {}).json()
    assert data['summary']['timezone_used'] == 'UTC'
    assert data['summary']['timezone_fallback'] is True


def test_missing_dates_metadata_results_and_empty_lessons_are_retained(review_client, exercise):
    client, repo = review_client
    undated, _ = seed(repo, exercise, at=None, metadata=False)
    empty, _ = seed(repo, exercise, at='2026-10-12T00:00:00Z', count=0)
    missing_result, ids = seed(repo, exercise, at='2026-10-11T00:00:00Z')
    with repo.transaction() as db:
        db.execute(s.evaluations.delete().where(s.evaluations.c.attempt_id.in_(select(s.attempts.c.id).where(s.attempts.c.exercise_id == ids[0]))))
    data = client.get('/api/review').json()
    assert data['summary']['completed_lessons'] == 3
    assert data['summary']['practice_days'] == 2
    assert data['summary']['undated_lessons'] == 1
    assert [item['id'] for item in data['lessons']] == [empty, missing_result, undated]
    for entry_id in [undated, missing_result]:
        report = client.get(f'/api/review/lesson/{entry_id}').json()
        assert report['lesson']['takeaway'] == 'Detailed assessment unavailable for this lesson.'
        assert report['passages'][0]['units'] == []
        assert report['passages'][0]['assessment_available'] is False
    assert client.get(f'/api/review/lesson/{empty}').json()['passages'] == []


def test_report_preserves_distinct_outcomes_and_does_not_correct_unknown(review_client, exercise):
    client, repo = review_client
    entry_id, _ = seed(repo, exercise, outcomes={('details', 0): 'partially_demonstrated',
        ('details', 1): 'misunderstood', ('inference', 0): 'insufficient_evidence'})
    report = client.get(f'/api/review/lesson/{entry_id}').json()['passages'][0]
    units = {(u['dimension'], u['index']): u for u in report['units']}
    assert units['details', 0]['status'] == 'partial'
    assert units['details', 1]['status'] == 'misunderstood'
    assert units['inference', 0]['status'] == 'not_demonstrated'
    assert units['inference', 0]['evidence'] is None
    assert 'supporting details' in report['next_focus']
    entry_id, _ = seed(repo, exercise, outcomes={('inference', 0): 'insufficient_evidence'})
    report = client.get(f'/api/review/lesson/{entry_id}').json()['passages'][0]
    assert 'more evidence' in report['next_focus']


def test_legacy_units_are_reportable_but_never_numeric_trends_or_patterns(review_client, exercise):
    client, repo = review_client
    ids = []
    for day in (10, 11, 12):
        ids.append(seed(repo, exercise, at=f'2026-10-{day}T12:00:00Z', version='v0.1',
            outcomes={('details', 0): 'insufficient_evidence', ('details', 1): 'partially_demonstrated'})[0])
    response = client.get('/api/review')
    assert all(p['label'] == 'Not enough evidence' for p in response.json()['patterns'])
    assert 'overall' not in response.text and 'score' not in response.text
    report = client.get(f'/api/review/lesson/{ids[0]}').json()['passages'][0]
    assert any(u['status'] == 'partial' for u in report['units'])
    assert any(u['status'] == 'not_demonstrated' for u in report['units'])


@pytest.mark.parametrize('count,days,label', [(2, 2, 'Not enough evidence'), (3, 1, 'Not enough evidence'), (3, 2, 'Consistent strength')])
def test_pattern_repetition_threshold_is_lessons_not_passages(review_client, exercise, count, days, label):
    client, repo = review_client
    for i in range(count):
        seed(repo, exercise, at=f'2026-10-{10 + i % days}T12:00:00Z', count=3)
    patterns = client.get('/api/review').json()['patterns']
    assert all(p['label'] == label for p in patterns)
    if label != 'Not enough evidence':
        assert all(p['observations'] == count for p in patterns)


@pytest.mark.parametrize('outcomes,expected', [
    (['demonstrated', 'demonstrated', 'partially_demonstrated'], 'Developing'),
    (['partially_demonstrated', 'demonstrated', 'misunderstood'], 'Needs more practice'),
    (['insufficient_evidence', 'demonstrated', 'demonstrated'], 'Not enough evidence'),
])
def test_pattern_outcomes_and_unknown_exclusion(review_client, exercise, outcomes, expected):
    client, repo = review_client
    for i, outcome in enumerate(outcomes):
        seed(repo, exercise, at=f'2026-10-{10 + i}T12:00:00Z', outcomes={('details', 0): outcome})
    patterns = {p['dimension']: p for p in client.get('/api/review').json()['patterns']}
    assert patterns['details']['label'] == expected
    assert patterns['main_idea']['label'] == 'Consistent strength'


def test_different_difficulties_or_missing_routing_never_pool(review_client, exercise):
    client, repo = review_client
    for i, level in enumerate(['A2', 'B1', 'B2']):
        seed(repo, exercise, at=f'2026-10-{10 + i}T12:00:00Z', level=level)
    assert all(p['label'] == 'Not enough evidence' for p in client.get('/api/review').json()['patterns'])
    with repo.transaction() as db:
        for row in db.execute(select(s.exercises)).mappings().all():
            content = dict(row['content']); content.pop('passage_voice')
            db.execute(update(s.exercises).where(s.exercises.c.id == row['id']).values(content=content))
    assert all(p['label'] == 'Not enough evidence' for p in client.get('/api/review').json()['patterns'])


def test_cursor_order_counts_all_pages_and_insert_is_not_duplicated(review_client, exercise):
    client, repo = review_client
    for day in range(10, 15):
        seed(repo, exercise, at=f'2026-10-{day}T12:00:00Z')
    first = client.get('/api/review?limit=2').json()
    seed(repo, exercise, at='2026-10-20T12:00:00Z')
    second = client.get('/api/review', params={'limit': 2, 'cursor': first['next_cursor']}).json()
    assert first['summary']['completed_lessons'] == 5
    assert second['summary']['completed_lessons'] == 6
    assert not ({e['id'] for e in first['lessons']} & {e['id'] for e in second['lessons']})
    assert first['lessons'][-1]['completed_at'] > second['lessons'][0]['completed_at']
    assert client.get('/api/review?cursor=not-a-cursor').status_code == 422
    for query in ['limit=0', 'limit=31', 'timezone=' + 'x' * 101]:
        assert client.get('/api/review?' + query).status_code == 422


def test_audio_presence_and_transcript_gates_no_synthesis(review_client, exercise):
    client, repo = review_client
    missing, _ = seed(repo, exercise)
    present, ids = seed(repo, exercise, audio=True)
    assert client.get(f'/api/review/lesson/{missing}').json()['passages'][0]['audio_url'] is None
    passage = client.get(f'/api/review/lesson/{present}').json()['passages'][0]
    assert client.get(passage['audio_url']).content == b'retained original bytes'
    assert client.get(passage['transcript_url']).json()['script'] == exercise.script
    with repo.transaction() as db:
        db.execute(update(s.attempts).where(s.attempts.c.exercise_id == ids[0]).values(status='transcribed'))
    report = client.get(f'/api/review/lesson/{present}').json()
    assert report['passages'] == []
    assert client.get(passage['transcript_url']).status_code == 403


def test_cross_account_and_unauthorized_review_transcripts_audio(accounts, exercise):
    a, b, calls = accounts
    a.get('/api/profile'); b.get('/api/profile')
    owner = a.app.state.repository.for_learner(USER_A)
    entry_id, ids = seed(owner, exercise, audio=True)
    legacy, _ = seed(owner, exercise, kind='earlier_practice')
    assert a.get('/api/review').json()['summary']['completed_lessons'] == 2
    assert b.get('/api/review').json()['summary']['completed_lessons'] == 0
    for path in [f'/api/review/lesson/{entry_id}', f'/api/review/earlier_practice/{legacy}',
                 f'/api/exercises/{ids[0]}/transcript', f'/api/exercises/{ids[0]}/audio']:
        assert b.get(path).status_code == 404
        assert ASGIClient(a.app, a.provider).get(path).status_code == 401
    assert ASGIClient(a.app, a.provider).get('/api/review').status_code == 401
    assert a.get(f'/api/review/lesson/{entry_id}', headers={'Authorization': 'Bearer expired'}).status_code == 401
    assert b.get('/api/review', params={'learner_id': USER_A}).json()['lessons'] == []
    assert calls == []


def test_query_count_is_constant_without_blob_reads(review_client, exercise):
    client, repo = review_client
    for day in range(1, 21):
        seed(repo, exercise, at=f'2026-10-{day:02}T12:00:00Z')
    queries = []
    def record(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)
    event.listen(repo.engine, 'before_cursor_execute', record)
    try:
        assert client.get('/api/review?limit=1').status_code == 200
        small = len(queries)
        queries.clear()
        assert client.get('/api/review?limit=30').status_code == 200
        assert len(queries) == small == 4
        assert all('audio_assets.data' not in query for query in queries)
    finally:
        event.remove(repo.engine, 'before_cursor_execute', record)


def test_invalid_historical_dates_never_become_current_time():
    for value in [None, 'no date', '2026-10-10T12:00:00', '2026-99-99T00:00:00Z']:
        assert timestamp(value) is None
    assert display_zone(None)[0].key == 'UTC'


def test_patterns_only_use_recent_final_evidence(review_client, exercise):
    client, repo = review_client
    # Older repeated successes must not override the latest ten unknown lessons.
    for day in (1, 2, 3):
        seed(repo, exercise, at=f'2026-10-{day:02}T12:00:00Z')
    for day in range(4, 14):
        seed(repo, exercise, at=f'2026-10-{day:02}T12:00:00Z', outcomes={('details', 0): 'insufficient_evidence'})
    patterns = {p['dimension']: p for p in client.get('/api/review').json()['patterns']}
    assert patterns['details']['label'] == 'Not enough evidence'
    assert patterns['main_idea']['observations'] == 10


def test_invalid_unit_reference_is_limited_not_invented(review_client, exercise):
    client, repo = review_client
    entry_id, ids = seed(repo, exercise)
    with repo.transaction() as db:
        row = db.execute(select(s.evaluations).join(s.attempts).where(s.attempts.c.exercise_id == ids[0])).mappings().one()
        data = dict(row['data']); data['units'][0]['index'] = 1000
        db.execute(update(s.evaluations).where(s.evaluations.c.attempt_id == row['attempt_id']).values(data=data))
    report = client.get(f'/api/review/lesson/{entry_id}').json()['passages'][0]
    assert report['units'] == [] and report['assessment_available'] is False
    assert 'more evidence' in report['next_focus']


def test_recorded_accent_only_and_final_followup_not_intermediate(review_client, exercise):
    client, repo = review_client
    entry_id, ids = seed(repo, exercise)
    with repo.transaction() as db:
        # An earlier checkpoint reported uncertainty; final evaluated units resolved it.
        attempt_id = db.execute(select(s.attempts.c.id).where(s.attempts.c.exercise_id == ids[0])).scalar_one()
        db.execute(s.assessments.insert().values(attempt_id=attempt_id, exercise_id=ids[0],
            data={'units': [{'dimension': 'main_idea', 'status': 'insufficient_evidence'}]}, created_at=dt('2026-10-10T00:00:00Z')))
        content = dict(db.execute(select(s.exercises.c.content).where(s.exercises.c.id == ids[0])).scalar_one())
        content.pop('passage_voice')
        db.execute(update(s.exercises).where(s.exercises.c.id == ids[0]).values(content=content))
    profile = repo.profile()
    profile.accent_preferences.accents = ['british']; profile.accent_preferences.status = 'preferred'
    repo.save_profile(profile)
    report = client.get(f'/api/review/lesson/{entry_id}').json()['passages'][0]
    assert report['accent'] is None
    assert report['units'][0]['status'] == 'demonstrated'
