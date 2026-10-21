"""AUD-47 bridge release: tolerate the future `expired` status and schema 6 without producing either."""
from datetime import timedelta
import pytest
from app.lesson import Lesson, now
from app.repository import ProgressRepository
from app.storage import schema as s
from app.storage.adapters import ACCEPTED_POSTGRES_VERSIONS, ACCEPTED_SQLITE_VERSIONS, SCHEMA_VERSION


def snapshot(**fields):
    at = now()
    return dict(id='fixture', started_at=at.isoformat(), active_since=None, name='Maya', focus='details', **fields)


def test_expired_snapshot_is_readable_and_distinct_from_completed():
    lesson = Lesson.model_validate(snapshot(status='expired', elapsed_seconds=240))
    assert lesson.status == 'expired' and lesson.completed_at is None
    assert 'expired' not in s.OPEN_LESSON_STATUSES and 'completed' not in s.OPEN_LESSON_STATUSES


def test_unknown_status_still_rejected():
    with pytest.raises(ValueError):
        Lesson.model_validate(snapshot(status='archived'))


def test_bridge_accepts_schema_six_but_does_not_claim_it():
    assert SCHEMA_VERSION == 5
    assert [1, 2, 3, 4, 5, 6] in ACCEPTED_SQLITE_VERSIONS and [1, 2, 3, 4, 5, 6] in ACCEPTED_POSTGRES_VERSIONS
    assert [1, 2, 3, 4, 5, 7] not in ACCEPTED_SQLITE_VERSIONS and [1, 2, 3, 4, 5, 7] not in ACCEPTED_POSTGRES_VERSIONS


def test_sqlite_reopens_with_schema_six_ledger(tmp_path):
    path = tmp_path / 'audli.sqlite3'
    repo = ProgressRepository(path, 'a0000000-0000-4000-8000-000000000001')
    with repo.engine.begin() as db:
        db.execute(s.versions.insert().values(version=6, applied_at=now()))
    repo.close()
    reopened = ProgressRepository(path, 'a0000000-0000-4000-8000-000000000001')
    with reopened.engine.connect() as db:
        assert db.execute(s.versions.select().order_by(s.versions.c.version)).fetchall()[-1][0] == 6
    reopened.close()


def test_open_lesson_queries_treat_only_active_and_paused_as_open(tmp_path):
    repo = ProgressRepository(tmp_path / 'audli.sqlite3', 'a0000000-0000-4000-8000-000000000002')
    at = now()
    done = Lesson(id='b0000000-0000-4000-8000-000000000001', started_at=at - timedelta(days=2), active_since=None,
        name='Maya', focus='details', status='completed', phase='COMPLETED', completed_at=at - timedelta(days=2))
    repo.create_lesson(done.model_copy(update={'status': 'active'}))
    repo.save_lesson(done.model_copy(update={'status': 'active'}), 0)
    assert repo.lesson() is not None
    repo.save_lesson(done, 1)
    assert repo.lesson() is None and repo.lesson(completed=True).id == done.id
    repo.close()
