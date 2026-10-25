"""AUD-89 P1 follow-up: pin the derived attempt key and prove it is compatible with every attempt that already exists.

The derived id is a persisted contract: once an attempt exists under it, a later release must derive the same id from the same
(learner, exercise, upload id), or a repeat would no longer be recognised and would create a second attempt. The known answers
below are therefore literal values, so changing the namespace or the input format fails here instead of in production."""
import itertools
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import Transcription
from app.storage import schema as s
from app.upload_idempotency import NAMESPACE, attempt_id_for
from conftest import ASGIClient, FakeProvider
from test_conversation import scripted
from test_state_only_ready import OWNER, OTHER, dump, make_repo  # noqa: F401  (fixtures and helpers)

UPLOAD = '0123456789abcdef'
ROOT = Path(__file__).resolve().parents[1]
# Computed independently with uuid.uuid5 and the literal namespace; never regenerate these from the code under test.
KNOWN = [   # literal inputs too: this contract must not move if a shared test constant is ever edited
    ('00000000-0000-4000-8000-0000000000aa', 'ex-1', UPLOAD, '456bc3b9-6889-52e0-93ec-91a6be7d2130'),
]


def test_the_namespace_and_the_known_answers_are_pinned():
    assert str(NAMESPACE) == '7d3c4b0e-5f0a-4a52-9a43-1c6f0e2b8d11'
    for learner, exercise, upload, expected in KNOWN:
        assert attempt_id_for(learner, exercise, upload) == expected
        assert str(uuid.uuid5(uuid.UUID('7d3c4b0e-5f0a-4a52-9a43-1c6f0e2b8d11'), f'{learner}|{exercise}|{upload}')) == expected


def test_the_key_is_the_same_in_every_process_whatever_the_hash_seed():
    learner, exercise, upload, expected = KNOWN[0]
    code = ("from app.upload_idempotency import attempt_id_for; "
            f"print(attempt_id_for('{learner}', '{exercise}', '{upload}'))")
    seen = {subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=ROOT,
                           env={'PYTHONHASHSEED': str(seed), 'PYTHONPATH': str(ROOT), 'PATH': ''}).stdout.strip()
            for seed in (0, 1, 12345)}
    assert seen == {expected}


def test_distinct_learners_exercises_and_uploads_never_collide_and_are_always_version_5():
    learners = [str(uuid.uuid4()) for _ in range(25)]
    exercises = [str(uuid.uuid4()) for _ in range(25)]
    uploads = [f'{n:016x}' for n in range(0, 2 ** 32, 2 ** 32 // 16)]
    seen = {}
    for key in itertools.product(learners, exercises, uploads):
        attempt = attempt_id_for(*key)
        assert attempt not in seen and uuid.UUID(attempt).version == 5
        seen[attempt] = key
    assert len(seen) == 25 * 25 * 16


def test_one_learner_cannot_insert_into_read_or_replay_anothers_attempt(make_repo, tmp_path):
    owner = make_repo('isolation')
    if not hasattr(owner, 'for_learner'):
        pytest.skip('one learner per SQLite database')
    other = owner.for_learner(OTHER)
    exercise = {}
    for name, repo in (('owner', owner), ('other', other)):
        app = create_app(Settings(data_dir=tmp_path / name, upload_idempotency=True, _env_file=None), FakeProvider(), repo)
        exercise[name] = ASGIClient(app, FakeProvider()).post('/api/exercises').json()['id']
    mine, theirs = attempt_id_for(OWNER, exercise['owner'], UPLOAD), attempt_id_for(OTHER, exercise['other'], UPLOAD)
    assert mine != theirs
    text = Transcription(text='I heard something about an update.', confidence=.99)
    assert owner.add_attempt_once(exercise['owner'], text, mine) == (mine, True)
    for key in (mine, attempt_id_for(OTHER, exercise['owner'], UPLOAD)):
        with pytest.raises(ValueError):                               # not their exercise: nothing inserted
            other.add_attempt_once(exercise['owner'], text, key)
    assert other.attempt(mine) is None and owner.attempt(mine) is not None   # and not readable by them
    assert other.add_attempt_once(exercise['other'], text, theirs) == (theirs, True)
    with owner.engine.connect() as db:
        assert db.execute(select(func.count()).select_from(s.attempts)).scalar_one() == 2


def upload(client, exercise, audio, headers=None):
    return client.post(f'/api/exercises/{exercise}/attempts', files={'audio': ('response.wav', audio, 'audio/wav')}, headers=headers or {})


def test_attempts_that_already_exist_are_untouched_and_remain_assessable_with_the_flag_on(make_repo, tmp_path, audio_bytes):
    repo = make_repo('existing')
    before_flag = ASGIClient(create_app(Settings(data_dir=tmp_path / 'a', upload_idempotency=False, _env_file=None), FakeProvider(), repo), FakeProvider())
    exercise = before_flag.post('/api/exercises').json()['id']
    before_flag.post(f'/api/exercises/{exercise}/conversation/listened')
    legacy = upload(before_flag, exercise, audio_bytes).json()      # exactly as production attempts are today: a random UUIDv4
    assert uuid.UUID(legacy['id']).version == 4
    rows = dump(repo)
    after_flag = ASGIClient(create_app(Settings(data_dir=tmp_path / 'b', upload_idempotency=True, _env_file=None), FakeProvider(), repo), FakeProvider())
    assert dump(repo) == rows                                        # switching the flag on rewrites nothing
    assert after_flag.get(f'/api/exercises/{exercise}/conversation').json()['pending_attempt']['id'] == legacy['id']
    scripted(after_flag, [{}])
    assessed = after_flag.post(f"/api/attempts/{legacy['id']}/assess", json={'text': legacy['transcription']['text'], 'confirmed': True, 'followup_id': None})
    assert assessed.status_code == 200 and repo.completed(exercise) == legacy['id']
    # A header on a completed exercise is not a way around the existing rule: with no first attempt to replay it is refused as before.
    assert upload(after_flag, exercise, audio_bytes, {'X-Audli-Upload-Id': UPLOAD}).status_code == 409
    assert upload(after_flag, exercise, audio_bytes).status_code == 409


def test_attempts_written_with_the_flag_on_are_ordinary_rows_for_a_build_without_it(make_repo, tmp_path, audio_bytes):
    """The rollback path: the flag is turned off (or the release is rolled back) with derived-id attempts already stored."""
    repo = make_repo('rollback')
    on = ASGIClient(create_app(Settings(data_dir=tmp_path / 'a', upload_idempotency=True, _env_file=None), FakeProvider(), repo), FakeProvider())
    exercise = on.post('/api/exercises').json()['id']
    on.post(f'/api/exercises/{exercise}/conversation/listened')
    derived = upload(on, exercise, audio_bytes, {'X-Audli-Upload-Id': UPLOAD}).json()
    assert derived['id'] == attempt_id_for(repo.learner_id, exercise, UPLOAD)
    off = ASGIClient(create_app(Settings(data_dir=tmp_path / 'b', upload_idempotency=False, _env_file=None), FakeProvider(), repo), FakeProvider())
    assert off.get(f'/api/exercises/{exercise}/conversation').json()['pending_attempt']['id'] == derived['id']
    scripted(off, [{}])
    done = off.post(f"/api/attempts/{derived['id']}/assess", json={'text': derived['transcription']['text'], 'confirmed': True, 'followup_id': None})
    assert done.status_code == 200 and repo.completed(exercise) == derived['id']
