"""AUD-84 P2: read-only database operations in AUTOCOMMIT (AUDLI_DB_READ_AUTOCOMMIT, default off).

Proof obligations: (1) a complete lesson leaves identical rows and gives identical responses with the flag on or off;
(2) on a READ COMMITTED server a multi-statement read sees exactly the same data in AUTOCOMMIT as inside a transaction;
(3) the feature refuses to turn on for any other isolation level; (4) the connection leaves the pool clean (no leaked
autocommit, atomic writes still atomic); (5) anything but a plain SELECT on a read connection is rejected; (6) the
driver really sends no BEGIN: after a SELECT the connection is idle, not in a transaction.
"""
import logging
import uuid

import pytest
from sqlalchemy import insert, select, text

from app.config import Settings
from app.main import create_app
from app.storage import schema as s
from conftest import FakeProvider
from lesson_flow import run_lesson
from test_persistence import repository_factory  # noqa: F401  (fixture)
from test_state_only_ready import OWNER, OTHER, changed, deterministic_ids, dump, lesson_on, make_repo, normalise  # noqa: F401


def postgres_only(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('Postgres-only: the driver transaction state and isolation level')


def test_flag_is_off_by_default():
    assert Settings.model_fields['db_read_autocommit'].default is False


def lesson(make_repo, tmp_path, audio_bytes, monkeypatch, flag):
    deterministic_ids(monkeypatch)
    repo = make_repo(f'read-{flag}')
    provider = FakeProvider()

    async def speak(text, speech_rate=.9):
        return audio_bytes
    provider.speak = speak
    profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
    from conftest import ASGIClient
    app = create_app(Settings(data_dir=tmp_path / f'd{flag}', db_read_autocommit=flag, _env_file=None), provider, repo)
    client = ASGIClient(app, provider)
    ready = run_lesson(client, audio_bytes)
    return repo, client, ready


def test_a_complete_lesson_is_identical_with_the_flag_on(make_repo, tmp_path, audio_bytes, monkeypatch):
    off_repo, off_client, off_ready = lesson(make_repo, tmp_path, audio_bytes, monkeypatch, False)
    on_repo, on_client, on_ready = lesson(make_repo, tmp_path, audio_bytes, monkeypatch, True)
    assert on_repo.read_autocommit is (on_repo.engine.dialect.name == 'postgresql') and off_repo.read_autocommit is False
    assert dump(on_repo) == dump(off_repo)
    assert normalise(on_ready) == normalise(off_ready)
    for path in ('/api/lessons/current', '/api/profile', '/api/history', '/api/plan', '/api/eligibility'):
        assert normalise(on_client.get(path).json()) == normalise(off_client.get(path).json()), path


@pytest.mark.parametrize('autocommit', [False, True])
def test_multi_statement_reads_see_the_same_data_in_both_modes(make_repo, autocommit):
    """READ COMMITTED: a second statement sees a write committed after the first, with or without a read transaction."""
    repo = make_repo(f'iso-{autocommit}')
    postgres_only(repo)
    repo.read_autocommit = autocommit
    with repo.read() as db:
        assert db.execute(text('SHOW transaction_isolation')).scalar_one() == 'read committed'
        before = db.execute(select(s.users.c.id)).all()
        with repo.engine.begin() as writer:                     # another session commits between the two statements
            writer.execute(insert(s.users).values(id=str(uuid.uuid4()), created_at=s.users.c.created_at.type.python_type.fromtimestamp(0)))
        after = db.execute(select(s.users.c.id)).all()
    assert len(after) == len(before) + 1


def test_the_feature_refuses_a_stricter_isolation_level(make_repo, tmp_path, caplog):
    repo = make_repo('strict')
    postgres_only(repo)
    with repo.engine.connect() as db:
        name = db.execute(text('SELECT current_database()')).scalar_one()
    admin = repo.engine.url.set(database='postgres')
    from sqlalchemy import create_engine
    engine = create_engine(admin, isolation_level='AUTOCOMMIT')
    with engine.connect() as db:
        db.execute(text(f'ALTER DATABASE "{name}" SET default_transaction_isolation = \'repeatable read\''))
    repo.engine.dispose()                                        # new sessions pick up the database default
    allowed, reason = repo.autocommit_reads_allowed()
    assert not allowed and 'repeatable read' in reason
    caplog.set_level(logging.INFO, logger='audli')
    app = create_app(Settings(data_dir=tmp_path / 'strict', db_read_autocommit=True, _env_file=None), FakeProvider(), repo)
    assert repo.read_autocommit is False and 'IGNORED' in caplog.text
    engine.dispose()


def test_sqlite_ignores_the_flag(tmp_path):
    from app.repository import ProgressRepository
    repo = ProgressRepository(tmp_path / 'x.sqlite3', OWNER)
    assert repo.autocommit_reads_allowed()[0] is False
    create_app(Settings(data_dir=tmp_path, db_read_autocommit=True, _env_file=None), FakeProvider(), repo)
    assert repo.read_autocommit is False
    repo.close()


def test_the_driver_sends_no_begin_and_the_pool_comes_back_clean(make_repo):
    repo = make_repo('state')
    postgres_only(repo)
    for autocommit, expected in ((False, 'INTRANS'), (True, 'IDLE')):
        repo.read_autocommit = autocommit
        with repo.read() as db:
            db.execute(text('SELECT 1'))
            raw = db.connection.dbapi_connection
            assert raw.info.transaction_status.name == expected and raw.autocommit is autocommit
        with repo.engine.connect() as db:                        # the same pooled connection, next checkout
            assert db.connection.dbapi_connection.autocommit is False
    repo.read_autocommit = True
    with repo.read():
        pass
    profile = repo.profile(); profile.name = 'Atomic'
    with pytest.raises(RuntimeError):                            # a failing write transaction is still all-or-nothing
        with repo.transaction() as db:
            db.execute(s.profiles.update().where(s.profiles.c.user_id == OWNER).values(name='Changed'))
            raise RuntimeError('boom')
    assert repo.profile().name != 'Changed'
    repo.save_profile(profile)
    assert repo.profile().name == 'Atomic'


@pytest.mark.parametrize('autocommit', [False, True])
@pytest.mark.parametrize('statement', ["INSERT INTO users (id) VALUES ('x')", "UPDATE profiles SET name = 'x'", "DELETE FROM users",
                                       "SELECT id FROM users FOR UPDATE", "SELECT id FROM users FOR SHARE", "SELECT pg_advisory_xact_lock(1)",
                                       "CREATE TABLE t (a int)"])
def test_a_read_connection_rejects_writes_and_locking_statements(make_repo, autocommit, statement):
    repo = make_repo(f'guard-{autocommit}')
    if autocommit:
        postgres_only(repo)
    repo.read_autocommit = autocommit
    with pytest.raises(Exception, match='read-only database connection|syntax|no such'):
        with repo.read() as db:
            db.execute(text(statement))


def test_scoped_repositories_inherit_the_mode(make_repo):
    repo = make_repo('scope')
    postgres_only(repo)
    repo.read_autocommit = True
    assert repo.for_learner(OTHER).read_autocommit is True
