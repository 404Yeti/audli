"""PostgreSQL-only: migration 015 as applied, the least-privilege daily role, the read-only API grant, and the scheduler running ENTIRELY under the restricted role.
Skipped on SQLite (these are properties of real roles and row-level security). Runs in CI on the disposable postgres:17 service."""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError

from app.listening import daily as dl, schema as t
from app.listening.generation import Ledger
from app.listening.providers import FakeDailyJudge, FakeTopicProposer
from app.listening.roles import api_role_sql, daily_role_sql
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_daily import Clock, D1, D2, at, daily_settings, day, lab  # noqa: F401  (fixtures)


def as_role(engine, role):
    restricted = create_engine(engine.url.render_as_string(hide_password=False))

    @event.listens_for(restricted, 'connect')
    def switch(dbapi, record):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
        dbapi.commit()
    return restricted


@pytest.fixture
def daily_role(day):
    if day.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')
    name = 'ld_' + uuid4().hex[:10]
    with day.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {name} NOLOGIN')
        db.exec_driver_sql(daily_role_sql(name))
    yield day, name
    with day.engine.begin() as db:
        db.exec_driver_sql(f'DROP OWNED BY {name}')
        db.exec_driver_sql(f'DROP ROLE {name}')


def test_migration_015_is_recorded_secured_and_not_reappliable(day):
    if day.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')
    with day.engine.connect() as db:
        assert 15 in {r[0] for r in db.execute(text('SELECT version FROM listening_migrations'))}
        rls = dict(db.execute(text("SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('listening_daily_challenges', 'listening_daily_control')")).all())
        assert rls == {'listening_daily_challenges': True, 'listening_daily_control': True}
        public = db.execute(text("SELECT count(*) FROM pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a WHERE c.relname LIKE 'listening_daily_%' "
                                 "AND a.grantee = 0")).scalar_one()
        assert public == 0                                                                                   # nothing granted to PUBLIC
    from app.config import Settings
    from scripts.migrate_postgres import migrate
    url = day.lab.repo.engine.url.render_as_string(hide_password=False)
    assert migrate(Settings(persistence='postgres', database_url=url, learner_id=str(uuid4()), _env_file=None), listening=True) is False      # already applied: nothing runs twice


def test_the_whole_scheduler_runs_under_the_restricted_daily_role(daily_role):
    day_, name = daily_role
    engine = as_role(day_.engine, name)
    from app.listening.api import prepare
    prepare(engine, daily=True)                                           # the process's own start-up check passes under the restricted role...
    with pytest.raises(RuntimeError, match='lacks privileges'):
        prepare(engine)                                                   # ...and the API profile correctly does not
    s = daily_settings(day_.owner)
    d = dl.Daily(engine=engine, settings=s, ledger=Ledger(engine, s), judge=FakeDailyJudge(), proposer=FakeTopicProposer(), clock=Clock(at(D1, 23, 5)), owner='restricted')
    dl.tick(d)                                                            # plan + reserve + queue, as the restricted role
    day_.generate(dl.Daily(engine=day_.engine, settings=s, ledger=Ledger(day_.engine, s), judge=FakeDailyJudge(), proposer=FakeTopicProposer()))     # the generator is a different process
    assert dl.tick(d)['states'][D2] == 'scheduled'                         # gates, register and approve as the restricted role
    d.clock.now = at(D2, 0, 0)
    assert dl.tick(d)['states'][D2] == 'active'                            # publish (the one DELETE it holds) as the restricted role
    with day_.engine.connect() as db:
        lesson = db.execute(select(t.daily_challenges.c.lesson_id).where(t.daily_challenges.c.challenge_date == D2)).scalar_one()
        assert db.execute(select(t.lesson_owners.c.user_id).where(t.lesson_owners.c.lesson_id == lesson)).first() is None
    summary = dl.summary(engine, s, at(D2, 1))
    assert summary['migrated'] and summary['today_state'] in ('active', 'none', 'scheduled', 'generating')


REFUSED = [
    'SELECT * FROM users', 'SELECT * FROM listening_attempts', 'SELECT * FROM listening_responses', 'SELECT * FROM listening_answers', 'SELECT * FROM listening_spoken',
    "INSERT INTO listening_attempts (id) VALUES (gen_random_uuid())",
    'DELETE FROM listening_daily_challenges',                              # history is permanent
    'DELETE FROM listening_library_entries', 'DELETE FROM listening_lessons', 'DELETE FROM listening_generation_requests', 'DELETE FROM listening_jobs',
    "UPDATE listening_lesson_versions SET title = 'x'", "UPDATE listening_audio_variants SET voice = 'x'", "UPDATE listening_exercise_keys SET explanation = 'x'",
    "UPDATE listening_ai_limits SET halted = false", "UPDATE listening_lesson_owners SET user_id = user_id",
    "INSERT INTO listening_lesson_owners (lesson_id, user_id, created_at) VALUES (gen_random_uuid(), gen_random_uuid(), now())",
    'TRUNCATE listening_daily_challenges', 'TRUNCATE listening_lesson_owners', 'CREATE TABLE daily_made_this (id int)', 'ALTER TABLE listening_daily_challenges ADD COLUMN x int']


def test_everything_outside_the_daily_roles_remit_is_refused(daily_role):
    day_, name = daily_role
    engine = as_role(day_.engine, name)
    for statement in REFUSED:                                              # one database, one statement per transaction
        with pytest.raises(DBAPIError):
            with engine.begin() as db:
                db.exec_driver_sql(statement)


def test_the_daily_role_can_do_exactly_what_publishing_needs(daily_role):
    day_, name = daily_role
    with as_role(day_.engine, name).connect() as db:
        for table in ('listening_daily_challenges', 'listening_daily_control', 'listening_library_entries', 'listening_lesson_owners', 'listening_exercise_rubrics'):
            assert db.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() >= 0
        granted = {(r.table_name, r.privilege_type) for r in db.execute(text(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee = :r AND table_name LIKE 'listening_%'"), {'r': name})}
    assert ('listening_lesson_owners', 'DELETE') in granted
    assert {table for table, privilege in granted if privilege == 'DELETE'} == {'listening_lesson_owners'}
    assert not {table for table, privilege in granted if table in ('listening_attempts', 'listening_responses', 'listening_answers', 'listening_spoken')}


def test_grants_alone_return_no_rows_without_the_row_level_security_policies(day):
    if day.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')
    name = 'ld_' + uuid4().hex[:10]
    d = day.build()
    dl.tick(d)
    with day.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {name} NOLOGIN')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {name}')
        db.exec_driver_sql(f'GRANT SELECT ON listening_daily_challenges TO {name}')
    try:
        with as_role(day.engine, name).connect() as db:
            assert db.execute(text('SELECT count(*) FROM listening_daily_challenges')).scalar_one() == 0     # RLS hides every row without a policy
    finally:
        with day.engine.begin() as db:
            db.exec_driver_sql(f'DROP OWNED BY {name}')
            db.exec_driver_sql(f'DROP ROLE {name}')


def test_the_api_role_can_read_the_daily_tables_and_nothing_more(day):
    if day.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL only')
    name = 'ap_' + uuid4().hex[:10]
    d = day.build()
    dl.tick(d)
    with day.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {name} NOLOGIN')
        db.exec_driver_sql(api_role_sql(name))
    try:
        with as_role(day.engine, name).connect() as db:
            assert db.execute(text('SELECT count(*) FROM listening_daily_challenges')).scalar_one() >= 1       # today's card can read it
            db.execute(text('SELECT count(*) FROM listening_daily_control'))
        for statement in ("INSERT INTO listening_daily_control (scope, paused, updated_at) VALUES ('x', true, now())", "UPDATE listening_daily_challenges SET state = 'failed'",
                          'DELETE FROM listening_daily_challenges', 'TRUNCATE listening_daily_challenges'):
            with pytest.raises(DBAPIError):
                with as_role(day.engine, name).begin() as db:
                    db.exec_driver_sql(statement)
    finally:
        with day.engine.begin() as db:
            db.exec_driver_sql(f'DROP OWNED BY {name}')
            db.exec_driver_sql(f'DROP ROLE {name}')
