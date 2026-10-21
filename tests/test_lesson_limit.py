"""AUD-47 phase 2: durable 24-hour entitlement ledger, atomic claim, eligibility and off/observe modes.

Runs on SQLite and, with AUDLI_TEST_POSTGRES_URL, on a disposable PostgreSQL (CI). Mock providers establish
plumbing only; these tests do not measure evaluator accuracy.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select, text, update
from app.config import Settings
from app.lesson import Lesson
from app.lesson_limit import LessonCooldown, evaluate, WINDOW
from app.main import create_app
from app.onboarding_reset import reset_onboarding
from app.repository import ProgressRepository
from app.storage import schema as s
from app.storage.repository import aware
from app.storage.migrations import postgres_lesson_limit_migration
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def make(at, **fields):
    return Lesson(id=str(uuid4()), started_at=at, active_since=at, name='Maya', focus='details', **fields)


def ledger(repo):
    with repo.engine.connect() as db:
        row = db.execute(select(s.lesson_entitlements).where(s.lesson_entitlements.c.user_id == repo.learner_id)).mappings().first()
    # SQLite returns naive UTC; compare instants, not representations.
    return None if row is None else {key: aware(value) if isinstance(value, datetime) else value for key, value in row.items()}


def finish(repo, lesson):
    lesson.status, lesson.phase = 'completed', 'COMPLETED'
    repo.save_lesson(lesson, lesson.revision)


def count(repo):
    with repo.engine.connect() as db:
        return len(db.execute(select(s.lessons.c.id).where(s.lessons.c.user_id == repo.learner_id)).all())


def test_migration_006_is_generated_private_and_packaged():
    sql = postgres_lesson_limit_migration()
    assert Path('docs/postgres-006.sql').read_text() == sql
    assert 'ALTER TABLE lesson_entitlements ENABLE ROW LEVEL SECURITY' in sql
    assert 'REVOKE ALL ON TABLE lesson_entitlements FROM PUBLIC' in sql
    assert 'REFERENCES lesson_lifecycles' not in sql  # Lesson cleanup must never reach the ledger.
    assert 'postgres-006.sql' in Path('Dockerfile').read_text()
    assert 'postgres-006.sql' in Path('scripts/migrate_postgres.py').read_text()


def test_enforce_is_not_selectable_in_this_release():
    with pytest.raises(ValueError):
        Settings(lesson_limit_mode='enforce', _env_file=None)
    assert Settings(_env_file=None).lesson_limit_mode == 'off'


def test_off_mode_never_touches_the_ledger(repository_factory):
    repo = repository_factory()
    first = make(T0)
    repo.create_lesson(first)
    finish(repo, first)
    repo.create_lesson(make(T0 + timedelta(minutes=1)))  # Current production behavior: no allowance.
    assert ledger(repo) is None and count(repo) == 2


def test_claim_is_recorded_atomically_with_the_lesson(repository_factory):
    repo = repository_factory()
    lesson = make(T0)
    repo.create_lesson(lesson, limit_mode='observe')
    row = ledger(repo)
    assert row['last_lesson_id'] == lesson.id and row['voided_at'] is None
    assert row['next_eligible_at'] - row['last_started_at'] == WINDOW


@pytest.mark.parametrize('offset,allowed', [(timedelta(hours=24) - timedelta(seconds=1), False),
    (timedelta(hours=24), True), (timedelta(hours=24, seconds=1), True), (timedelta(0), False)])
def test_window_boundaries_enforced_at_repository_level(repository_factory, offset, allowed):
    repo = repository_factory()
    first = make(T0)
    repo.create_lesson(first, limit_mode='enforce')
    finish(repo, first)
    second = make(T0 + offset)
    if allowed:
        repo.create_lesson(second, limit_mode='enforce')
        assert ledger(repo)['last_lesson_id'] == second.id
    else:
        with pytest.raises(LessonCooldown) as refused:
            repo.create_lesson(second, limit_mode='enforce')
        assert refused.value.next_eligible_at == T0 + WINDOW
        assert count(repo) == 1 and ledger(repo)['last_lesson_id'] == first.id


def test_observe_logs_would_refuse_without_blocking_or_extending_the_window(repository_factory, caplog):
    repo = repository_factory()
    first = make(T0)
    repo.create_lesson(first, limit_mode='observe')
    finish(repo, first)
    caplog.set_level(logging.INFO, logger='uvicorn.error')
    second = make(T0 + timedelta(hours=1))
    repo.create_lesson(second, limit_mode='observe')
    assert count(repo) == 2  # Never blocks.
    row = ledger(repo)
    assert row['last_lesson_id'] == first.id and row['next_eligible_at'] == T0 + WINDOW
    messages = [record.getMessage() for record in caplog.records if 'would_refuse' in record.getMessage()]
    assert len(messages) == 1 and repo.learner_id not in messages[0]


def test_entitlement_survives_onboarding_restart_and_lesson_cleanup(repository_factory):
    repo = repository_factory()
    lesson = make(T0)
    repo.create_lesson(lesson, limit_mode='enforce')
    reset_onboarding(repo, repo.profile().onboarding.revision)  # Deletes open lesson rows.
    assert repo.lesson(lesson.id) is None
    with pytest.raises(LessonCooldown):
        repo.create_lesson(make(T0 + timedelta(hours=2)), limit_mode='enforce')
    done = make(T0 + timedelta(hours=30))
    repo.create_lesson(done, limit_mode='enforce')
    finish(repo, done)
    with repo.engine.begin() as db:  # Retention-style cleanup of every lesson record.
        db.execute(s.lesson_exercises.delete())
        db.execute(s.lessons.delete().where(s.lessons.c.user_id == repo.learner_id))
    assert count(repo) == 0 and ledger(repo)['last_lesson_id'] == done.id
    with pytest.raises(LessonCooldown):
        repo.create_lesson(make(T0 + timedelta(hours=31)), limit_mode='enforce')


def test_existing_learner_uses_latest_lesson_start_without_a_ledger_row(repository_factory):
    repo = repository_factory()
    old, latest = make(T0 - timedelta(days=9)), make(T0)
    for item in (old, latest):
        repo.create_lesson(item)  # Off: written by the previous release, no ledger.
        finish(repo, item)
    assert ledger(repo) is None
    eligibility = repo.eligibility('observe', T0 + timedelta(hours=3))
    assert not eligibility.eligible and eligibility.next_available_at == T0 + WINDOW
    with pytest.raises(LessonCooldown):
        repo.create_lesson(make(T0 + timedelta(hours=3)), limit_mode='enforce')
    assert repo.eligibility('observe', T0 + WINDOW).eligible
    # Historical lesson outcomes are untouched.
    assert {item.id for item in repo.accent_lesson_history()} == {old.id, latest.id}


def test_lesson_started_by_a_ledger_unaware_release_is_honored(repository_factory):
    repo = repository_factory()
    first = make(T0)
    repo.create_lesson(first, limit_mode='observe')
    finish(repo, first)
    later = make(T0 + timedelta(hours=25))
    repo.create_lesson(later)  # Off: previous release bypasses the ledger.
    finish(repo, later)
    assert ledger(repo)['last_lesson_id'] == first.id
    with pytest.raises(LessonCooldown):
        repo.create_lesson(make(T0 + timedelta(hours=26)), limit_mode='enforce')
    third = make(T0 + timedelta(hours=49, minutes=1))
    repo.create_lesson(third, limit_mode='enforce')  # Re-syncs the ledger; the guard accepts a real window end.
    assert ledger(repo)['last_lesson_id'] == third.id


def test_concurrent_starts_claim_exactly_once(repository_factory):
    base = repository_factory()
    first = make(T0)
    base.create_lesson(first, limit_mode='enforce')
    finish(base, first)
    repos = [repository_factory() for _ in range(6)]
    barrier, outcomes = threading.Barrier(len(repos)), []
    def start(repo, index):
        barrier.wait()
        try:
            repo.create_lesson(make(T0 + WINDOW + timedelta(seconds=index)), limit_mode='enforce')
            outcomes.append('started')
        except (LessonCooldown, ValueError) as error:
            outcomes.append(type(error).__name__)
    threads = [threading.Thread(target=start, args=(repo, index)) for index, repo in enumerate(repos)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(60)
    assert outcomes.count('started') == 1 and len(outcomes) == len(repos)
    assert count(base) == 2


def test_failed_start_does_not_consume_the_allowance(repository_factory):
    repo = repository_factory()
    real = repo.claim_lesson_start
    def claim_then_fail(db, lesson, mode):
        real(db, lesson, mode)
        raise RuntimeError('storage failed after the claim')
    repo.claim_lesson_start = claim_then_fail
    with pytest.raises(RuntimeError):
        repo.create_lesson(make(T0), limit_mode='enforce')
    assert ledger(repo) is None and count(repo) == 0
    repo.claim_lesson_start = real
    repo.create_lesson(make(T0 + timedelta(seconds=5)), limit_mode='enforce')  # Retry is not penalized.


def test_void_is_limited_to_the_unstarted_current_lesson(repository_factory):
    repo = repository_factory()
    lesson = make(T0)
    repo.create_lesson(lesson, limit_mode='enforce')
    assert repo.void_allowance(str(uuid4()), T0) is False  # Not the ledger's lesson.
    assert repo.void_allowance(lesson.id, T0 + timedelta(seconds=30)) is True
    assert repo.void_allowance(lesson.id, T0 + timedelta(seconds=31)) is False  # Only once.
    row = ledger(repo)
    assert row['voided_at'] is not None and row['next_eligible_at'] == row['last_started_at']
    started = make(T0 + timedelta(minutes=1))  # The verified failure freed the allowance.
    lesson_row = repo.lesson(lesson.id)
    lesson_row.status = 'completed'; lesson_row.phase = 'COMPLETED'
    repo.save_lesson(lesson_row, lesson_row.revision)
    repo.create_lesson(started, limit_mode='enforce')
    # A lesson with progress (revision > 0) can never be voided.
    repo.save_lesson(started, started.revision)
    assert repo.void_allowance(started.id) is False


def test_eligibility_uses_server_time_and_rounds_retry_up(repository_factory):
    repo = repository_factory()
    lesson = make(T0)
    repo.create_lesson(lesson, limit_mode='observe')
    open_state = repo.eligibility('observe', T0 + timedelta(minutes=1))
    assert open_state.reason == 'lesson_in_progress' and not open_state.eligible
    assert open_state.open_lesson.id == lesson.id and open_state.open_lesson.expires_at == T0 + timedelta(seconds=600)
    finish(repo, lesson)
    at = T0 + WINDOW - timedelta(seconds=.4)
    cooling = repo.eligibility('observe', at)
    assert cooling.reason == 'cooldown' and cooling.retry_after_seconds == 1 and cooling.server_time == at
    assert cooling.next_available_at == T0 + WINDOW and cooling.enforced is False
    assert repo.eligibility('observe', T0 + WINDOW).eligible
    off = repo.eligibility('off', T0)
    assert off.eligible and off.mode == 'off' and off.next_available_at is None


def test_pure_evaluation_clock_skew_is_a_server_decision():
    ready = evaluate('observe', T0 + WINDOW, T0 + WINDOW, T0, None)
    assert ready.eligible and ready.retry_after_seconds is None
    behind = evaluate('observe', T0 + WINDOW - timedelta(hours=5), T0 + WINDOW, T0, None)  # Browser clock is irrelevant.
    assert behind.retry_after_seconds == 5 * 3600


@pytest.fixture
def api(repository_factory, tmp_path, audio_bytes):
    def build(mode, repo=None):
        repo = repo or repository_factory()
        profile = repo.profile(); profile.name = 'Maya'; profile.onboarding_status = 'complete'; repo.save_profile(profile)
        provider = FakeProvider()
        async def speak(text, speech_rate=.9): return audio_bytes
        provider.speak = speak
        return repo, ASGIClient(create_app(Settings(data_dir=tmp_path, lesson_limit_mode=mode, _env_file=None), provider, repo), provider)
    return build


def test_api_off_mode_reports_neutral_eligibility_and_starts_as_before(api):
    repo, client = api('off')
    body = client.get('/api/eligibility').json()
    assert body['mode'] == 'off' and body['eligible'] and body['enforced'] is False and body['next_available_at'] is None
    assert client.post('/api/lessons/start').status_code == 200 and ledger(repo) is None


def test_api_observe_records_start_and_refresh_or_second_device_do_not_reset_it(api):
    repo, client = api('observe')
    lesson = client.post('/api/lessons/start').json()
    claimed = ledger(repo)
    assert claimed['last_lesson_id'] == lesson['id']
    again = client.post('/api/lessons/start').json()  # Refresh, retry or another device.
    assert again['id'] == lesson['id'] and ledger(repo)['last_started_at'] == claimed['last_started_at']
    state = client.get('/api/eligibility').json()
    assert state['reason'] == 'lesson_in_progress' and state['open_lesson']['id'] == lesson['id']
    assert state['open_lesson']['expires_at'] and state['server_time']
    end = client.post(f"/api/lessons/{lesson['id']}/end", json={'revision': lesson['revision']})
    assert end.status_code == 200  # Pausing does not touch the allowance.
    assert ledger(repo)['last_started_at'] == claimed['last_started_at']


def test_api_observe_never_blocks_a_second_lesson(api):
    repo, client = api('observe')
    first = client.post('/api/lessons/start').json()
    stored = repo.lesson(first['id']); stored.status = 'completed'; stored.phase = 'COMPLETED'
    repo.save_lesson(stored, stored.revision)
    cooling = client.get('/api/eligibility').json()
    assert cooling['reason'] == 'cooldown' and cooling['next_available_at'] and cooling['retry_after_seconds'] > 0
    second = client.post('/api/lessons/start')
    assert second.status_code == 200 and second.json()['id'] != first['id']  # Observe only logs.
    assert ledger(repo)['last_lesson_id'] == first['id']


def test_observe_without_migration_006_fails_closed_but_off_starts(tmp_path):
    repo = ProgressRepository(tmp_path / 'audli.sqlite3')
    with repo.engine.begin() as db:
        s.lesson_entitlements.drop(db)
        db.execute(s.versions.delete().where(s.versions.c.version == 6))
    create_app(Settings(data_dir=tmp_path, _env_file=None), FakeProvider(), repo)
    with pytest.raises(RuntimeError, match='migration 006'):
        create_app(Settings(data_dir=tmp_path, lesson_limit_mode='observe', _env_file=None), FakeProvider(), repo)


def test_ledger_is_per_account(repository_factory):
    a, b = repository_factory(), repository_factory(str(uuid4()))
    a.create_lesson(make(T0), limit_mode='enforce')
    b.create_lesson(make(T0 + timedelta(minutes=1)), limit_mode='enforce')  # Another learner is unaffected.
    assert ledger(a)['last_lesson_id'] != ledger(b)['last_lesson_id']


# ---- PostgreSQL only: database-level backstop and the migration itself (run in CI) ----

def pg(repo):
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL trigger and migration behavior')


def test_postgres_backstop_refuses_early_windows_even_from_raw_sql(repository_factory):
    repo = repository_factory(); pg(repo)
    repo.create_lesson(make(T0), limit_mode='enforce')
    def update_raw(sql, **values):
        with repo.engine.begin() as db:
            db.execute(text(sql), {'owner': repo.learner_id, **values})
    with pytest.raises(Exception, match='lesson_cooldown_active'):
        update_raw("UPDATE lesson_entitlements SET last_started_at=:at, next_eligible_at=:at + interval '24 hours' WHERE user_id=:owner",
                   at=T0 + timedelta(hours=1))
    with pytest.raises(Exception, match='lesson_window_extension_refused'):
        update_raw("UPDATE lesson_entitlements SET next_eligible_at=next_eligible_at + interval '1 hour' WHERE user_id=:owner")
    with pytest.raises(Exception, match='lesson_window_invalid'):
        update_raw("UPDATE lesson_entitlements SET last_started_at=:at, next_eligible_at=:at + interval '2 hours' WHERE user_id=:owner",
                   at=T0 + WINDOW)
    update_raw("UPDATE lesson_entitlements SET last_started_at=:at, next_eligible_at=:at + interval '24 hours' WHERE user_id=:owner",
               at=T0 + WINDOW)  # A genuine next window is accepted.


def test_postgres_migration_006_backfills_existing_learners_and_allows_expired(repository_factory):
    repo = repository_factory(); pg(repo)
    old, latest = make(T0 - timedelta(days=3)), make(T0 - timedelta(days=1))
    repo.create_lesson(old); finish(repo, old)
    repo.create_lesson(latest); finish(repo, latest)
    sql = Path('docs/postgres-006.sql').read_text()
    with repo.engine.begin() as db:  # Return to the schema-5 shape, then apply the checked-in migration.
        db.execute(text('DROP TABLE lesson_entitlements'))
        db.execute(text('DROP FUNCTION lesson_entitlement_guard()'))
        db.execute(text('ALTER TABLE lesson_lifecycles DROP CONSTRAINT lesson_status_valid'))
        db.execute(text("ALTER TABLE lesson_lifecycles ADD CONSTRAINT lesson_status_valid CHECK (status IN ('active','paused','completed'))"))
        db.execute(text('DROP INDEX one_open_lesson_per_learner'))
        db.execute(text("CREATE UNIQUE INDEX one_open_lesson_per_learner ON lesson_lifecycles (user_id) WHERE status != 'completed'"))
        db.execute(text('DELETE FROM schema_migrations WHERE version = 6'))
    with repo.engine.begin() as db:
        db.exec_driver_sql(sql)
    row = ledger(repo)
    assert row['last_lesson_id'] == latest.id and row['next_eligible_at'] - row['last_started_at'] == WINDOW
    assert count(repo) == 2  # Historical lesson rows are not altered.
    expired = make(T0 - timedelta(hours=2))
    expired.status = 'expired'
    with repo.engine.begin() as db:
        db.execute(s.lessons.insert().values(id=expired.id, user_id=repo.learner_id, status='expired', revision=0,
            started_at=expired.started_at, data=expired.model_dump(mode='json')))
    repo.create_lesson(make(T0))  # An expired lesson is not an open lesson.
    with pytest.raises(Exception):
        repo.create_lesson(make(T0 + timedelta(seconds=1)))
    with repo.engine.connect() as db:
        assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one() == 6
