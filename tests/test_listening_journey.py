"""Audli 2.0 Milestone 02: the four-exercise journey, the spoken answer's background assessment, the durable job queue and recovery.

Every AI provider here is a deterministic test double (app.listening.providers.Fake*): no network call and no paid request is made.
Runs on SQLite always and on real PostgreSQL when AUDLI_TEST_POSTGRES_URL points at a disposable cluster.
"""
import array
import io
import threading
import wave
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update, func

from app.config import Settings
from app.listening import jobs, schema as l, spoken, store
from app.listening.domain import (FillKey, SequenceKey, LessonContent, ground_assessment, normalize_text, score_fill_blank, score_sequence,
                                  sequence_positions_correct, word_count)
from app.listening.objects import LocalObjectStore, ObjectMissing, S3ObjectStore
from app.listening.providers import FakeAssessor, FakeTranscriber, ProviderPermanent, ProviderTemporary, build_providers
from app.listening.seed import load_fixture
from app.listening.worker import run, run_once
from app.main import create_app
from app.storage.repository import utcnow
from conftest import ASGIClient, FakeProvider
from test_auth import AccountClient, auth_response, settings_for
from test_listening import make_repository, published, fixture_json  # noqa: F401  (fixtures)

PEOPLE_WORDS = 'I think the restaurant had run out of mushrooms so Maria chose the chicken without onions instead'
ORDER = ['water', 'mushrooms', 'chicken', 'bread']


def tone(seconds=3.0, amplitude=9000):
    samples = array.array('h', (int(amplitude * (1 if (i // 40) % 2 else -1)) for i in range(int(16000 * seconds))))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1), out.setsampwidth(2), out.setframerate(16000), out.writeframes(samples.tobytes())
    return buffer.getvalue()


@pytest.fixture
def journey(make_repository, tmp_path):
    owner = str(uuid4())
    repo = make_repository(owner)
    from app.listening.api import prepare
    from app.listening.seed import seed
    prepare(repo.engine)
    objects = LocalObjectStore(tmp_path / 'listening_objects', b'k' * 32)
    recordings = LocalObjectStore(tmp_path / 'listening_recordings', b'r' * 32)
    version_id, _ = seed(repo.engine, objects)
    with repo.engine.connect() as db:
        lesson_id = db.execute(select(l.versions.c.lesson_id).where(l.versions.c.id == version_id)).scalar_one()
    app = create_app(Settings(data_dir=tmp_path, provider='demo', listening_enabled=True, _env_file=None), FakeProvider(), repository=repo)
    return SimpleNamespace(client=ASGIClient(app, None), repo=repo, engine=repo.engine, lesson_id=lesson_id, recordings=recordings, owner=owner,
                           app=app, tmp=tmp_path)


def begin(client, lesson_id):
    return client.post(f'/api/listening/lessons/{lesson_id}/attempts', json={'client_request_id': uuid4().hex}).json()


def by_kind(view):
    return {e['kind']: e for e in view['exercises']}


def post_answer(client, view, kind, **body):
    return client.post(f"/api/listening/attempts/{view['attempt']['id']}/answers", json={'exercise_id': by_kind(view)[kind]['id'], **body})


def upload(client, view, data=None, upload_id=None, content_type='audio/wav'):
    return client.post(f"/api/listening/attempts/{view['attempt']['id']}/exercises/{by_kind(view)['spoken']['id']}/recording",
                       params={'upload_id': upload_id or uuid4().hex}, content=data if data is not None else tone(), headers={'content-type': content_type})


def spoken_action(client, view, action):
    return client.post(f"/api/listening/attempts/{view['attempt']['id']}/exercises/{by_kind(view)['spoken']['id']}/{action}")


def refresh(client, view):
    return client.get(f"/api/listening/attempts/{view['attempt']['id']}").json()


def work(j, transcriber=None, assessor=None, now=None, owner='worker-1'):
    """Drain the queue like the worker process would. Returns the list of outcomes."""
    transcriber, assessor, outcomes = transcriber or FakeTranscriber(PEOPLE_WORDS), assessor or FakeAssessor(), []
    while (outcome := run_once(j.engine, j.recordings, transcriber, assessor, owner, now=now)) is not None:
        outcomes.append(outcome)
    return outcomes


def finish_everything_but_speech(j, view):
    assert post_answer(j.client, view, 'multiple_choice', option_id='b').status_code == 200
    assert post_answer(j.client, view, 'fill_blank', text='herbs').status_code == 200
    return post_answer(j.client, view, 'sequencing', order=ORDER).json()


def recordings_on_disk(j):
    return sorted(p for p in (j.tmp / 'listening_recordings' / 'listening-tmp').rglob('*') if p.is_file()) if (j.tmp / 'listening_recordings' / 'listening-tmp').exists() else []


# ---- domain ------------------------------------------------------------------------------------------------------------------

def test_fill_blank_scoring_ignores_case_punctuation_and_spacing_but_not_the_word():
    key = FillKey(accepted=['herbs'], explanation='x')
    assert [score_fill_blank(key, a) for a in ('herbs', ' Herbs. ', 'HERBS!', '  h e r b s') ] == [True, True, True, False]
    assert not any(score_fill_blank(key, a) for a in ('herb', 'spices', '', '   ', '...'))
    assert normalize_text('Don’t  Panic!') == "don't panic"


def test_sequence_scoring_is_exact_with_a_position_count():
    key = SequenceKey(order=['a', 'b', 'c'], explanation='x')
    assert score_sequence(key, ['a', 'b', 'c']) and not score_sequence(key, ['a', 'c', 'b']) and not score_sequence(key, ['b', 'a'])
    assert sequence_positions_correct(key, ['a', 'c', 'b']) == 1


def test_grounding_demands_a_real_quote_and_otherwise_says_unclear():
    transcript = 'She wanted the pasta but they had no mushrooms'
    raw = [{'id': 'a', 'state': 'covered', 'quote': 'no mushrooms'}, {'id': 'b', 'state': 'covered', 'quote': 'she loves chicken'},
           {'id': 'c', 'state': 'partly', 'quote': 'mush'}, {'id': 'd', 'state': 'missing', 'quote': 'ignored'}]
    result = ground_assessment(raw, ['a', 'b', 'c', 'd'], transcript)
    assert [(v['state'], v['quote']) for v in result] == [('covered', 'no mushrooms'), ('unclear', None), ('unclear', None), ('missing', None)]
    for bad in ([], raw[:3], [{'id': 'x', 'state': 'covered', 'quote': 'q'}] * 4, [{**r, 'state': 'excellent'} for r in raw]):
        with pytest.raises(ValueError):
            ground_assessment(bad, ['a', 'b', 'c', 'd'], transcript)


def test_content_rules_for_the_new_exercises():
    data = fixture_json()
    full = {**data, **{k: v for k, v in __import__('json').load(open('app/listening/fixtures/restaurant_b1/lesson.json')).items() if k in
                       ('spoken', 'fill_blank', 'fill_key', 'sequencing', 'sequence_key')}}
    assert LessonContent.model_validate(full).spoken is not None
    broken = [{'fill_blank': {'prompt': 'No gap here'}}, {'fill_blank': {'prompt': 'Two ___ gaps ___'}}, {'fill_key': None},
              {'sequence_key': {'order': ['chicken', 'bread', 'water', 'mushrooms'], 'explanation': 'x'}},      # identical to the first view
              {'sequence_key': {'order': ['water', 'mushrooms', 'chicken'], 'explanation': 'x'}}, {'spoken': {'prompt': 'q', 'points': []}}]
    for change in broken:
        with pytest.raises(Exception):
            LessonContent.model_validate({**full, **change})


def test_backoff_grows_and_is_capped():
    assert [jobs.backoff_seconds(n) for n in (1, 2, 3, 4, 9)] == [15, 30, 60, 120, 600]


# ---- the complete journey ------------------------------------------------------------------------------------------------------

def test_published_lesson_has_four_exercises_in_order_and_milestone_01_lessons_still_work(journey):
    view = begin(journey.client, journey.lesson_id)
    assert [(e['position'], e['kind']) for e in view['exercises']] == [(1, 'multiple_choice'), (2, 'spoken'), (3, 'fill_blank'), (4, 'sequencing')]
    assert view['attempt']['total'] == 4 and view['attempt']['scored'] == 3
    assert by_kind(view)['sequencing']['options'] and not by_kind(view)['fill_blank']['options'] and not by_kind(view)['spoken']['options']


def test_the_learner_finishes_while_the_spoken_answer_is_still_pending_then_it_is_assessed_in_the_background(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    after = finish_everything_but_speech(j, view)
    assert after['attempt']['status'] == 'in_progress'                    # three of four decided
    sent = upload(j.client, view)
    assert sent.status_code == 200, sent.text
    now = sent.json()
    # The learner is not waiting for any AI: the attempt is already complete and the spoken answer is pending.
    assert now['attempt']['status'] == 'completed' and now['attempt']['correct'] == 3 and now['attempt']['scored'] == 3
    assert by_kind(now)['spoken']['spoken']['status'] == 'pending' and by_kind(now)['spoken']['spoken']['points'] is None
    assert len(recordings_on_disk(j)) == 1                                # held privately until the worker has transcribed it
    assert work(j) == ['done']
    final = refresh(j.client, view)
    result = by_kind(final)['spoken']['spoken']
    assert result['status'] == 'assessed' and result['transcript'] == PEOPLE_WORDS and not result['insufficient']
    assert [p['id'] for p in result['points']] == ['first', 'reason', 'final'] and result['total_points'] == 3
    assert all(p['quote'] is None or normalize_text(p['quote']) in normalize_text(PEOPLE_WORDS) for p in result['points'])
    assert final['attempt']['status'] == 'completed' and final['attempt']['correct'] == 3      # the finished attempt never changes
    assert recordings_on_disk(j) == []                                     # deleted as soon as the words were saved
    with j.engine.connect() as db:
        assert db.execute(select(l.spoken.c.recording_key)).scalar_one() is None


def test_each_exercise_reveals_its_key_only_after_that_exercise_is_answered(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    text = j.client.get(f"/api/listening/attempts/{view['attempt']['id']}").text
    for secret in ('accepted', 'correct_order', 'positions_correct', 'correct_option_id', 'explanation', 'listening-tmp', 'recording_key',
                   'She ordered the chicken instead', 'The restaurant had run out of mushrooms', 'sequence_key', 'rubric'):
        assert secret not in text, secret
    wrong = post_answer(j.client, view, 'fill_blank', text='Spices').json()
    fill = by_kind(wrong)['fill_blank']['fill']
    assert fill == {'answer': 'Spices', 'is_correct': False, 'accepted': 'herbs', 'explanation': 'Sam says the chicken comes with lemon and fresh herbs.'}
    assert by_kind(wrong)['sequencing']['sequence'] is None and by_kind(wrong)['multiple_choice']['feedback'] is None
    seq = by_kind(post_answer(j.client, view, 'sequencing', order=['chicken', 'bread', 'water', 'mushrooms']).json())['sequencing']['sequence']
    assert seq['is_correct'] is False and seq['correct_order'] == ORDER and seq['positions_correct'] == 0


def test_fill_blank_is_deterministic_idempotent_and_final(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    first = post_answer(j.client, view, 'fill_blank', text='  Herbs.  ')
    assert by_kind(first.json())['fill_blank']['fill']['is_correct'] is True and by_kind(first.json())['fill_blank']['fill']['answer'] == 'Herbs.'
    assert post_answer(j.client, view, 'fill_blank', text='herbs').status_code == 200          # same answer after normalising: a replay
    assert post_answer(j.client, view, 'fill_blank', text='spices').status_code == 409         # a different second answer is refused
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.answers)).scalar_one() == 1


@pytest.mark.parametrize('kind,body', [
    ('fill_blank', {'option_id': 'a'}), ('fill_blank', {'text': ''}), ('fill_blank', {'text': 'x' * 81}), ('fill_blank', {'text': 'a', 'order': ['a', 'b', 'c']}),
    ('sequencing', {'order': ['water', 'mushrooms', 'chicken']}), ('sequencing', {'order': ['water', 'water', 'chicken', 'bread']}),
    ('sequencing', {'order': ['water', 'mushrooms', 'chicken', 'nope']}), ('sequencing', {'text': 'water'}), ('sequencing', {'order': ['A', 'b', 'c']}),
    ('multiple_choice', {'text': 'b'}), ('spoken', {'text': 'my answer'}), ('spoken', {'option_id': 'a'})])
def test_malformed_answers_change_nothing(journey, kind, body):
    j = journey
    view = begin(j.client, j.lesson_id)
    assert post_answer(j.client, view, kind, **body).status_code in (409, 422)
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.answers)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(l.responses)).scalar_one() == 0
        assert db.execute(select(func.count()).select_from(l.spoken)).scalar_one() == 0


def test_skipping_the_spoken_answer_is_recorded_honestly_and_never_scored(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    finish_everything_but_speech(j, view)
    skipped = spoken_action(j.client, view, 'skip').json()
    assert skipped['attempt']['status'] == 'completed' and skipped['attempt']['correct'] == 3
    assert by_kind(skipped)['spoken']['spoken']['status'] == 'skipped' and by_kind(skipped)['spoken']['spoken']['points'] is None
    assert spoken_action(j.client, view, 'skip').status_code == 200                     # idempotent
    assert upload(j.client, view).status_code == 409                                      # a skipped question is decided
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.jobs)).scalar_one() == 0
    assert recordings_on_disk(j) == []


def test_attempt_is_recovered_at_every_stage_of_the_journey(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    assert j.client.get(f'/api/listening/lessons/{j.lesson_id}/attempts/current').json()['attempt_id'] == view['attempt']['id']
    post_answer(j.client, view, 'multiple_choice', option_id='a')
    mid = refresh(j.client, view)
    assert by_kind(mid)['multiple_choice']['feedback']['is_correct'] is False and by_kind(mid)['fill_blank']['fill'] is None
    upload(j.client, mid, upload_id='recover-one-1')
    pending = refresh(j.client, view)
    assert by_kind(pending)['spoken']['spoken']['status'] == 'pending' and pending['attempt']['status'] == 'in_progress'
    post_answer(j.client, view, 'fill_blank', text='herbs')
    post_answer(j.client, view, 'sequencing', order=ORDER)
    done = refresh(j.client, view)
    assert done['attempt']['status'] == 'completed' and done['attempt']['correct'] == 2
    work(j)
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['status'] == 'assessed'
    again = begin(j.client, j.lesson_id)                                                # a completed attempt does not block a new one
    assert again['attempt']['id'] != view['attempt']['id'] and again['attempt']['status'] == 'in_progress'


# ---- uploads ---------------------------------------------------------------------------------------------------------------

def test_uploads_are_validated_before_anything_is_stored(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    cases = [(tone(), 'text/plain', 415), (b'not audio at all' * 40, 'audio/wav', 422), (tone(0.5), 'audio/wav', 422),
             (tone(3, amplitude=0), 'audio/wav', 422), (b'', 'audio/wav', 422), (tone(), None, 415)]
    for data, content_type, status in cases:
        response = j.client.post(f"/api/listening/attempts/{view['attempt']['id']}/exercises/{by_kind(view)['spoken']['id']}/recording",
                                 params={'upload_id': uuid4().hex}, content=data, headers={'content-type': content_type} if content_type else {})
        assert response.status_code == status, (content_type, response.text)
    big = upload(j.client, view, data=b'RIFF' + bytes(6 * 1024 * 1024))
    assert big.status_code == 413
    assert upload(j.client, view, upload_id='short').status_code == 422                  # id format
    assert recordings_on_disk(j) == []
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.spoken)).scalar_one() == 0


def test_a_repeated_upload_is_one_answer_and_a_different_one_is_refused(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    first = upload(j.client, view, upload_id='same-upload-1')
    again = upload(j.client, view, upload_id='same-upload-1')
    assert first.status_code == again.status_code == 200 and again.json()['exercises'] == first.json()['exercises']
    assert upload(j.client, view, upload_id='other-upload-2').status_code == 409
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.jobs)).scalar_one() == 1
    assert len(recordings_on_disk(j)) == 1


def test_concurrent_identical_uploads_make_one_job(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    results = []

    def go():
        results.append(upload(j.client, view, upload_id='racing-upload-1').status_code)
    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(results) <= {200, 409} and 200 in results
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.jobs)).scalar_one() == 1


def test_a_storage_failure_keeps_the_claim_so_the_same_upload_can_resume(journey, monkeypatch):
    j = journey
    view = begin(j.client, j.lesson_id)
    real = LocalObjectStore.put
    monkeypatch.setattr(LocalObjectStore, 'put', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    failed = upload(j.client, view, upload_id='resume-upload-1')
    assert failed.status_code == 503 and 'disk' not in failed.text
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['status'] == 'receiving'      # honest: not saved yet, not queued
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.jobs)).scalar_one() == 0
    monkeypatch.setattr(LocalObjectStore, 'put', real)
    assert upload(j.client, view, upload_id='resume-upload-1').status_code == 200
    assert work(j) == ['done']


def test_the_daily_limit_protects_provider_spend(journey):
    j = journey
    limited = ASGIClient(create_app(Settings(data_dir=j.tmp, provider='demo', listening_enabled=True, listening_spoken_daily_limit=1, _env_file=None),
                                    FakeProvider(), repository=j.repo), None)
    view = begin(limited, j.lesson_id)
    assert upload(limited, view, upload_id='take-number-1').status_code == 200
    work(j, assessor=FakeAssessor(fail=ProviderPermanent('400')))                           # fails, so another recording is allowed...
    again = upload(limited, view, upload_id='take-number-2')
    assert again.status_code == 429 and 'limit' in again.json()['detail']                  # ...but today's allowance is spent
    assert recordings_on_disk(j) == []
    with j.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.jobs)).scalar_one() == 1


# ---- worker outcomes -------------------------------------------------------------------------------------------------------------

def start_pending(j, words=PEOPLE_WORDS):
    view = begin(j.client, j.lesson_id)
    assert upload(j.client, view).status_code == 200
    return view


def stored(j):
    with j.engine.connect() as db:
        return db.execute(select(l.spoken)).mappings().one(), db.execute(select(l.jobs)).mappings().one()


def test_too_little_speech_is_unknown_not_a_zero(journey):
    j = journey
    view = start_pending(j)
    assert work(j, FakeTranscriber('um okay')) == ['done']
    result = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert result['status'] == 'assessed' and result['insufficient'] is True and result['points'] is None and result['covered'] is None
    assert result['transcript'] == 'um okay'


def test_a_model_cannot_claim_understanding_without_evidence(journey):
    j = journey
    view = start_pending(j)
    lie = [{'id': 'first', 'state': 'covered', 'quote': 'she ordered the lasagna'}, {'id': 'reason', 'state': 'covered', 'quote': 'run out of mushrooms'},
           {'id': 'final', 'state': 'missing', 'quote': None}]
    assert work(j, assessor=FakeAssessor(raw=lie)) == ['done']
    points = by_kind(refresh(j.client, view))['spoken']['spoken']['points']
    assert [(p['state'], p['quote']) for p in points] == [('unclear', None), ('covered', 'run out of mushrooms'), ('missing', None)]
    assert [p['text'] for p in points][1] == 'The restaurant had run out of mushrooms'


def test_temporary_provider_trouble_retries_with_backoff_then_succeeds(journey):
    j = journey
    view = start_pending(j)
    t0 = utcnow()
    transcriber = FakeTranscriber([ProviderTemporary('429'), ProviderTemporary('503'), PEOPLE_WORDS])
    assert work(j, transcriber, now=t0) == ['retry']
    assert work(j, transcriber, now=t0 + timedelta(seconds=5)) == []                            # not before the backoff
    assert work(j, transcriber, now=t0 + timedelta(seconds=20)) == ['retry']
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['status'] == 'pending'
    assert len(recordings_on_disk(j)) == 1                                                     # still needed for the next try
    assert work(j, transcriber, now=t0 + timedelta(minutes=2)) == ['done']
    row, job = stored(j)
    assert row['status'] == 'assessed' and job['status'] == 'succeeded' and job['attempts'] == 3 and recordings_on_disk(j) == []


def test_exhausted_retries_fail_visibly_delete_the_audio_and_allow_recording_again(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    finish_everything_but_speech(j, view)
    upload(j.client, view, upload_id='first-take-123')
    t0, outcomes = utcnow(), []
    broken = FakeTranscriber(ProviderTemporary('503'))
    for n in range(6):
        outcomes += work(j, broken, now=t0 + timedelta(hours=n))
    assert outcomes == ['retry', 'retry', 'retry', 'failed']
    failed = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert failed['status'] == 'failed' and failed['failure_code'] == 'provider_unavailable' and failed['transcript'] is None and failed['retryable']
    assert recordings_on_disk(j) == []
    assert spoken_action(j.client, view, 'retry').status_code == 422                          # no saved words: record again instead
    assert upload(j.client, view, upload_id='second-take-12').status_code == 200
    assert work(j, FakeTranscriber(PEOPLE_WORDS), now=t0 + timedelta(days=1)) == ['done']
    final = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert final['status'] == 'assessed' and refresh(j.client, view)['attempt']['correct'] == 3


def test_assessment_failure_keeps_the_learners_words_and_can_be_retried_without_audio(journey):
    j = journey
    view = start_pending(j)
    t0, out = utcnow(), []
    for n in range(6):
        out += work(j, assessor=FakeAssessor(fail=ProviderPermanent('400')), now=t0 + timedelta(hours=n))
    assert out == ['failed']                                                                    # a permanent refusal does not retry
    failed = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert failed['status'] == 'failed' and failed['transcript'] == PEOPLE_WORDS and failed['retryable'] and recordings_on_disk(j) == []
    retried = spoken_action(j.client, view, 'retry')
    assert retried.status_code == 200 and by_kind(retried.json())['spoken']['spoken']['status'] == 'pending'
    assert work(j, FakeTranscriber(ProviderPermanent('must not be called')), now=t0 + timedelta(days=1)) == ['done']
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['status'] == 'assessed'


def test_tries_are_capped(journey):
    j = journey
    view = start_pending(j)
    t0 = utcnow()
    for n in range(3):
        work(j, assessor=FakeAssessor(fail=ProviderPermanent('400')), now=t0 + timedelta(days=n))
        response = spoken_action(j.client, view, 'retry')
        if n < 2:
            assert response.status_code == 200
    assert response.status_code == 409
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['retryable'] is False


def test_a_missing_recording_fails_cleanly(journey):
    j = journey
    view = start_pending(j)
    for path in recordings_on_disk(j):
        path.unlink()
    assert work(j) == ['failed']
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['failure_code'] == 'recording_missing'


# ---- job queue: leases, fencing, concurrency ----------------------------------------------------------------------------------

def make_job(j, key, user=None):
    with j.engine.begin() as db:
        return jobs.enqueue(db, user or j.owner, 'spoken', key, {'n': key})


def test_enqueue_is_idempotent_and_claims_hand_each_job_to_exactly_one_worker(journey):
    j = journey
    begin(j.client, j.lesson_id)
    ids = {make_job(j, f'k{n}') for n in range(6)}
    assert len(ids) == 6 and make_job(j, 'k0') in ids
    claimed, lock = [], threading.Lock()

    def worker(name):
        while True:
            with j.engine.begin() as db:
                job = jobs.claim(db, name)
            if job is None:
                return
            with lock:
                claimed.append((job.id, name, job.fence))
    threads = [threading.Thread(target=worker, args=(f'w{n}',)) for n in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(i for i, _, _ in claimed) == sorted(ids) and all(f == 1 for _, _, f in claimed)    # once each, first fence


def test_a_worker_that_lost_its_lease_cannot_overwrite_the_one_that_took_over(journey):
    j = journey
    make_job(j, 'fenced-1')
    t0 = utcnow()
    with j.engine.begin() as db:
        slow = jobs.claim(db, 'slow', lease_seconds=30, now=t0)
    assert slow.fence == 1
    with j.engine.begin() as db:
        assert jobs.claim(db, 'rival', now=t0 + timedelta(seconds=10)) is None              # lease still held
    with j.engine.begin() as db:
        fast = jobs.claim(db, 'fast', now=t0 + timedelta(seconds=45))                      # expired: taken over
    assert fast.fence == 2 and fast.id == slow.id
    for stale_write in (lambda d: jobs.succeed(d, slow), lambda d: jobs.heartbeat(d, slow), lambda d: jobs.assert_current(d, slow),
                        lambda d: jobs.fail(d, slow, 'late'), lambda d: jobs.retry_later(d, slow, 'late')):
        with pytest.raises(jobs.LeaseLost):
            with j.engine.begin() as db:
                stale_write(db)
    with j.engine.begin() as db:
        jobs.succeed(db, fast)                                                             # the rightful holder finishes
    with j.engine.connect() as db:
        row = db.execute(select(l.jobs)).mappings().one()
    assert row['status'] == 'succeeded' and row['fence'] == 2 and row['attempts'] == 2


def test_a_zombie_worker_cannot_overwrite_a_finished_assessment(journey):
    j = journey
    view = start_pending(j)
    t0 = utcnow()
    with j.engine.begin() as db:
        zombie = jobs.claim(db, 'zombie', lease_seconds=10, now=t0)
    # The zombie stalls; a healthy worker takes over after the lease and finishes the work.
    assert run_once(j.engine, j.recordings, FakeTranscriber(PEOPLE_WORDS), FakeAssessor(), 'healthy', now=t0 + timedelta(minutes=5)) == 'done'
    good = by_kind(refresh(j.client, view))['spoken']['spoken']
    # The zombie wakes up and tries to finish with different content: its fence is stale, so nothing changes.
    outcome = spoken.process(j.engine, zombie, j.recordings, FakeTranscriber('totally different words here now'), FakeAssessor(), now=t0 + timedelta(minutes=6))
    assert outcome == 'lost'
    assert by_kind(refresh(j.client, view))['spoken']['spoken'] == good


def test_jobs_that_run_out_of_tries_through_expired_leases_are_settled_by_maintenance(journey):
    j = journey
    view = start_pending(j)
    t0 = utcnow()
    for n in range(4):                                                                       # four claims that never report back
        with j.engine.begin() as db:
            assert jobs.claim(db, f'crash{n}', lease_seconds=10, now=t0 + timedelta(minutes=n)) is not None
    with j.engine.begin() as db:
        assert jobs.claim(db, 'late', now=t0 + timedelta(minutes=30)) is None             # nothing is claimable any more
    counts = spoken.maintenance(j.engine, j.recordings, now=t0 + timedelta(minutes=30))
    assert counts['reaped'] == 1 and counts['deleted'] == 1
    result = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert result['status'] == 'failed' and result['failure_code'] == 'lease_expired' and recordings_on_disk(j) == []
    assert spoken.maintenance(j.engine, j.recordings, now=t0 + timedelta(minutes=31)) == {'reaped': 0, 'abandoned': 0, 'deleted': 0}   # repeatable


def test_abandoned_and_stale_recordings_are_cleaned_up(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    # An upload that was claimed but never completed.
    with j.engine.begin() as db:
        action, key, _ = spoken.claim_upload(db, j.owner, view['attempt']['id'], by_kind(view)['spoken']['id'], 'abandoned-up-1', 'wav', 'audio/wav', 30)
    j.recordings.put(key, tone(), 'audio/wav')
    assert spoken.maintenance(j.engine, j.recordings)['abandoned'] == 0                          # still within its grace period
    later = utcnow() + timedelta(minutes=11)
    counts = spoken.maintenance(j.engine, j.recordings, now=later)
    assert counts['abandoned'] == 1 and counts['deleted'] == 1 and recordings_on_disk(j) == []
    row = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert row['status'] == 'failed' and row['failure_code'] == 'upload_incomplete'
    assert upload(j.client, view, upload_id='fresh-take-123').status_code == 200            # the learner can simply record again


def test_the_worker_loop_drains_and_stops(journey):
    j = journey
    start_pending(j)
    assert run(j.engine, j.recordings, FakeTranscriber(PEOPLE_WORDS), FakeAssessor(), owner='loop', once=True) == 1


# ---- accounts ----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def two(make_repository, tmp_path):
    import httpx as _httpx
    repo = make_repository(None)
    app = create_app(settings_for(tmp_path, listening_enabled=True), FakeProvider(), repository=repo,
                     auth_client=_httpx.AsyncClient(transport=_httpx.MockTransport(auth_response)))
    from app.listening.api import prepare
    from app.listening.seed import seed
    prepare(repo.engine)
    objects = LocalObjectStore(tmp_path / 'listening_objects', b'k' * 32)
    recordings = LocalObjectStore(tmp_path / 'listening_recordings', b'r' * 32)
    version_id, _ = seed(repo.engine, objects)
    with repo.engine.connect() as db:
        lesson_id = db.execute(select(l.versions.c.lesson_id).where(l.versions.c.id == version_id)).scalar_one()
    return SimpleNamespace(a=AccountClient(app, None, 'valid-a'), b=AccountClient(app, None, 'valid-b'), lesson_id=lesson_id, repo=repo,
                           engine=repo.engine, recordings=recordings, tmp=tmp_path)


def test_one_account_cannot_touch_another_accounts_spoken_answer(two):
    a_view = begin(two.a, two.lesson_id)
    post_answer(two.a, a_view, 'fill_blank', text='herbs')
    assert upload(two.a, a_view).status_code == 200
    ids = f"/api/listening/attempts/{a_view['attempt']['id']}/exercises/{by_kind(a_view)['spoken']['id']}"
    for path, kwargs in ((ids + '/recording', {'params': {'upload_id': uuid4().hex}, 'content': tone(), 'headers': {'content-type': 'audio/wav'}}),
                         (ids + '/skip', {}), (ids + '/retry', {})):
        assert two.b.post(path, **kwargs).status_code == 404
    assert two.b.get(f"/api/listening/attempts/{a_view['attempt']['id']}").status_code == 404
    assert two.b.post(f"/api/listening/attempts/{a_view['attempt']['id']}/answers",
                      json={'exercise_id': by_kind(a_view)['sequencing']['id'], 'order': ORDER}).status_code == 404
    work_view = SimpleNamespace(engine=two.engine, recordings=two.recordings)
    assert work(work_view) == ['done']
    b_view = begin(two.b, two.lesson_id)
    assert by_kind(b_view)['spoken']['spoken'] is None and by_kind(b_view)['fill_blank']['fill'] is None
    assert PEOPLE_WORDS not in two.b.get(f"/api/listening/attempts/{b_view['attempt']['id']}").text
    with two.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(l.jobs)).scalar_one() == 1
    assert upload(two.b, b_view).status_code == 200                                         # B's own upload is independent of A's
    assert two.a.get(f"/api/listening/attempts/{b_view['attempt']['id']}").status_code == 404


def test_the_ids_in_a_url_must_belong_together(journey):
    j = journey
    view = begin(j.client, j.lesson_id)
    other = begin(j.client, j.lesson_id)
    assert other['attempt']['id'] == view['attempt']['id']
    mc = by_kind(view)['multiple_choice']['id']
    recording = lambda ex: j.client.post(f"/api/listening/attempts/{view['attempt']['id']}/exercises/{ex}/recording", params={'upload_id': uuid4().hex},
                                         content=tone(), headers={'content-type': 'audio/wav'})
    assert recording(mc).status_code == 404 and recording(str(uuid4())).status_code == 404 and recording('not-a-uuid').status_code == 404
    assert spoken_action(j.client, view, 'retry').status_code == 409
    assert recordings_on_disk(j) == []


# ---- providers and storage -----------------------------------------------------------------------------------------------------

def test_fake_providers_are_refused_in_production_and_chosen_by_default_in_development():
    dev = Settings(_env_file=None)
    transcriber, assessor = build_providers(dev)
    assert isinstance(transcriber, FakeTranscriber) and isinstance(assessor, FakeAssessor)
    with pytest.raises(RuntimeError):
        build_providers(SimpleNamespace(listening_provider='fake', environment='production', listening_fake_transcript=''))
    with pytest.raises(RuntimeError):
        build_providers(SimpleNamespace(listening_provider='openai', openai_api_key=None))


def test_object_stores_can_read_and_delete(tmp_path):
    local = LocalObjectStore(tmp_path, b'k' * 32)
    local.put('listening-tmp/a/b.wav', b'abc', 'audio/wav')
    assert local.get('listening-tmp/a/b.wav') == b'abc'
    local.delete('listening-tmp/a/b.wav'), local.delete('listening-tmp/a/b.wav')
    with pytest.raises(ObjectMissing):
        local.get('listening-tmp/a/b.wav')
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, 'Signature=' in request.headers['authorization']))
        return httpx.Response({'GET': 200, 'DELETE': 204}.get(request.method, 404), content=b'bytes')
    s3 = S3ObjectStore('https://s3.example.com', 'audli-audio', 'auto', 'AK', 'SK', client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert s3.get('listening-tmp/a/b.wav') == b'bytes'
    s3.delete('listening-tmp/a/b.wav')
    assert calls == [('GET', '/audli-audio/listening-tmp/a/b.wav', True), ('DELETE', '/audli-audio/listening-tmp/a/b.wav', True)]
    missing = S3ObjectStore('https://s3.example.com', 'audli-audio', 'auto', 'AK', 'SK', client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(ObjectMissing):
        missing.get('listening-tmp/x.wav')
    missing.delete('listening-tmp/x.wav')                                                  # deleting what is already gone succeeds
    with pytest.raises(httpx.HTTPStatusError):
        S3ObjectStore('https://s3.example.com', 'audli-audio', 'auto', 'AK', 'SK',
                      client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))).get('listening-tmp/x.wav')
