"""Account erasure for Audli 2.0: generated lessons, private audio, attempts, progress and recordings go; other learners and the shared library stay.

Runs on SQLite and, when AUDLI_TEST_POSTGRES_URL is set (CI), on real PostgreSQL where the migration-013 guards are exercised for real.
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError

from app.account import erase_listening
from app.listening import schema as t, store
from app.listening.erasure import ErasureFailed, ErasureRefused, erase_learner_data
from app.listening.objects import LocalObjectStore
from test_auth import USER_A, USER_B
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_generation_api import SPEC, api, create  # noqa: F401  (fixtures)


class FlakyStore:
    """Deletes through to a real store but fails for chosen keys (an outage in the middle of erasure)."""
    def __init__(self, inner, fail):
        self.inner, self.fail = inner, set(fail)

    def delete(self, key):
        if key in self.fail:
            raise ConnectionError('storage unreachable')
        self.inner.delete(key)


def count(db, table, **where):
    q = select(func.count()).select_from(table)
    for column, value in where.items():
        q = q.where(table.c[column] == value)
    return db.execute(q).scalar_one()


@pytest.fixture
def world(api):
    """Learner A has a generated lesson, an attempt with an answer and a stored recording; learner B has an attempt on a shared lesson."""
    request = create(api.a).json()['id']
    api.work()
    lesson = api.a.get(f'/api/listening/generation/requests/{request}').json()['lesson_id']
    assert lesson
    shared = next(i['lesson_id'] for i in api.b.get('/api/listening/lessons').json() if i['lesson_id'] != lesson)
    a_attempt = api.a.post(f'/api/listening/lessons/{lesson}/attempts', json={'client_request_id': uuid4().hex}).json()
    api.b.post(f'/api/listening/lessons/{shared}/attempts', json={'client_request_id': uuid4().hex})
    first = a_attempt['exercises'][0]
    api.a.post(f"/api/listening/attempts/{a_attempt['attempt']['id']}/answers", json={'exercise_id': first['id'], 'option_id': first['options'][0]['id']})
    objects = LocalObjectStore(api.tmp / 'listening_objects', b'k' * 32)
    recordings = LocalObjectStore(api.tmp / 'listening_recordings', b'r' * 32)
    key = f'listening-tmp/{uuid4().hex}.wav'
    recordings.put(key, b'RIFF' + b'0' * 100, 'audio/wav')
    spoken = next(e for e in a_attempt['exercises'] if e['kind'] == 'spoken')
    with api.engine.begin() as db:
        version = db.execute(select(t.attempts.c.version_id).where(t.attempts.c.id == a_attempt['attempt']['id'])).scalar_one()
        db.execute(insert(t.spoken).values(attempt_id=a_attempt['attempt']['id'], exercise_id=spoken['id'], version_id=version, status='pending', generation=1,
                                           upload_id=uuid4().hex, recording_key=key, recording_type='audio/wav', created_at=store.utcnow(), updated_at=store.utcnow()))
    with api.engine.connect() as db:
        audio_keys = list(db.execute(select(t.audio.c.object_key).join(t.versions, t.versions.c.id == t.audio.c.version_id).where(t.versions.c.lesson_id == lesson)).scalars())
        shared_keys = list(db.execute(select(t.audio.c.object_key).join(t.versions, t.versions.c.id == t.audio.c.version_id).where(t.versions.c.lesson_id == shared)).scalars())
    return type('World', (), dict(api=api, lesson=lesson, shared=shared, objects=objects, recordings=recordings, recording_key=key,
                                 audio_keys=audio_keys, shared_keys=shared_keys, a=USER_A, b=USER_B))


def test_erasing_a_learner_removes_their_lessons_audio_attempts_and_recordings_and_nobody_elses(world):
    w = world
    assert w.audio_keys and all(w.objects.exists(k) for k in w.audio_keys) and w.recordings.exists(w.recording_key)
    with w.api.engine.connect() as db:
        b_attempts, shared_versions = count(db, t.attempts, user_id=w.b), count(db, t.versions)
    with w.api.engine.begin() as db:
        summary = erase_learner_data(db, w.a, w.objects, w.recordings)
    assert summary['lessons'] == 1 and summary['attempts'] == 1 and summary['objects'] >= len(w.audio_keys) + 1
    with w.api.engine.connect() as db:
        assert count(db, t.lessons, id=w.lesson) == 0 and count(db, t.lesson_owners, user_id=w.a) == 0
        assert count(db, t.attempts, user_id=w.a) == 0 and count(db, t.responses) == 0 and count(db, t.answers) == 0 and count(db, t.spoken) == 0
        assert count(db, t.generation_requests, user_id=w.a) == 0 and count(db, t.jobs, user_id=w.a) == 0
        assert db.execute(select(func.count()).select_from(t.versions).where(t.versions.c.lesson_id == w.lesson)).scalar_one() == 0
        assert count(db, t.attempts, user_id=w.b) == b_attempts                    # the other learner is untouched
        assert count(db, t.versions) == shared_versions - 1                        # only A's generated version went; the shared library stays
        assert count(db, t.lessons, id=w.shared) == 1
    assert not any(w.objects.exists(k) for k in w.audio_keys) and not w.recordings.exists(w.recording_key)
    assert all(w.objects.exists(k) for k in w.shared_keys)                         # shared lesson audio is never deleted


def test_a_storage_failure_rolls_everything_back_and_a_retry_finishes_the_job(world):
    w = world
    flaky = FlakyStore(w.objects, [w.audio_keys[0]])
    with pytest.raises(ErasureFailed):
        with w.api.engine.begin() as db:
            erase_learner_data(db, w.a, flaky, w.recordings)
    with w.api.engine.connect() as db:                                             # nothing was deleted from the database
        assert count(db, t.lessons, id=w.lesson) == 1 and count(db, t.attempts, user_id=w.a) == 1 and count(db, t.generation_requests, user_id=w.a) == 1
    with w.api.engine.begin() as db:                                               # the retry (store healthy again) completes
        erase_learner_data(db, w.a, w.objects, w.recordings)
    with w.api.engine.connect() as db:
        assert count(db, t.lessons, id=w.lesson) == 0
    assert not any(w.objects.exists(k) for k in w.audio_keys)


def test_a_second_run_after_success_is_a_harmless_no_op(world):
    w = world
    with w.api.engine.begin() as db:
        erase_learner_data(db, w.a, w.objects, w.recordings)
    with w.api.engine.begin() as db:
        assert erase_learner_data(db, w.a, w.objects, w.recordings) == {'lessons': 0, 'attempts': 0, 'objects': 0}


def test_another_accounts_activity_on_a_private_lesson_refuses_and_deletes_nothing(world):
    w = world
    with w.api.engine.begin() as db:
        version = db.execute(select(t.versions.c.id).where(t.versions.c.lesson_id == w.lesson)).scalar_one()
        audio = db.execute(select(t.audio.c.id).where(t.audio.c.version_id == version)).scalars().first()
        db.execute(insert(t.attempts).values(id=str(uuid4()), user_id=w.b, version_id=version, audio_variant_id=audio, client_request_id=uuid4().hex,
                                             status='in_progress', total=4, created_at=store.utcnow()))
    with pytest.raises(ErasureRefused):
        with w.api.engine.begin() as db:
            erase_learner_data(db, w.a, w.objects, w.recordings)
    with w.api.engine.connect() as db:
        assert count(db, t.lessons, id=w.lesson) == 1
    assert all(w.objects.exists(k) for k in w.audio_keys)


def test_a_recording_key_outside_the_recording_prefix_is_never_deleted(world):
    w = world
    with w.api.engine.begin() as db:
        db.execute(text("UPDATE listening_spoken SET recording_key = :k"), {'k': w.audio_keys[0]})
    with pytest.raises(ErasureRefused):
        with w.api.engine.begin() as db:
            erase_learner_data(db, w.a, w.objects, w.recordings)
    assert all(w.objects.exists(k) for k in w.audio_keys)


def test_the_account_deletion_wrapper_fails_closed(world):
    w = world
    with w.api.engine.connect() as db:
        with pytest.raises(HTTPException) as refused:                              # 2.0 data exists but no eraser is configured
            erase_listening(db, w.a, None)
        assert refused.value.status_code == 503
        erase_listening(db, str(uuid4()), None)                                    # a learner with no 2.0 data needs none
    def failing(db, owner):
        raise ErasureFailed('1 stored object(s) could not be deleted')
    with w.api.engine.connect() as db:
        with pytest.raises(HTTPException) as failed:
            erase_listening(db, w.a, failing)
        assert failed.value.status_code == 503 and 'nothing was deleted' in failed.value.detail


def test_on_postgres_the_guards_stay_strict_everywhere_except_inside_the_owners_own_erasure(world):
    w = world
    if w.api.engine.dialect.name != 'postgresql':
        pytest.skip('immutability triggers exist only on PostgreSQL')
    def attempt(owner_setting):
        with w.api.engine.begin() as db:
            if owner_setting:
                db.execute(text("SELECT set_config('audli.erasing_owner', :o, true)"), {'o': owner_setting})
            db.execute(text('DELETE FROM listening_audio_variants WHERE version_id IN (SELECT id FROM listening_lesson_versions WHERE lesson_id = :l)'), {'l': w.lesson})
    with pytest.raises(DBAPIError, match='append-only'):
        attempt(None)                                                              # no erasure: still append-only
    with pytest.raises(DBAPIError, match='append-only'):
        attempt(w.b)                                                               # another learner's id does not open A's lesson
    with w.api.engine.begin() as db:                                               # and a shared lesson cannot be opened by anyone's erasure
        db.execute(text("SELECT set_config('audli.erasing_owner', :o, true)"), {'o': w.a})
        with pytest.raises(DBAPIError, match='immutable'):
            db.execute(text('DELETE FROM listening_lesson_versions WHERE lesson_id = :l'), {'l': w.shared})
