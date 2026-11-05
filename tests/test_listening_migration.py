"""Migration 009 against real PostgreSQL: fresh install, upgrade, repeated runs, both orders with the real migration 008, and the
claims-schema compatibility that is the reason 009 keeps its own ledger. Needs AUDLI_TEST_POSTGRES_URL (a disposable cluster)."""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.listening import schema as l
from app.listening.api import prepare
from app.storage.adapters import PostgresRepository
from scripts.migrate_postgres import migrate, LISTENING_MIGRATIONS

pytestmark = pytest.mark.skipif(not os.environ.get('AUDLI_TEST_POSTGRES_URL'), reason='needs AUDLI_TEST_POSTGRES_URL')
DOCS = Path(__file__).resolve().parents[1] / 'docs'
SUPABASE_ROLES = ('anon', 'authenticated', 'service_role')    # migration 008 names them; a disposable cluster can have them


@pytest.fixture
def database():
    import psycopg
    from psycopg import sql
    admin = os.environ['AUDLI_TEST_POSTGRES_URL']
    with psycopg.connect(admin, autocommit=True) as db:
        for role in SUPABASE_ROLES:
            db.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$")
        name = 'audli_mig_' + uuid4().hex
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url = make_url(admin).set(database=name).render_as_string(hide_password=False)
    engines = []

    def engine():
        made = create_engine(make_url(url).set(drivername='postgresql+psycopg'))
        engines.append(made)
        return made
    engine.url = url
    engine.settings = lambda: Settings(persistence='postgres', database_url=url, learner_id=str(uuid4()), _env_file=None)
    yield engine
    for made in engines:
        made.dispose()
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def run_file(engine, name):
    with engine.begin() as db:
        db.exec_driver_sql((DOCS / name).read_text())    # exec_driver_sql, as the repo's own 008 rehearsal applies it


def ledgers(engine):
    with engine.connect() as db:
        main = [r[0] for r in db.execute(text('SELECT version FROM schema_migrations ORDER BY 1'))]
        own = [r[0] for r in db.execute(text('SELECT version FROM listening_migrations ORDER BY 1'))] \
            if db.execute(text("SELECT to_regclass('public.listening_migrations')")).scalar_one() else []
    return main, own


def tables(engine):
    with engine.connect() as db:
        return {r[0] for r in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}


def test_default_migration_never_applies_009_and_the_opt_in_is_repeatable(database):
    settings = database.settings()
    assert migrate(settings) is True
    assert not set(l.NAMES) & tables(database())                 # not part of the default run
    assert migrate(settings) is False
    assert migrate(settings, listening=True) is True
    assert migrate(settings, listening=True) is False            # repeated execution is a safe no-op
    assert ledgers(database()) == ([1, 2, 3, 4, 5, 6, 7], [9])
    assert [v for v, _ in LISTENING_MIGRATIONS] == [9]


def test_fresh_database_starts_1x_and_2x(database):
    migrate(database.settings(), listening=True)
    repo = PostgresRepository(database.url, str(uuid4()))        # 1.x startup validates its ledger and schema
    repo.close()
    prepare(database())                                          # 2.0 startup validates its tables and privileges


@pytest.mark.parametrize('order', [('postgres-008.sql', 'postgres-009.sql'), ('postgres-009.sql', 'postgres-008.sql')])
def test_009_and_the_real_008_apply_in_either_order(database, order):
    migrate(database.settings())
    engine = database()
    for name in order:
        run_file(engine, name)
    assert ledgers(engine) == ([1, 2, 3, 4, 5, 6, 7, 8], [9])
    # The claims runtime's gate reads max(schema_migrations.version); 009 must leave it exactly where 008 put it.
    with engine.connect() as db:
        assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one() == 8
    PostgresRepository(database.url, str(uuid4())).close()       # 1.x startup with the 008 claims guards present
    prepare(engine)


def test_009_before_008_does_not_break_008s_exact_ledger_precondition(database):
    migrate(database.settings(), listening=True)
    run_file(database(), 'postgres-008.sql')                     # asserts the ledger is exactly 1..7; would raise if 009 had touched it
    assert ledgers(database())[0] == [1, 2, 3, 4, 5, 6, 7, 8]


def test_upgrade_preserves_every_existing_row_and_adds_only_new_tables(database):
    settings = database.settings()
    migrate(settings)
    repo = PostgresRepository(database.url, settings.learner_id and str(settings.learner_id))
    owner = repo.learner_id
    profile = repo.profile()
    profile.name, profile.goal = 'Maya', 'Follow meetings'
    repo.save_profile(profile)
    repo.close()
    engine = database()
    before_tables = tables(engine)
    with engine.connect() as db:
        counts = {t: db.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in sorted(before_tables)}
    migrate(settings, listening=True)
    after = tables(engine)
    assert after - before_tables == set(l.NAMES)                 # nothing but the 2.0 tables appeared
    with engine.connect() as db:
        assert {t: db.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in before_tables} == counts
        assert db.execute(text('SELECT count(*) FROM listening_lessons')).scalar_one() == 0
    again = PostgresRepository(database.url, owner)
    assert again.profile().name == 'Maya'
    again.close()


def test_a_repeated_raw_run_is_refused_and_changes_nothing(database):
    migrate(database.settings(), listening=True)
    engine = database()
    snapshot = (tables(engine), ledgers(engine))
    with pytest.raises(DBAPIError, match='already applied or partially present'):
        run_file(engine, 'postgres-009.sql')
    assert (tables(engine), ledgers(engine)) == snapshot


def test_a_partially_present_install_is_refused_rather_than_completed(database):
    migrate(database.settings())
    engine = database()
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE listening_lessons (id uuid primary key)')
    with pytest.raises(DBAPIError, match='already applied or partially present'):
        run_file(engine, 'postgres-009.sql')
    assert 'listening_migrations' not in tables(engine)          # the whole migration rolled back


def test_009_refuses_to_run_without_the_earlier_migrations(database):
    engine = database()
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE schema_migrations (version integer primary key, applied_at timestamptz not null)')
        db.exec_driver_sql('INSERT INTO schema_migrations VALUES (1, now()), (2, now())')
    with pytest.raises(DBAPIError, match='001 through 007'):
        run_file(engine, 'postgres-009.sql')


def test_a_restricted_role_without_reviewed_grants_fails_closed_at_startup(database):
    migrate(database.settings(), listening=True)
    admin = database()
    role = 'listening_restricted_' + uuid4().hex[:8]
    with admin.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE {role} NOLOGIN')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {role}')
    restricted = database()

    @event.listens_for(restricted, 'connect')
    def switch(dbapi, record):
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET ROLE {role}')
        dbapi.commit()
    with pytest.raises(RuntimeError, match='lacks privileges'):
        prepare(restricted)
    with admin.begin() as db:
        db.exec_driver_sql(f'GRANT SELECT ON {", ".join(l.NAMES)} TO {role}')
        db.exec_driver_sql(f'GRANT INSERT ON listening_attempts, listening_responses TO {role}')
    restricted.dispose()
    prepare(restricted)                                          # with explicit grants it starts
