"""The spoken-answer worker under its own least-privilege PostgreSQL role: the full pipeline works, and nothing outside its remit is reachable.
PostgreSQL only (needs AUDLI_TEST_POSTGRES_URL); the role SQL itself is also checked against the checked-in file everywhere."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.listening import schema as l
from app.listening.ai_budget import AiGuard, BudgetRefused
from app.listening.api import prepare
from app.listening.providers import FakeAssessor, FakeTranscriber
from app.listening.roles import EXAMPLE_ROLE, INSERT, SELECT, UPDATE, worker_role_sql
from app.listening.worker import run_once
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_journey import PEOPLE_WORDS, by_kind, journey, refresh, start_pending  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]


def test_the_checked_in_role_sql_is_the_generated_sql():
    assert (ROOT / 'docs/postgres-listening-worker-role.sql').read_text() == worker_role_sql(EXAMPLE_ROLE)
    sql = worker_role_sql('lw_test')
    assert 'DELETE' not in sql.upper().replace('DROP POLICY IF EXISTS', '') and 'ALL PRIVILEGES' not in sql.upper() and 'SUPERUSER' not in sql.upper()
    assert set(SELECT) >= set(UPDATE) | set(INSERT) and 'listening_exercise_keys' not in sql and 'listening_attempts' not in sql
    for bad in ('x; DROP TABLE users', 'a b', '1abc', '', 'a' * 41, 'a-b'):
        with pytest.raises(ValueError):
            worker_role_sql(bad)


def worker_engine(j, role):
    engine = create_engine(j.engine.url.render_as_string(hide_password=False))

    @event.listens_for(engine, 'connect')
    def switch(dbapi, record):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
        dbapi.commit()
    return engine


@pytest.fixture
def role(journey):
    j = journey
    if j.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')
    name = 'lw_' + uuid4().hex[:10]
    with j.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {name} NOLOGIN')
        db.exec_driver_sql(worker_role_sql(name))
    yield j, name
    with j.engine.begin() as db:
        db.exec_driver_sql(f'DROP OWNED BY {name}')
        db.exec_driver_sql(f'DROP ROLE {name}')


def test_the_whole_spoken_pipeline_runs_under_the_restricted_role(role):
    j, name = role
    view = start_pending(j)
    engine = worker_engine(j, name)
    prepare(engine, worker=True)                                           # the worker's own startup check passes...
    with pytest.raises(RuntimeError, match='lacks privileges'):
        prepare(engine)                                                    # ...and the full (API) profile correctly does not
    guard = AiGuard(engine, Settings(listening_provider='openai', OPENAI_API_KEY='sk-test-not-real', listening_ai_daily_budget_usd=1.0, _env_file=None))
    guard.reserve('assessment', 1000)                                      # the spending ledger works (select, insert a day row, update)
    assert run_once(engine, j.recordings, FakeTranscriber(PEOPLE_WORDS), FakeAssessor(), 'restricted-worker') == 'done'
    result = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert result['status'] == 'assessed' and result['transcript'] == PEOPLE_WORDS
    from app.listening import spoken
    assert spoken.maintenance(engine, j.recordings) == {'reaped': 0, 'abandoned': 0, 'deleted': 0}


@pytest.mark.parametrize('statement', [
    'SELECT * FROM users',                                                 # no learner accounts
    'SELECT * FROM listening_exercise_keys',                               # no answer keys
    'SELECT * FROM listening_attempts', 'SELECT * FROM listening_responses', 'SELECT * FROM listening_answers',
    'SELECT * FROM listening_lessons', 'SELECT * FROM listening_audio_variants',
    "INSERT INTO listening_jobs (id, user_id, kind, dedupe_key, payload, status, attempts, max_attempts, run_after, fence, created_at, updated_at) "
    "VALUES (gen_random_uuid(), gen_random_uuid(), 'spoken', 'x', '{}'::jsonb, 'queued', 0, 1, now(), 0, now(), now())",
    'DELETE FROM listening_spoken', 'DELETE FROM listening_jobs', 'DELETE FROM listening_ai_limits',
    "UPDATE listening_responses SET option_id = 'a'", "UPDATE listening_exercise_rubrics SET data = '{}'::jsonb",
    "UPDATE listening_lesson_versions SET title = 'x'", 'TRUNCATE listening_spoken',
    'CREATE TABLE worker_made_this (id int)', 'ALTER TABLE listening_spoken ADD COLUMN x int'])
def test_everything_outside_the_workers_remit_is_refused(role, statement):
    j, name = role
    start_pending(j)
    with pytest.raises(DBAPIError):
        with worker_engine(j, name).begin() as db:
            db.exec_driver_sql(statement)


def test_one_learner_cannot_be_reached_through_a_table_the_worker_does_not_own(role):
    j, name = role
    start_pending(j)
    with worker_engine(j, name).connect() as db:
        assert db.execute(text('SELECT count(*) FROM listening_spoken')).scalar_one() == 1       # what it needs, it sees
        for hidden in ('users', 'listening_exercise_keys', 'listening_attempts'):
            with pytest.raises(DBAPIError):
                db.execute(text(f'SELECT 1 FROM {hidden} LIMIT 1'))
            db.rollback()


def test_grants_alone_return_no_rows_because_row_level_security_has_no_policies(journey):
    j = journey
    if j.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')
    name = 'lw_' + uuid4().hex[:10]
    start_pending(j)
    with j.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {name} NOLOGIN')
        db.exec_driver_sql(worker_role_sql(name, policies=False))
    try:
        with worker_engine(j, name).connect() as db:
            assert db.execute(text('SELECT count(*) FROM listening_spoken')).scalar_one() == 0   # RLS hides every row without a policy
    finally:
        with j.engine.begin() as db:
            db.exec_driver_sql(f'DROP OWNED BY {name}')
            db.exec_driver_sql(f'DROP ROLE {name}')


def test_the_guard_still_fails_closed_under_the_restricted_role_when_the_ceiling_is_used(role):
    j, name = role
    engine = worker_engine(j, name)
    guard = AiGuard(engine, Settings(listening_provider='openai', OPENAI_API_KEY='sk-test-not-real', listening_ai_daily_budget_usd=0.002, _env_file=None))
    guard.reserve('assessment', 2000)
    with pytest.raises(BudgetRefused):
        guard.reserve('assessment', 1)
