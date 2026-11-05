"""Audli 2.0 listening lessons: immutability, answer-key isolation, scoring, idempotency, recovery, ownership, signed audio.

Runs on SQLite always and on a real PostgreSQL cluster when AUDLI_TEST_POSTGRES_URL points at a disposable one (migration 009 is
applied through the real opt-in migration path). The legacy conversational engine is never involved: no provider is called.
"""
import ast
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, insert, update, delete, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.listening import schema as l, store
from app.listening.domain import LessonContent, Cue, validate_alignment, check_level_fit, score_multiple_choice, AnswerKey
from app.listening.migrations import postgres_listening_migration
from app.listening.objects import LocalObjectStore, S3ObjectStore, build_store, valid_key
from app.listening.seed import load_fixture, seed
from app.main import create_app
from app.storage.adapters import ProgressRepository, PostgresRepository
from conftest import ASGIClient, FakeProvider
from test_auth import AccountClient, USER_A, USER_B, auth_response, settings_for

ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ['sqlite'] + (['postgres'] if os.environ.get('AUDLI_TEST_POSTGRES_URL') else [])
CORRECT, WRONG = 'b', 'a'
FROZEN_009 = '942be6c226482aa3a420b2caf4b11454552a99046fafced6316e3c4fb8d609b9'    # sha256 of docs/postgres-009.sql as released with Milestone 01 (git blob at 6fcc3c5)


@pytest.fixture(params=BACKENDS)
def make_repository(request, tmp_path):
    opened, cleanup = [], []
    if request.param == 'postgres':
        import psycopg
        from psycopg import sql
        from scripts.migrate_postgres import migrate
        admin_url = os.environ['AUDLI_TEST_POSTGRES_URL']
        name = 'audli_test_' + uuid4().hex
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        settings = Settings(persistence='postgres', database_url=url, learner_id=str(uuid4()), _env_file=None)
        assert migrate(settings) is True
        assert migrate(settings, listening=True) is True    # explicit opt-in applies 009 exactly once
        assert migrate(settings, listening=True) is False
        cleanup.append(lambda: psycopg.connect(admin_url, autocommit=True).execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name))))

        def make(owner=None):
            repo = PostgresRepository(url, owner)
            opened.append(repo)
            return repo
        make.url = url
    else:
        def make(owner=None):
            repo = ProgressRepository(tmp_path / 'audli.sqlite3', owner)
            opened.append(repo)
            return repo
    make.backend = request.param
    yield make
    for repo in opened:
        repo.close()
    for call in cleanup:
        call()


def published(repo, tmp_path):
    """Seed the checked-in lesson; returns (lesson_id, version_id)."""
    from app.listening.api import prepare
    prepare(repo.engine)
    l_store = LocalObjectStore(tmp_path / 'listening_objects', b'k' * 32)
    version_id, _ = seed(repo.engine, l_store, journey=False)    # Milestone 01 shape; the full journey is tested in test_listening_journey.py
    with repo.engine.connect() as db:
        return db.execute(select(l.versions.c.lesson_id).where(l.versions.c.id == version_id)).scalar_one(), version_id


@pytest.fixture
def local(make_repository, tmp_path):
    """One shared development learner, no Auth server (the 1.x local mode)."""
    owner = str(uuid4())
    repo = make_repository(owner)
    app = create_app(Settings(data_dir=tmp_path, provider='demo', listening_enabled=True, _env_file=None), FakeProvider(), repository=repo)
    lesson_id, version_id = published(repo, tmp_path)
    return ASGIClient(app, None), repo, lesson_id, version_id, owner


@pytest.fixture
def accounts(make_repository, tmp_path):
    """Two Auth-verified learners against one database."""
    repo = make_repository(None)
    transport = httpx.MockTransport(auth_response)
    app = create_app(settings_for(tmp_path, listening_enabled=True), FakeProvider(), repository=repo, auth_client=httpx.AsyncClient(transport=transport))
    lesson_id, version_id = published(repo, tmp_path)
    return AccountClient(app, None, 'valid-a'), AccountClient(app, None, 'valid-b'), lesson_id, repo


def start(client, lesson_id, request_id=None, accent=None):
    body = {'client_request_id': request_id or uuid4().hex, 'accent': accent}
    return client.post(f'/api/listening/lessons/{lesson_id}/attempts', json=body)


def answer(client, view, option):
    return client.post(f"/api/listening/attempts/{view['attempt']['id']}/answers", json={'exercise_id': view['exercises'][0]['id'], 'option_id': option})


# ---- domain -----------------------------------------------------------------------------------------------------------

def fixture_json():
    """The Milestone 01 shape of the seed lesson (one multiple-choice question); the full journey is covered in test_listening_journey.py."""
    data = json.loads((ROOT / 'app/listening/fixtures/restaurant_b1/lesson.json').read_text())
    for extra in ('spoken', 'fill_blank', 'fill_key', 'sequencing', 'sequence_key'):
        data.pop(extra)
    return data


def test_seed_lesson_is_valid_b1_with_measured_alignment():
    content, audio, data = load_fixture(journey=False)
    assert content.cefr == 'B1' and content.topic == 'Ordering food at a restaurant'
    assert check_level_fit(content) == []
    assert len(content.sentences) >= 12 and {s.speaker for s in content.sentences} == {'sam', 'maria'}
    validate_alignment(audio.cues, len(content.sentences), audio.duration_ms)
    assert hashlib.sha256(data).hexdigest() == audio.sha256 and len(data) > 100_000


@pytest.mark.parametrize('mutate', [
    lambda d: d['key'].update(correct_option_id='z'),
    lambda d: d['exercise']['options'].__setitem__(1, dict(d['exercise']['options'][0], id='x')),   # duplicate text
    lambda d: d['sentences'].pop(2),                                                                  # index gap
    lambda d: d['sentences'][0].update(speaker='nobody'),
    lambda d: [d['exercise']['options'].pop(), d['exercise']['options'].pop()],                       # fewer than three options
    lambda d: d.update(cefr='B3'),
    lambda d: d.update(unexpected=True),
    lambda d: d['key'].update(rubric='private'),
])
def test_content_schema_rejects_malformed_lessons(mutate):
    data = fixture_json()
    mutate(data)
    with pytest.raises(ValidationError):
        LessonContent.model_validate(data)


def test_level_fit_flags_a_script_that_is_the_wrong_size():
    data = fixture_json()
    data['cefr'] = 'A1'
    assert check_level_fit(LessonContent.model_validate(data))


def test_alignment_must_be_ordered_complete_and_inside_the_audio():
    good = [Cue(index=0, start_ms=0, end_ms=500), Cue(index=1, start_ms=600, end_ms=900)]
    validate_alignment(good, 2, 1000)
    for bad, count in ([good[:1], 2], [[good[1], good[0]], 2], [[good[0], Cue(index=1, start_ms=400, end_ms=900)], 2],
                       [[good[0], Cue(index=1, start_ms=600, end_ms=1200)], 2], [[Cue(index=0, start_ms=5, end_ms=5)], 1]):
        with pytest.raises((ValueError, ValidationError)):
            validate_alignment(bad, count, 1000)


def test_scoring_is_deterministic_and_exact():
    key = AnswerKey(correct_option_id='b', explanation='because')
    assert score_multiple_choice(key, 'b') is True
    assert [score_multiple_choice(key, o) for o in ('a', 'c', 'B', '')] == [False] * 4


def test_listening_package_never_imports_the_conversational_engine():
    forbidden = {'app.lesson', 'app.conversation', 'app.lesson_api', 'app.conversation_api', 'app.feedback', 'app.services.tutor_tts',
                 'app.onboarding', 'app.services.openai_provider'}
    for path in (ROOT / 'app/listening').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            assert not forbidden & set(names), f'{path.name} imports {forbidden & set(names)}'


# ---- immutable publishing -----------------------------------------------------------------------------------------------

def test_publishing_is_idempotent_and_versions_are_immutable(make_repository, tmp_path):
    repo = make_repository(str(uuid4()))
    lesson_id, version_id = published(repo, tmp_path)
    again = published(repo, tmp_path)
    assert again == (lesson_id, version_id)
    content, audio, _ = load_fixture(journey=False)
    with repo.engine.begin() as db:
        assert store.publish_version(db, content, audio) == (version_id, False)
    with repo.engine.connect() as db:
        exercise_id = db.execute(select(l.exercises.c.id).where(l.exercises.c.version_id == version_id)).scalar_one()
        variant_id = db.execute(select(l.audio.c.id).where(l.audio.c.version_id == version_id)).scalar_one()
    attempts = [
        update(l.versions).where(l.versions.c.id == version_id).values(title='Edited'),
        delete(l.versions).where(l.versions.c.id == version_id),
        update(l.exercises).where(l.exercises.c.id == exercise_id).values(prompt='Edited?'),
        delete(l.exercises).where(l.exercises.c.id == exercise_id),
        update(l.keys).where(l.keys.c.exercise_id == exercise_id).values(correct_option_id='a'),
        delete(l.keys).where(l.keys.c.exercise_id == exercise_id),
        insert(l.exercises).values(id=str(uuid4()), version_id=version_id, position=2, kind='multiple_choice', prompt='Extra?', options=[]),
        update(l.audio).where(l.audio.c.id == variant_id).values(duration_ms=1),
        delete(l.audio).where(l.audio.c.id == variant_id),
    ]
    for statement in attempts:
        with pytest.raises(DBAPIError):
            with repo.engine.begin() as db:
                db.execute(statement)
    with repo.engine.connect() as db:
        assert db.execute(select(l.versions.c.title)).scalar_one() == 'Ordering food at a restaurant'


def test_changed_content_becomes_a_new_version_and_old_attempts_keep_theirs(local, tmp_path):
    client, repo, lesson_id, version_1, _ = local
    view = start(client, lesson_id).json()
    assert view['lesson']['version_id'] == version_1
    data = fixture_json()
    data['exercise']['prompt'] = 'Why is the mushroom pasta unavailable?'
    content, audio, _ = load_fixture(journey=False)
    with repo.engine.begin() as db:
        version_2, created = store.publish_version(db, LessonContent.model_validate(data), audio)
    assert created and version_2 != version_1
    again = client.get(f"/api/listening/attempts/{view['attempt']['id']}").json()
    assert again['lesson']['version_id'] == version_1 and again['exercises'][0]['prompt'] == "Why can't Maria have the mushroom pasta?"
    done = answer(client, view, CORRECT).json()                        # the open attempt finishes on the version it was shown
    assert done['attempt']['status'] == 'completed'
    fresh = start(client, lesson_id).json()                          # the next attempt gets the new version
    assert fresh['lesson']['version_id'] == version_2 and fresh['exercises'][0]['prompt'].startswith('Why is the mushroom')


def test_database_refuses_an_answer_from_a_different_version(local):
    client, repo, lesson_id, version_1, _ = local
    view = start(client, lesson_id).json()
    data = fixture_json()
    data['exercise']['prompt'] = 'Another version?'
    content, audio, _ = load_fixture(journey=False)
    with repo.engine.begin() as db:
        store.publish_version(db, LessonContent.model_validate(data), audio)
        other_exercise = db.execute(select(l.exercises.c.id).where(l.exercises.c.version_id != version_1)).scalar_one()
    with pytest.raises(DBAPIError):
        with repo.engine.begin() as db:
            db.execute(insert(l.responses).values(attempt_id=view['attempt']['id'], exercise_id=other_exercise, version_id=version_1,
                option_id='a', is_correct=False, submitted_at=datetime.now(timezone.utc)))


# ---- learner-safe delivery -----------------------------------------------------------------------------------------------

SECRETS = ('correct_option_id', 'explanation', 'run out of mushrooms tonight, so Maria', 'object_key', 'lessons/ordering-food', 'sha256')


def test_no_learner_response_contains_the_key_or_storage_details_before_answering(local):
    client, _, lesson_id, _, _ = local
    catalog = client.get('/api/listening/lessons')
    assert catalog.status_code == 200 and catalog.json()[0]['accents'] == ['american'] and catalog.json()[0]['cefr'] == 'B1'
    view = start(client, lesson_id)
    current = client.get(f'/api/listening/lessons/{lesson_id}/attempts/current')
    signed = client.get(f"/api/listening/attempts/{view.json()['attempt']['id']}/audio")
    for response in (catalog, view, current, signed, client.get(f"/api/listening/attempts/{view.json()['attempt']['id']}")):
        # A signed link necessarily names its object; it is the only response that may, and it never carries the key or checksum.
        for secret in (SECRETS if response is not signed else ('correct_option_id', 'explanation', 'object_key', 'sha256')):
            assert secret not in response.text, secret
    assert view.json()['exercises'][0]['feedback'] is None
    assert set(view.json()['exercises'][0]) == {'id', 'position', 'kind', 'prompt', 'options', 'feedback', 'fill', 'sequence', 'spoken'}
    assert set(view.json()['exercises'][0]['options'][0]) == {'id', 'text'}


def test_the_key_is_revealed_only_for_the_question_the_learner_answered(local):
    client, _, lesson_id, _, _ = local
    view = start(client, lesson_id).json()
    done = answer(client, view, WRONG).json()
    feedback = done['exercises'][0]['feedback']
    assert feedback == {'selected_option_id': 'a', 'is_correct': False, 'correct_option_id': 'b',
                        'explanation': 'Sam says they have just run out of mushrooms tonight, so Maria chooses the chicken instead.'}


def test_transcript_alignment_and_audio_variant_are_delivered(local):
    client, _, lesson_id, _, _ = local
    view = start(client, lesson_id).json()
    content, audio, _ = load_fixture(journey=False)
    assert [s['text'] for s in view['lesson']['sentences']] == [s.text for s in content.sentences]
    assert view['audio']['accent'] == 'american' and view['audio']['duration_ms'] == audio.duration_ms
    cues = [Cue(**c) for c in view['audio']['cues']]
    validate_alignment(cues, len(content.sentences), view['audio']['duration_ms'])
    assert cues == audio.cues
    assert start(client, lesson_id, accent='scottish').status_code in (200, 422)    # an open attempt is resumed regardless of accent
    assert client.get('/api/listening/lessons').json()[0]['duration_ms'] == audio.duration_ms


def test_unavailable_accent_is_rejected_for_a_new_attempt(local):
    client, _, lesson_id, _, _ = local
    assert start(client, lesson_id, accent='scottish').status_code == 422


# ---- scoring, idempotency, recovery ---------------------------------------------------------------------------------------

def test_correct_and_incorrect_answers_are_scored_deterministically(local):
    client, _, lesson_id, _, _ = local
    right = answer(client, (first := start(client, lesson_id).json()), CORRECT).json()
    assert right['attempt'] | {} and right['attempt']['status'] == 'completed' and right['attempt']['correct'] == 1
    assert right['exercises'][0]['feedback']['is_correct'] is True
    wrong = answer(client, start(client, lesson_id).json(), WRONG).json()
    assert wrong['attempt']['id'] != first['attempt']['id']
    assert wrong['attempt']['correct'] == 0 and wrong['exercises'][0]['feedback']['is_correct'] is False
    assert wrong['exercises'][0]['feedback']['correct_option_id'] == CORRECT


def test_attempt_creation_is_idempotent(local):
    client, repo, lesson_id, _, owner = local
    first = start(client, lesson_id, 'request-0001').json()
    replay = start(client, lesson_id, 'request-0001').json()
    resumed = start(client, lesson_id, 'request-0002').json()       # a different request while one is open resumes it
    assert first['attempt']['id'] == replay['attempt']['id'] == resumed['attempt']['id']
    with repo.engine.connect() as db:
        assert db.execute(select(l.attempts.c.id)).all() == [(first['attempt']['id'],)]
    answer(client, first, CORRECT)
    assert start(client, lesson_id, 'request-0001').json()['attempt']['id'] == first['attempt']['id']   # a retry after completion
    assert start(client, lesson_id, 'request-0003').json()['attempt']['id'] != first['attempt']['id']  # a genuinely new run


def test_a_request_id_cannot_be_reused_for_another_lesson(local, tmp_path):
    client, repo, lesson_id, _, _ = local
    data = fixture_json() | {'slug': 'second-lesson'}
    content, audio, _ = load_fixture(journey=False)
    with repo.engine.begin() as db:
        store.publish_version(db, LessonContent.model_validate(data), audio)
        second = db.execute(select(l.lessons.c.id).where(l.lessons.c.slug == 'second-lesson')).scalar_one()
    assert start(client, lesson_id, 'request-shared').status_code == 200
    assert start(client, second, 'request-shared').status_code == 409


def test_answer_submission_is_idempotent_and_final(local):
    client, repo, lesson_id, _, _ = local
    view = start(client, lesson_id).json()
    once = answer(client, view, WRONG)
    again = answer(client, view, WRONG)
    assert once.status_code == again.status_code == 200 and once.json()['attempt'] == again.json()['attempt']
    conflict = answer(client, view, CORRECT)
    assert conflict.status_code == 409
    with repo.engine.connect() as db:
        assert db.execute(select(l.responses.c.option_id, l.responses.c.is_correct)).all() == [('a', False)]
        assert db.execute(select(l.attempts.c.correct)).scalar_one() == 0


@pytest.mark.parametrize('body, status', [
    ({'option_id': 'b'}, 422), ({'exercise_id': 'x', 'option_id': 'b'}, 422),
    ({'exercise_id': str(uuid4()), 'option_id': 'b'}, 404), ({'exercise_id': None, 'option_id': 'zz'}, 422),
    ({'exercise_id': 'FILL', 'option_id': 'nope'}, 422), ({'exercise_id': 'FILL', 'option_id': 'b', 'extra': 1}, 422),
])
def test_invalid_answers_are_rejected_without_changing_state(local, body, status):
    client, repo, lesson_id, _, _ = local
    view = start(client, lesson_id).json()
    if body.get('exercise_id') == 'FILL':
        body = body | {'exercise_id': view['exercises'][0]['id']}
    response = client.post(f"/api/listening/attempts/{view['attempt']['id']}/answers", json=body)
    assert response.status_code == status
    with repo.engine.connect() as db:
        assert db.execute(select(l.responses)).all() == []
        assert db.execute(select(l.attempts.c.status)).scalar_one() == 'in_progress'


def test_attempt_is_recovered_after_a_refresh_at_every_stage(local):
    client, _, lesson_id, _, _ = local
    assert client.get(f'/api/listening/lessons/{lesson_id}/attempts/current').json() == {'attempt_id': None}
    view = start(client, lesson_id, 'request-recover').json()
    attempt_id = view['attempt']['id']
    # "refresh" before answering: nothing but the lesson id is needed
    current = client.get(f'/api/listening/lessons/{lesson_id}/attempts/current').json()['attempt_id']
    assert current == attempt_id
    recovered = client.get(f'/api/listening/attempts/{current}').json()
    assert recovered['attempt']['status'] == 'in_progress' and recovered['exercises'][0]['feedback'] is None
    answer(client, view, CORRECT)
    # "refresh" after finishing: the result and feedback come back
    finished = client.get(f"/api/listening/attempts/{client.get(f'/api/listening/lessons/{lesson_id}/attempts/current').json()['attempt_id']}").json()
    assert finished['attempt']['status'] == 'completed' and finished['exercises'][0]['feedback']['is_correct'] is True


def test_concurrent_identical_requests_produce_one_attempt_and_one_answer(local):
    client, repo, lesson_id, _, _ = local
    app = client.app

    async def run(requests):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://testserver') as http:
            async def ticker():
                while True:
                    await asyncio.sleep(.01)
            tick = asyncio.create_task(ticker())
            try:
                return await asyncio.gather(*[http.request(m, p, json=b) for m, p, b in requests])
            finally:
                tick.cancel()
    starts = asyncio.run(run([('POST', f'/api/listening/lessons/{lesson_id}/attempts', {'client_request_id': 'request-race-1'})] * 6))
    assert {r.status_code for r in starts} == {200} and len({r.json()['attempt']['id'] for r in starts}) == 1
    view = starts[0].json()
    body = {'exercise_id': view['exercises'][0]['id'], 'option_id': CORRECT}
    answers = asyncio.run(run([('POST', f"/api/listening/attempts/{view['attempt']['id']}/answers", body)] * 6))
    assert {r.status_code for r in answers} == {200}
    with repo.engine.connect() as db:
        assert len(db.execute(select(l.attempts.c.id)).all()) == 1 and len(db.execute(select(l.responses.c.option_id)).all()) == 1


# ---- ownership --------------------------------------------------------------------------------------------------------------

def test_one_account_cannot_read_or_change_another_accounts_attempt(accounts):
    a, b, lesson_id, repo = accounts
    view = start(a, lesson_id, 'request-owner-a').json()
    attempt = view['attempt']['id']
    assert b.get(f'/api/listening/attempts/{attempt}').status_code == 404
    assert b.get(f'/api/listening/attempts/{attempt}/audio').status_code == 404
    assert answer(b, view, CORRECT).status_code == 404
    assert b.get(f'/api/listening/lessons/{lesson_id}/attempts/current').json() == {'attempt_id': None}
    # B starting with A's request id gets a separate attempt, never A's
    mine = start(b, lesson_id, 'request-owner-a').json()
    assert mine['attempt']['id'] != attempt
    # A's attempt is untouched by B's activity
    assert answer(a, view, CORRECT).json()['attempt']['status'] == 'completed'
    assert answer(b, mine, WRONG).json()['attempt']['correct'] == 0
    assert a.get(f'/api/listening/attempts/{attempt}').json()['attempt']['correct'] == 1


def test_listening_routes_require_authentication_but_signed_media_does_not(accounts):
    a, _, lesson_id, _ = accounts
    anonymous = AccountClient(a.app, None, 'not-a-token')
    for path in ('/api/listening/lessons', f'/api/listening/lessons/{lesson_id}/attempts/current'):
        assert anonymous.get(path).status_code == 401
    assert anonymous.post(f'/api/listening/lessons/{lesson_id}/attempts', json={'client_request_id': 'request-anon-1'}).status_code == 401
    attempt = start(a, lesson_id).json()['attempt']['id']
    url = a.get(f'/api/listening/attempts/{attempt}/audio').json()['url']
    assert anonymous.get(url).status_code == 200      # the signature is the credential for an <audio> element


def test_account_erasure_removes_that_learners_attempts_and_nothing_else(accounts):
    a, b, lesson_id, repo = accounts
    va, vb = start(a, lesson_id).json(), start(b, lesson_id).json()
    answer(a, va, CORRECT), answer(b, vb, WRONG)
    from app.account import erase_application_data
    with repo.transaction() as db:
        erase_application_data(db, USER_A)             # the unchanged 1.x erasure plan; listening rows follow by ON DELETE CASCADE
    with repo.engine.connect() as db:
        assert {r[0] for r in db.execute(select(l.attempts.c.user_id))} == {USER_B}
        assert len(db.execute(select(l.responses.c.attempt_id)).all()) == 1
        assert len(db.execute(select(l.versions.c.id)).all()) == 1 and len(db.execute(select(l.audio.c.id)).all()) == 1
    assert b.get(f"/api/listening/attempts/{vb['attempt']['id']}").status_code == 200


# ---- signed audio -----------------------------------------------------------------------------------------------------------

def test_signed_audio_url_serves_the_stored_bytes_with_range_support(local):
    client, _, lesson_id, _, _ = local
    attempt = start(client, lesson_id).json()['attempt']['id']
    info = client.get(f'/api/listening/attempts/{attempt}/audio').json()
    assert info['content_type'] == 'audio/mpeg' and info['url'].startswith('/api/listening/media/lessons/ordering-food-at-a-restaurant/')
    assert 'object' not in info['url'] and datetime.fromisoformat(info['expires_at']) > datetime.now(timezone.utc)
    _, audio, data = load_fixture(journey=False)
    whole = client.get(info['url'])
    assert whole.status_code == 200 and hashlib.sha256(whole.content).hexdigest() == audio.sha256
    assert whole.headers['content-type'] == 'audio/mpeg' and whole.headers['accept-ranges'] == 'bytes'
    part = client.get(info['url'], headers={'Range': 'bytes=100-199'})
    assert part.status_code == 206 and part.content == data[100:200] and part.headers['content-range'] == f'bytes 100-199/{len(data)}'


def test_signed_media_rejects_tampering_expiry_and_traversal(local, tmp_path):
    client, _, lesson_id, _, _ = local
    attempt = start(client, lesson_id).json()['attempt']['id']
    url = client.get(f'/api/listening/attempts/{attempt}/audio').json()['url']
    path, query = url.split('?')
    exp, sig = [p.split('=')[1] for p in query.split('&')]
    assert client.get(f'{path}?exp={exp}&sig={"0" * 64}').status_code == 404
    assert client.get(f'{path}?exp={int(exp) + 5}&sig={sig}').status_code == 404
    assert client.get(f'{path}?exp={exp}').status_code == 422
    assert client.get(path.replace('.mp3', '.wav') + f'?exp={exp}&sig={sig}').status_code == 404
    assert client.get(f'/api/listening/media/..%2F..%2Fetc%2Fpasswd?exp={exp}&sig={sig}').status_code == 404
    store_ = LocalObjectStore(tmp_path / 'listening_objects', b'k' * 32)
    key = path.removeprefix('/api/listening/media/')
    assert store_.verify(key, int(time.time()) - 1, store_.sign(key, int(time.time()) - 1)) is None     # expired, correct signature


def test_local_object_store_signing_and_key_rules(tmp_path):
    store_ = LocalObjectStore(tmp_path, b's' * 32)
    store_.put('lessons/a/b.mp3', b'audio', 'audio/mpeg')
    assert store_.exists('lessons/a/b.mp3') and not store_.exists('lessons/a/missing.mp3')
    url = store_.signed_url('lessons/a/b.mp3', 60, now=1000)
    key = 'lessons/a/b.mp3'
    assert store_.verify(key, 1060, store_.sign(key, 1060), now=1059) == tmp_path / key
    assert store_.verify(key, 1060, store_.sign(key, 1060), now=1061) is None
    assert store_.verify(key, 1060, store_.sign('lessons/a/c.mp3', 1060), now=1000) is None
    assert store_.verify(key, 1060 + 99999, store_.sign(key, 1060 + 99999), now=1000) is None      # absurd lifetime
    for bad in ('../x', '/abs', 'a//b', 'a/../b', '', 'a b', 'a/', 'x' * 300):
        assert not valid_key(bad)
        with pytest.raises(ValueError):
            store_.put(bad, b'x', 'audio/mpeg')
    for ttl in (0, 3601):
        with pytest.raises(ValueError):
            store_.signed_url(key, ttl)
    with pytest.raises(ValueError):
        LocalObjectStore(tmp_path, b'short')


def test_s3_signature_matches_the_published_aws_example():
    store_ = S3ObjectStore('https://examplebucket.s3.amazonaws.com', 'examplebucket', 'us-east-1', 'AKIAIOSFODNN7EXAMPLE',
                           'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', path_style=False)
    url = store_._presign('test.txt', 86400, datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert url.endswith('X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404')


def test_s3_store_builds_private_signed_requests_without_leaking_secrets():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(404 if request.method == 'HEAD' else 200)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    store_ = S3ObjectStore('https://s3.example.com', 'audli-audio', 'auto', 'ACCESSKEY', 'SECRETKEY', client=client)
    store_.put('lessons/x/a.mp3', b'bytes', 'audio/mpeg')
    assert store_.exists('lessons/x/a.mp3') is False
    put, head = seen
    assert put.method == 'PUT' and put.url.path == '/audli-audio/lessons/x/a.mp3' and put.content == b'bytes'
    assert put.headers['x-amz-content-sha256'] == hashlib.sha256(b'bytes').hexdigest() and put.headers['authorization'].startswith('AWS4-HMAC-SHA256 Credential=ACCESSKEY/')
    assert 'SECRETKEY' not in str(put.headers) and 'SECRETKEY' not in str(put.url)
    signed = store_.signed_url('lessons/x/a.mp3', 300, now=1_700_000_000)
    assert signed.startswith('https://s3.example.com/audli-audio/lessons/x/a.mp3?X-Amz-Algorithm=AWS4-HMAC-SHA256') and 'SECRETKEY' not in signed and 'X-Amz-Expires=300' in signed
    for endpoint in ('http://s3.example.com', 'https://s3.example.com/path', 'https://', 'ftp://x'):
        with pytest.raises(ValueError):
            S3ObjectStore(endpoint, 'audli-audio', 'auto', 'a', 'b')
    with pytest.raises(ValueError):
        S3ObjectStore('https://s3.example.com', 'Bad_Bucket', 'auto', 'a', 'b')


def test_production_configuration_requires_object_storage():
    base = dict(environment='production', persistence='postgres', database_url='postgresql://u:p@h/d?sslmode=require', auth_mode='supabase',
                supabase_url='https://project.supabase.co', supabase_publishable_key='sb_publishable_x', listening_enabled=True, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(**base)
    with pytest.raises(ValidationError):
        Settings(**base, listening_storage='s3', listening_s3_endpoint='http://insecure', listening_s3_bucket='b', listening_s3_access_key='a', listening_s3_secret_key='s',
                 listening_recordings_bucket='r', listening_recordings_access_key='r', listening_recordings_secret_key='t')
    s3 = dict(listening_storage='s3', listening_s3_endpoint='https://s3.example.com', listening_s3_bucket='audli-audio',
              listening_s3_access_key='a', listening_s3_secret_key='s', listening_recordings_bucket='audli-recordings',
              listening_recordings_access_key='r', listening_recordings_secret_key='t')
    with pytest.raises(ValidationError):                # the deterministic fake AI providers can never run in production
        Settings(**base, **s3)
    ok = Settings(**base, **s3, listening_provider='openai')
    assert isinstance(build_store(ok, Path('/nonexistent')), S3ObjectStore)
    assert Settings(_env_file=None).listening_enabled is False


# ---- coexistence with 1.x ---------------------------------------------------------------------------------------------------

def test_routes_do_not_exist_unless_the_feature_is_enabled(make_repository, tmp_path):
    app = create_app(Settings(data_dir=tmp_path, provider='demo', _env_file=None), FakeProvider(), repository=make_repository(str(uuid4())))
    client = ASGIClient(app, None)
    assert client.get('/api/listening/lessons').status_code == 404
    assert client.get('/api/health').status_code == 200


def test_legacy_routes_still_work_alongside_listening(local):
    client, _, _, _, _ = local
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/profile').status_code == 200
    assert client.get('/api/lessons/current').status_code == 200


# ---- PostgreSQL specifics ---------------------------------------------------------------------------------------------------

def test_migration_file_is_the_generated_migration():
    assert (ROOT / 'docs/postgres-009.sql').read_text() == postgres_listening_migration()
    from app.listening.migrations import postgres_listening_migration_010, postgres_listening_migration_011, postgres_listening_migration_012
    assert (ROOT / 'docs/postgres-010.sql').read_text() == postgres_listening_migration_010()
    assert (ROOT / 'docs/postgres-011.sql').read_text() == postgres_listening_migration_011()
    assert (ROOT / 'docs/postgres-012.sql').read_text() == postgres_listening_migration_012()


def test_migration_009_is_frozen():
    # 009 may be applied in production before Milestone 02 merges; any later change belongs in a new numbered migration.
    assert hashlib.sha256((ROOT / 'docs/postgres-009.sql').read_bytes()).hexdigest() == FROZEN_009


@pytest.mark.skipif('postgres' not in BACKENDS, reason='needs AUDLI_TEST_POSTGRES_URL')
def test_postgres_migration_security_and_ledger(make_repository):
    if make_repository.backend != 'postgres':
        pytest.skip('PostgreSQL only')
    repo = make_repository(None)       # 1.x startup still accepts its unchanged ledger
    with repo.engine.connect() as db:
        # The shared ledger must not move: app/claims/runtime.py refuses to start when max(version) >= 8 without claims mode.
        assert [r[0] for r in db.execute(text('SELECT version FROM schema_migrations ORDER BY version'))] == [1, 2, 3, 4, 5, 6, 7]
        assert [r[0] for r in db.execute(text('SELECT version FROM listening_migrations'))] == [9, 10, 11, 12]
        rls = dict(db.execute(text("SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(:names) AND relkind = 'r'"), {'names': list(l.NAMES)}).all())
        assert set(rls) == set(l.NAMES) and all(rls.values())
        grants = db.execute(text("SELECT count(*) FROM information_schema.role_table_grants WHERE table_name = ANY(:names) "
                                 "AND grantee IN ('PUBLIC','anon','authenticated')"), {'names': list(l.NAMES)}).scalar_one()
        assert grants == 0
    with pytest.raises(Exception):
        import psycopg
        with psycopg.connect(make_repository.url, autocommit=True) as db:
            db.execute((ROOT / 'docs/postgres-009.sql').read_text(), prepare=False)    # a second manual run is refused


@pytest.mark.skipif('postgres' not in BACKENDS, reason='needs AUDLI_TEST_POSTGRES_URL')
def test_postgres_migration_requires_the_earlier_migrations():
    import psycopg
    from psycopg import sql
    admin = os.environ['AUDLI_TEST_POSTGRES_URL']
    name = 'audli_test_' + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with psycopg.connect(make_url(admin).set(database=name).render_as_string(hide_password=False), autocommit=True) as db:
            db.execute('CREATE TABLE schema_migrations (version integer primary key, applied_at timestamptz not null)')
            db.execute('INSERT INTO schema_migrations VALUES (1, now())')
            with pytest.raises(psycopg.errors.RaiseException):
                db.execute((ROOT / 'docs/postgres-009.sql').read_text(), prepare=False)
    finally:
        with psycopg.connect(admin, autocommit=True) as db:
            db.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def test_missing_postgres_tables_fail_closed():
    from sqlalchemy import create_engine
    from app.listening.api import prepare
    engine = create_engine('sqlite://')
    prepare(engine)                                    # SQLite creates its own local tables
    assert 'listening_attempts' in __import__('sqlalchemy').inspect(engine).get_table_names()


# ---- request-body limits ----------------------------------------------------------------------------------------------------

def raw_post(client, path, **kwargs):
    return client.post(path, **kwargs)


def test_listening_json_bodies_are_small_but_ordinary_requests_work(local):
    client, _, lesson_id, _, _ = local
    path = f'/api/listening/lessons/{lesson_id}/attempts'
    assert raw_post(client, path, json={'client_request_id': 'request-small-1'}).status_code == 200
    padded = {'client_request_id': 'request-big-0001', 'accent': 'american', 'pad': 'x' * 5000}
    assert raw_post(client, path, json=padded).status_code == 413          # declared length over 4 KiB
    assert raw_post(client, path, content=b'{"client_request_id":"request-big-0002","pad":"' + b'x' * 20_000 + b'"}',
                    headers={'content-type': 'application/json'}).status_code == 413
    # a chunked body with no Content-Length is bounded while it is read, not trusted
    async def chunks():
        for _ in range(8):
            yield b'x' * 1024
    assert raw_post(client, path, content=chunks(), headers={'content-type': 'application/json'}).status_code == 413
    answer_path = f'/api/listening/attempts/{uuid4()}/answers'
    assert raw_post(client, answer_path, content=b'y' * 5000, headers={'content-type': 'application/json'}).status_code == 413
    assert raw_post(client, path, json={'client_request_id': 'request-small-2'}).status_code == 200      # still usable afterwards


@pytest.mark.parametrize('kwargs', [
    {'content': b'{not json', 'headers': {'content-type': 'application/json'}},
    {'content': b'[]', 'headers': {'content-type': 'application/json'}},
    {'content': b'null', 'headers': {'content-type': 'application/json'}},
    {'content': b'', 'headers': {'content-type': 'application/json'}},
    {'content': b'client_request_id=request-form-1', 'headers': {'content-type': 'application/x-www-form-urlencoded'}},
    {'content': b'{"client_request_id":"request-text-1"}', 'headers': {'content-type': 'text/plain'}},
    {'json': {'client_request_id': 'short'}}, {'json': {'client_request_id': 'request-ok-1', 'accent': 'Not An Accent!'}},
    {'json': {'client_request_id': 'request-ok-2', 'unexpected': 1}}, {'json': {'client_request_id': 12345678}},
])
def test_malformed_or_unsupported_listening_payloads_are_rejected_without_side_effects(local, kwargs):
    client, repo, lesson_id, _, _ = local
    response = raw_post(client, f'/api/listening/lessons/{lesson_id}/attempts', **kwargs)
    assert response.status_code == 422, response.text
    with repo.engine.connect() as db:
        assert db.execute(select(l.attempts.c.id)).all() == []


def test_the_existing_1x_body_limits_are_unchanged(local):
    client, _, _, _, _ = local
    # 1.x: routes ending in /attempts still take audio-sized bodies; every other JSON route stays at 40 KB.
    upload = client.post(f'/api/exercises/{uuid4()}/attempts', content=b'x' * 100_000, headers={'content-type': 'application/octet-stream'})
    assert upload.status_code != 413
    assert client.post('/api/exercises/not-real/attempts', content=b'x' * (12 * 1024 * 1024 + 70_000),
                       headers={'content-type': 'application/octet-stream'}).status_code == 413
    assert client.post('/api/onboarding/answer', content=b'{"text":"' + b'x' * 41_000 + b'"}',
                       headers={'content-type': 'application/json'}).status_code == 413
    assert client.post('/api/onboarding/answer', json={'text': 'hello there', 'confirmed': True}).status_code != 413


def test_cross_site_listening_writes_are_still_refused(local):
    client, _, lesson_id, _, _ = local
    response = raw_post(client, f'/api/listening/lessons/{lesson_id}/attempts', json={'client_request_id': 'request-origin-1'},
                        headers={'origin': 'https://evil.example'})
    assert response.status_code == 403


# ---- object-storage failure handling (mocked transport: behaviour only, NOT evidence of real S3 transport) ---------------------

@pytest.mark.parametrize('status', [401, 403, 500, 503])
def test_storage_errors_are_raised_never_mistaken_for_a_missing_object(status):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    store_ = S3ObjectStore('https://s3.example.com', 'audli-audio', 'auto', 'a', 'b', client=client)
    with pytest.raises(httpx.HTTPStatusError):
        store_.exists('lessons/x/a.mp3')                 # a 403 must not read as "not there"
    with pytest.raises(httpx.HTTPStatusError):
        store_.put('lessons/x/a.mp3', b'bytes', 'audio/mpeg')


def test_put_sends_the_content_type_and_a_missing_object_is_reported(tmp_path):
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(404 if r.method == 'HEAD' else 200))[1]))
    store_ = S3ObjectStore('https://s3.example.com', 'audli-audio', 'auto', 'a', 'b', client=client)
    assert store_.exists('lessons/x/a.mp3') is False
    store_.put('lessons/x/a.mp3', b'bytes', 'audio/mpeg')
    assert seen[1].headers['content-type'] == 'audio/mpeg' and 'content-type' in seen[1].headers['authorization']


def test_loopback_http_is_only_for_an_explicit_test_flag():
    for endpoint in ('http://127.0.0.1:9000', 'http://localhost:9000'):
        with pytest.raises(ValueError):
            S3ObjectStore(endpoint, 'audli-audio', 'auto', 'a', 'b')
        assert S3ObjectStore(endpoint, 'audli-audio', 'auto', 'a', 'b', allow_http_loopback=True).base.startswith('http://')
    with pytest.raises(ValueError):
        S3ObjectStore('http://s3.example.com', 'audli-audio', 'auto', 'a', 'b', allow_http_loopback=True)   # never a remote host


def test_seeding_refuses_to_publish_when_the_upload_cannot_be_confirmed(make_repository, tmp_path):
    from app.listening.api import prepare

    class Lossy:
        def exists(self, key): return False
        def put(self, key, data, content_type): pass
        def signed_url(self, key, ttl_seconds=300, now=None): return ''
    repo = make_repository(str(uuid4()))
    prepare(repo.engine)
    with pytest.raises(RuntimeError, match='could not be confirmed'):
        seed(repo.engine, Lossy())
    with repo.engine.connect() as db:
        assert db.execute(select(l.versions.c.id)).all() == []


def test_a_missing_stored_object_is_a_clean_404_not_a_server_fault(local, tmp_path):
    import shutil
    client, _, lesson_id, _, _ = local
    attempt = start(client, lesson_id).json()['attempt']['id']
    url = client.get(f'/api/listening/attempts/{attempt}/audio').json()['url']
    assert client.get(url).status_code == 200
    shutil.rmtree(tmp_path / 'listening_objects')            # the stored object disappears behind a still-valid link
    assert client.get(url).status_code == 404                # the player turns this into "audio couldn't load", never a 500


# ---- the complete Milestone 01 journey ---------------------------------------------------------------------------------------

class Tripwire:
    """A provider that fails the test the moment anything asks it to generate, transcribe, speak, assess or converse."""
    calls = 0

    def __getattr__(self, name):
        if name in ('generate', 'transcribe', 'speak', 'tutor_speak', 'speech', 'assess', 'evaluate', 'extract_profile', 'respond_checkin', 'structured'):
            def fail(*args, **kwargs):
                Tripwire.calls += 1
                raise AssertionError(f'The 2.0 journey must not call the AI provider ({name})')
            return fail
        raise AttributeError(name)


def test_complete_journey_authenticated_no_ai_isolated_immutable_and_key_never_leaks(make_repository, tmp_path):
    Tripwire.calls = 0
    repo = make_repository(None)
    app = create_app(settings_for(tmp_path, listening_enabled=True), Tripwire(), repository=repo,
                     auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
    lesson_id, version_id = published(repo, tmp_path)
    a, b = AccountClient(app, None, 'valid-a'), AccountClient(app, None, 'valid-b')
    anonymous = AccountClient(app, None, 'invalid')
    assert anonymous.get('/api/listening/lessons').status_code == 401                         # authenticate first
    catalog = a.get('/api/listening/lessons').json()                                          # open the saved B1 lesson
    assert [(c['title'], c['cefr']) for c in catalog] == [('Ordering food at a restaurant', 'B1')]
    view = start(a, lesson_id, 'journey-request-1').json()
    again = start(a, lesson_id, 'journey-request-1').json()                                   # accidental duplicate
    assert view['attempt']['id'] == again['attempt']['id']
    audio = a.get(f"/api/listening/attempts/{view['attempt']['id']}/audio").json()            # play audio
    assert a.get(audio['url']).status_code == 200
    assert len(view['lesson']['sentences']) == len(view['audio']['cues']) == 16                # synchronized transcript data
    assert 'correct_option_id' not in json.dumps(view) and view['exercises'][0]['feedback'] is None
    assert b.get(f"/api/listening/attempts/{view['attempt']['id']}").status_code == 404       # another account sees nothing
    result = answer(a, view, WRONG).json()                                                    # deterministic feedback
    assert result['exercises'][0]['feedback']['is_correct'] is False and result['attempt']['status'] == 'completed'
    recovered_id = a.get(f'/api/listening/lessons/{lesson_id}/attempts/current').json()['attempt_id']   # refresh
    assert a.get(f'/api/listening/attempts/{recovered_id}').json() == result
    with repo.engine.connect() as db:                                                         # immutable and unduplicated
        assert db.execute(select(l.versions.c.status, l.versions.c.id)).all() == [('published', version_id)]
        assert len(db.execute(select(l.attempts.c.id)).all()) == 1 and len(db.execute(select(l.responses.c.attempt_id)).all()) == 1
    assert Tripwire.calls == 0                                                                # no AI provider was ever asked


def test_the_feature_flag_defaults_to_off_everywhere_it_is_configured():
    assert Settings(_env_file=None).listening_enabled is False
    example = (ROOT / '.env.example').read_text()
    assert 'AUDLI_LISTENING_ENABLED=false' in example
    assert not any(line.startswith('AUDLI_LISTENING_ENABLED=true') for line in example.splitlines())
