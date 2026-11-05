"""Production-cutover safeguards: operational health and alerts, the token-protected monitor endpoint, and the tested rollback to a database with only 1.x.
Health tests run on SQLite and PostgreSQL; the rollback tests need PostgreSQL (AUDLI_TEST_POSTGRES_URL)."""
import json
import os
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert, select, text, update

from app.config import Settings
from app.listening import health, jobs, schema as l, store
from app.listening.migrations import postgres_listening_rollback
from app.listening.seed import load_fixture
from app.main import create_app
from app.storage.repository import utcnow
from conftest import ASGIClient, FakeProvider
from test_auth import AccountClient, auth_response, settings_for
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_journey import begin, journey, start_pending, upload  # noqa: F401  (fixtures)

PG = pytest.mark.skipif(not os.environ.get('AUDLI_TEST_POSTGRES_URL'), reason='needs AUDLI_TEST_POSTGRES_URL')


# ---- health and alerts -------------------------------------------------------------------------------------------------------------

def test_a_quiet_healthy_system_reports_zeros_and_no_alerts(journey):
    report = health.report_with_alerts(journey.engine, Settings(_env_file=None))
    assert report['alerts'] == [] and report['jobs'] == {'queued': 0, 'running': 0, 'failed_total': 0, 'failed_last_24h': 0, 'oldest_ready_seconds': 0, 'expired_leases': 0}
    assert report['answers'] == {'pending': 0, 'oldest_pending_seconds': 0} and report['recordings'] == {'held': 0, 'oldest_held_seconds': 0}


def test_a_waiting_job_a_pending_answer_and_a_held_recording_raise_alerts_only_when_late(journey):
    j = journey
    start_pending(j)
    now = utcnow()
    fresh = health.report_with_alerts(j.engine, Settings(_env_file=None), now=now)
    assert (fresh['jobs']['queued'], fresh['answers']['pending'], fresh['recordings']['held']) == (1, 1, 1) and fresh['alerts'] == []        # normal work in progress
    late = health.report_with_alerts(j.engine, Settings(_env_file=None), now=now + timedelta(hours=3))
    text_ = ' '.join(late['alerts'])
    assert 'worker running' in text_ and 'answer has been pending' in text_ and 'cleanup is not running' in text_


def test_failed_jobs_expired_leases_halting_and_an_exhausted_budget_are_reported(journey):
    j = journey
    start_pending(j)
    with j.engine.begin() as db:
        job = jobs.claim(db, 'w', lease_seconds=10, now=utcnow())
    report = health.report_with_alerts(j.engine, Settings(_env_file=None), now=utcnow() + timedelta(minutes=5))
    assert report['jobs']['expired_leases'] == 1 and any('lease' in a for a in report['alerts'])
    with j.engine.begin() as db:
        db.execute(update(l.jobs).where(l.jobs.c.id == job.id).values(status='failed', lease_owner=None, finished_at=utcnow()))
    from app.listening.ai_budget import AiGuard
    guard = AiGuard(j.engine, Settings(listening_ai_daily_budget_usd=0.01, listening_provider='openai', OPENAI_API_KEY='sk-test-not-real', _env_file=None))
    guard.reserve('assessment', 10000)
    guard.set_halted(True)
    report = health.report_with_alerts(j.engine, Settings(listening_ai_daily_budget_usd=0.01, _env_file=None))
    joined = ' '.join(report['alerts'])
    assert report['jobs']['failed_last_24h'] == 1 and 'failed in the last 24 hours' in joined and 'kill switch is on' in joined and 'budget is used up' in joined


def test_the_report_contains_numbers_only(journey):
    j = journey
    start_pending(j)
    raw = json.dumps(health.report_with_alerts(j.engine, Settings(_env_file=None)))
    for secret in (j.owner, 'transcript', 'listening-tmp', 'recording_key', 'object_key', 'sk-'):
        assert secret not in raw


def test_the_command_line_exits_nonzero_when_an_alert_fires(journey, monkeypatch, capsys):
    j = journey
    monkeypatch.setattr('app.config.Settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr('app.listening.worker.make_engine', lambda settings: j.engine)
    assert health.main([]) == 0 and json.loads(capsys.readouterr().out)['alerts'] == []
    from app.listening.ai_budget import AiGuard
    AiGuard(j.engine, Settings(_env_file=None)).set_halted(True)
    assert health.main([]) == 1 and 'kill switch' in capsys.readouterr().out


# ---- the monitor endpoint ----------------------------------------------------------------------------------------------------------------

TOKEN = 'ops-token-' + 'x' * 20


def test_the_monitor_endpoint_is_absent_without_a_token_and_protected_with_one(journey):
    j = journey
    plain = ASGIClient(create_app(Settings(data_dir=j.tmp, provider='demo', listening_enabled=True, _env_file=None), FakeProvider(), repository=j.repo), None)
    assert plain.get('/api/listening/ops/health').status_code == 404
    armed = ASGIClient(create_app(Settings(data_dir=j.tmp, provider='demo', listening_enabled=True, listening_ops_token=TOKEN, _env_file=None), FakeProvider(), repository=j.repo), None)
    for headers in ({}, {'Authorization': 'Bearer wrong'}, {'Authorization': TOKEN}, {'Authorization': 'Bearer ' + TOKEN[:-1]}):
        assert armed.get('/api/listening/ops/health', headers=headers).status_code == 401
    good = armed.get('/api/listening/ops/health', headers={'Authorization': 'Bearer ' + TOKEN})
    assert good.status_code == 200 and set(good.json()) == {'at', 'jobs', 'answers', 'recordings', 'ai', 'alerts'} and TOKEN not in good.text
    with pytest.raises(Exception):
        Settings(listening_ops_token='short', _env_file=None)


def test_the_monitor_endpoint_works_in_accounts_mode_without_a_learner_session(make_repository, tmp_path):
    repo = make_repository(None)
    from test_listening_library import prepare_all
    prepare_all(repo, tmp_path)
    app = create_app(settings_for(tmp_path, listening_enabled=True, listening_ops_token=TOKEN), FakeProvider(), repository=repo,
                     auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response)))
    anonymous = ASGIClient(app, None)
    assert anonymous.get('/api/listening/lessons').status_code == 401                                   # learner routes still need a signed-in learner
    assert anonymous.get('/api/listening/ops/health', headers={'Authorization': 'Bearer ' + TOKEN}).status_code == 200
    assert anonymous.get('/api/listening/ops/health').status_code == 401


# ---- rollback (PostgreSQL) --------------------------------------------------------------------------------------------------------------

@pytest.fixture
def database():
    import psycopg
    from psycopg import sql
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    admin = os.environ['AUDLI_TEST_POSTGRES_URL']
    name = 'audli_roll_' + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url = make_url(admin).set(database=name).render_as_string(hide_password=False)
    engine = create_engine(make_url(url).set(drivername='postgresql+psycopg'))
    yield url, engine
    engine.dispose()
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def run_rollback(url, *confirmations):
    import psycopg
    with psycopg.connect(url, autocommit=True) as db:
        for setting in confirmations:
            db.execute(f"SET {setting} = 'yes'")
        db.execute(postgres_listening_rollback(), prepare=False)


@PG
def test_the_rollback_is_refused_without_words_and_leaves_everything_in_place(database):
    import psycopg
    from app.storage.adapters import PostgresRepository
    from scripts.migrate_postgres import migrate
    url, engine = database
    settings = Settings(persistence='postgres', database_url=url, learner_id=str(uuid4()), _env_file=None)
    migrate(settings, listening=True)
    owner = str(uuid4())
    repo = PostgresRepository(url, owner)
    content, audio, _ = load_fixture()
    with engine.begin() as db:
        version_id, _ = store.publish_version(db, content, audio)
        lesson_id = db.execute(select(l.versions.c.lesson_id).where(l.versions.c.id == version_id)).scalar_one()
        store.create_attempt(db, owner, lesson_id, 'rollback-attempt-1', None)
    repo.close()
    with pytest.raises(psycopg.errors.Error, match='confirm_listening_rollback'):
        run_rollback(url)
    with pytest.raises(psycopg.errors.Error, match='attempts exist'):
        run_rollback(url, 'audli.confirm_listening_rollback')
    with engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM listening_attempts')).scalar_one() == 1       # nothing was dropped


@PG
def test_the_rollback_removes_only_audli_2_leaves_1x_data_and_can_be_reapplied(database):
    from app.storage.adapters import PostgresRepository
    from scripts.migrate_postgres import migrate
    url, engine = database
    settings = Settings(persistence='postgres', database_url=url, learner_id=str(uuid4()), _env_file=None)
    migrate(settings)
    owner = str(uuid4())
    repo = PostgresRepository(url, owner)
    profile = repo.profile()
    profile.name, profile.goal = 'Maya', 'Follow meetings'
    repo.save_profile(profile)
    repo.close()
    migrate(settings, listening=True)

    def one_x_counts():
        with engine.connect() as db:
            names = sorted(r[0] for r in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")) if not r[0].startswith('listening_') or r[0] == 'listening_content')
            return {n: db.execute(text(f'SELECT count(*) FROM "{n}"')).scalar_one() for n in names}
    before = one_x_counts()
    content, audio, _ = load_fixture()
    with engine.begin() as db:
        store.publish_version(db, content, audio)
    run_rollback(url, 'audli.confirm_listening_rollback')                                            # no attempts exist: one confirmation is enough
    with engine.connect() as db:
        left = {r[0] for r in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
        functions = {r[0] for r in db.execute(text("SELECT proname FROM pg_proc WHERE starts_with(proname, 'listening_guard_')"))}
        ledger = db.execute(text('SELECT version FROM schema_migrations ORDER BY 1')).scalars().all()
    assert not (set(l.NAMES) - {'listening_content'}) & left and not functions
    assert one_x_counts() == before and ledger == [1, 2, 3, 4, 5, 6, 7]                               # 1.x data and its ledger are exactly as they were
    reopened = PostgresRepository(url, owner)
    assert reopened.profile().name == 'Maya'                                                          # 1.x starts and still knows the learner
    reopened.close()
    assert migrate(settings, listening=True) is True                                                  # and 2.0 can be applied again from scratch
    with engine.begin() as db:
        assert store.publish_version(db, content, audio)[1] is True


@PG
def test_the_rollback_can_discard_learner_data_when_told_to(database):
    from app.storage.adapters import PostgresRepository
    from scripts.migrate_postgres import migrate
    url, engine = database
    settings = Settings(persistence='postgres', database_url=url, learner_id=str(uuid4()), _env_file=None)
    migrate(settings, listening=True)
    owner = str(uuid4())
    PostgresRepository(url, owner).close()
    content, audio, _ = load_fixture()
    with engine.begin() as db:
        version_id, _ = store.publish_version(db, content, audio)
        lesson_id = db.execute(select(l.versions.c.lesson_id).where(l.versions.c.id == version_id)).scalar_one()
        store.create_attempt(db, owner, lesson_id, 'rollback-attempt-2', None)
    run_rollback(url, 'audli.confirm_listening_rollback', 'audli.discard_learner_data')
    with engine.connect() as db:
        assert db.execute(text("SELECT to_regclass('public.listening_attempts')")).scalar_one() is None
