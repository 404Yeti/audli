"""AUD-84 P5: /conversation/ready as a state-only update (AUDLI_DB_STATE_ONLY_TRANSITIONS, default off).

Proof obligations: (1) with the flag on, the persisted state and the API responses are identical to the current
implementation for a complete lesson, on SQLite and on real PostgreSQL; (2) only the conversation row changes, and the
current implementation's rewrite of the child rows is itself a no-op, so skipping it loses nothing; (3) every unusual case
falls back to the unchanged full save; (4) the flag is off by default; (5) the statement count really drops.
"""
import hashlib
import itertools
import json
import os
import re
import threading
import uuid
from datetime import datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app
from app.models import Conversation
from app.repository import PostgresRepository, ProgressRepository
from app.storage import schema as s
from conftest import ASGIClient, FakeProvider
from lesson_flow import run_lesson
from test_persistence import BACKENDS

OWNER = '00000000-0000-4000-8000-0000000000aa'
OTHER = '00000000-0000-4000-8000-0000000000bb'
ID_MODULES = ('app.account', 'app.conversation', 'app.conversation_api', 'app.lesson_api', 'app.homework', 'app.homework_review', 'app.main',
              'app.storage.adapters', 'app.storage.repository', 'app.storage.accent_controls', 'app.storage.homework', 'app.storage.improvement')
WALL_CLOCK = re.compile(r'"(elapsed_seconds|remaining_seconds|seconds_remaining)": [0-9.]+')   # derived from the real clock on every request
TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?')


@pytest.fixture(params=BACKENDS)
def make_repo(request, tmp_path):
    """Create independent, identically initialised databases for one learner id."""
    opened, names = [], []
    admin_url = os.environ.get('AUDLI_TEST_POSTGRES_URL')

    def create(label):
        if request.param == 'postgres':
            import psycopg
            from psycopg import sql
            from scripts.migrate_postgres import migrate
            name = 'audli_test_' + uuid.uuid4().hex
            with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin:
                admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            names.append(name)
            url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
            assert migrate(Settings(persistence='postgres', database_url=url, learner_id=OWNER, _env_file=None)) is True
            repo = PostgresRepository(url, OWNER)
        else:
            repo = ProgressRepository(tmp_path / f'{label}.sqlite3', OWNER)
        opened.append(repo)
        return repo
    yield create
    for repo in opened:
        repo.close()
    if names:
        import psycopg
        from psycopg import sql
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin:
            for name in names:
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def deterministic_ids(monkeypatch):
    counter = itertools.count(1)
    generate = lambda: uuid.UUID(int=next(counter))
    for module in ID_MODULES:
        monkeypatch.setattr(f'{module}.uuid4', generate, raising=False)


def normalise(value):
    """JSON text with timestamps replaced and bytes hashed, so only meaningful content is compared."""
    def convert(item):
        if isinstance(item, (bytes, memoryview)):
            return 'sha256:' + hashlib.sha256(bytes(item)).hexdigest()
        if isinstance(item, datetime):
            return item.isoformat()
        return str(item)
    return WALL_CLOCK.sub(r'"\1": <n>', TIMESTAMP.sub('<ts>', json.dumps(value, default=convert, sort_keys=True)))


def dump(repo):
    """Every row of every table, as sorted normalised JSON strings."""
    out = {}
    with repo.engine.connect() as db:
        for table in s.metadata.sorted_tables:
            out[table.name] = sorted(normalise(dict(row)) for row in db.execute(select(table)).mappings())
    return out


def changed(before, after):
    return {name for name in before if before[name] != after[name]}


def lesson_on(repo, tmp_path, audio_bytes, flag, before_ready=None):
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return audio_bytes
    provider.speak = speak
    app = create_app(Settings(data_dir=tmp_path / f'data-{flag}', db_state_only_transitions=flag, _env_file=None), provider, repo)
    return ASGIClient(app, provider)


def drive(make_repo, tmp_path, audio_bytes, monkeypatch, flag):
    deterministic_ids(monkeypatch)
    repo = make_repo(f'flag-{flag}')
    client = lesson_on(repo, tmp_path, audio_bytes, flag)
    seen = {}
    ready = run_lesson(client, audio_bytes, before_ready=lambda: seen.update(before=dump(repo)))
    return repo, client, ready, seen['before'], dump(repo)


def test_flag_is_off_by_default():
    assert Settings(_env_file=None).db_state_only_transitions is False


def test_persistent_state_and_responses_are_identical_with_the_flag_on(make_repo, tmp_path, audio_bytes, monkeypatch):
    _, off_client, off_ready, off_before, off_after = drive(make_repo, tmp_path, audio_bytes, monkeypatch, False)
    _, on_client, on_ready, on_before, on_after = drive(make_repo, tmp_path, audio_bytes, monkeypatch, True)
    assert on_before == off_before                                 # identical lesson up to the readiness call
    assert normalise(on_ready) == normalise(off_ready)             # the readiness response
    assert on_after == off_after                                   # identical rows in every table afterwards
    exercise_id = off_ready['topic'] and next(iter(re.findall(r'"exercise_id": "([^"]+)"', ' '.join(off_after['conversations']))))
    for path in (f'/api/exercises/{exercise_id}/conversation', '/api/lessons/current', '/api/profile', '/api/history'):
        assert normalise(on_client.get(path).json()) == normalise(off_client.get(path).json()), path


def test_only_the_conversation_row_changes_and_the_old_rewrite_was_a_no_op(make_repo, tmp_path, audio_bytes, monkeypatch):
    for flag in (False, True):
        _, _, _, before, after = drive(make_repo, tmp_path, audio_bytes, monkeypatch, flag)
        assert changed(before, after) == {'conversations'}, (flag, changed(before, after))
        old, new = json.loads(before['conversations'][0]), json.loads(after['conversations'][0])
        assert old['state'] != 'READY_FOR_NEXT' and new['state'] == 'READY_FOR_NEXT'
        assert {k for k in new if new[k] != old[k]} == {'state', 'data'}     # state column and the state inside the JSON, nothing else


def conversation_with_children(client, repo, audio_bytes):
    """Run to the end of an exercise and return the stored conversation (children persisted)."""
    run_lesson(client, audio_bytes)
    exercise_id = repo.current_exercise()['id'] if repo.current_exercise() else None
    with repo.engine.connect() as db:
        return db.execute(select(s.conversations.c.exercise_id)).scalar_one()


def test_unusual_cases_use_the_unchanged_full_save(make_repo, tmp_path, audio_bytes, monkeypatch):
    repo = make_repo('fallback')
    client = lesson_on(repo, tmp_path, audio_bytes, True)
    run_lesson(client, audio_bytes)
    with repo.engine.connect() as db:
        exercise_id = db.execute(select(s.conversations.c.exercise_id)).scalar_one()
    calls = []
    original = repo.save_conversation
    monkeypatch.setattr(repo, 'save_conversation', lambda conversation: (calls.append(conversation.state), original(conversation))[1])
    stored = repo.conversation(exercise_id)

    stored.state = 'LISTENING'                                     # normal case: one UPDATE, no full save
    repo.save_conversation_state(stored)
    assert calls == [] and repo.conversation(exercise_id).state == 'LISTENING'

    pending = stored.model_copy(update={'pending_attempt_id': str(uuid.UUID(int=999999))})   # needs the attempt ownership check
    with pytest.raises(ValueError, match='Attempt not found'):
        repo.save_conversation_state(pending)
    assert calls == ['LISTENING'] and repo.conversation(exercise_id).state == 'LISTENING'

    stranger = repo.for_learner(OTHER)                             # another learner on the same database
    with pytest.raises(ValueError, match='Exercise not found for this learner'):
        stranger.save_conversation_state(stored)
    assert repo.conversation(exercise_id).state == 'LISTENING'      # nothing written for the wrong learner

    with repo.engine.begin() as db:                                # missing row: the full save re-inserts it, exactly as before
        db.execute(s.conversations.delete().where(s.conversations.c.exercise_id == exercise_id))
    assert repo.conversation(exercise_id) is None
    revived = Conversation(exercise_id=exercise_id, state='READY_FOR_NEXT')
    repo.save_conversation_state(revived)
    assert calls[-1] == 'READY_FOR_NEXT' and repo.conversation(exercise_id).state == 'READY_FOR_NEXT'


def test_handler_uses_the_new_save_only_when_enabled(make_repo, tmp_path, audio_bytes, monkeypatch):
    for flag in (False, True):
        repo = make_repo(f'spy-{flag}')
        client = lesson_on(repo, tmp_path, audio_bytes, flag)
        spy = {'full': 0, 'state': 0}
        full, state = repo.save_conversation, repo.save_conversation_state
        monkeypatch.setattr(repo, 'save_conversation', lambda c, full=full: (spy.__setitem__('full', spy['full'] + 1), full(c))[1])
        monkeypatch.setattr(repo, 'save_conversation_state', lambda c, state=state: (spy.__setitem__('state', spy['state'] + 1), state(c))[1])
        counts = {}
        run_lesson(client, audio_bytes, before_ready=lambda: counts.update(full=spy['full'], state=spy['state']))
        assert spy['state'] - counts['state'] == (1 if flag else 0)
        assert spy['full'] - counts['full'] == (0 if flag else 1)


def count_ready(make_repo, tmp_path, audio_bytes, monkeypatch, flag):
    repo = make_repo(f'count-{flag}')
    client = lesson_on(repo, tmp_path, audio_bytes, flag)
    tally = {'q': 0, 'c': 0, 'live': False}
    event.listen(repo.engine, 'before_cursor_execute', lambda *a: tally.__setitem__('q', tally['q'] + (tally['live'] and not a[2].lstrip().upper().startswith(('PRAGMA', 'BEGIN')))))
    original = repo.engine.pool.connect
    monkeypatch.setattr(repo.engine.pool, 'connect', lambda *a, **k: (tally.__setitem__('c', tally['c'] + tally['live']), original(*a, **k))[1])
    real_post = client.post
    def post(path, **kw):
        marked = path.endswith('/conversation/ready')
        tally['live'] = marked
        try:
            return real_post(path, **kw)
        finally:
            tally['live'] = False
    monkeypatch.setattr(client, 'post', post)
    run_lesson(client, audio_bytes)
    return tally['q'], tally['c']


def test_the_readiness_request_does_fewer_statements_and_the_same_number_of_checkouts(make_repo, tmp_path, audio_bytes, monkeypatch):
    off_q, off_c = count_ready(make_repo, tmp_path, audio_bytes, monkeypatch, False)
    on_q, on_c = count_ready(make_repo, tmp_path, audio_bytes, monkeypatch, True)
    assert off_q - on_q >= 14 and on_c == off_c, (off_q, on_q, off_c, on_c)


def test_concurrent_state_saves_leave_one_valid_state(make_repo, tmp_path, audio_bytes, monkeypatch):
    repo = make_repo('threads')
    client = lesson_on(repo, tmp_path, audio_bytes, True)
    run_lesson(client, audio_bytes)
    with repo.engine.connect() as db:
        exercise_id = db.execute(select(s.conversations.c.exercise_id)).scalar_one()
    errors = []

    def worker(state):
        try:
            for _ in range(15):
                conversation = repo.conversation(exercise_id)
                conversation.state = state
                (repo.save_conversation_state if state == 'LISTENING' else repo.save_conversation)(conversation)
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=worker, args=(state,)) for state in ('LISTENING', 'READY_FOR_NEXT', 'LISTENING', 'READY_FOR_NEXT')]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors and repo.conversation(exercise_id).state in ('LISTENING', 'READY_FOR_NEXT')
    after = dump(repo)
    assert len(after['conversation_turns']) >= 1 and len(after['assessment_evidence']) >= 1      # children intact after the interleaving
