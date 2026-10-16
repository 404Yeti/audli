"""Synthetic profiles captured with exact deployed bridge ce55cbc; no live data."""
import hashlib
import json
from pathlib import Path
from app.models import LearnerProfile
from app.storage import schema as s
from app.storage.repository import profile_values
from test_persistence import repository_factory
from test_planner import complete


def test_deployed_profile_roundtrip_and_planner_preserve_typed_name_and_accents(repository_factory):
    repo = repository_factory()
    fixture = json.loads((Path(__file__).parent / 'fixtures/aud40-bridge-profile.json').read_text())
    # Exercise the actual persisted-record boundary, not only model construction.
    repo.profile()
    with repo.engine.begin() as db:
        db.execute(s.profiles.update().where(s.profiles.c.user_id == repo.learner_id).values(**profile_values(LearnerProfile.model_validate(fixture))))
    before = repo.profile()
    assert before == LearnerProfile.model_validate(fixture)
    assert before.onboarding.name_question_revision == 7 and before.onboarding.typed_name
    plan = repo.roadmap()
    assert len(plan.upcoming) == 3
    assert [x.accent_focus for x in plan.upcoming] == ['british', 'australian', 'british']
    for _ in range(3): complete(repo)
    plan = repo.roadmap()
    repo.roadmap(revision=plan.revision, feedback={'status':'answered','response':'More travel please','interests':['travel']})
    after = repo.profile()
    assert after == before
    assert repository_factory().profile() == before
    # Preference is never itself an accepted one-passage consent.
    assert all(x.accent_consent.state == 'none' for x in repo.accent_lesson_history())


def test_migration004_matches_restored_production_rehearsal():
    root = Path(__file__).parents[1]
    migration = (root / 'docs/postgres-004.sql').read_bytes()
    assert migration == (root / 'tests/fixtures/postgres-004.sql').read_bytes()
    assert hashlib.sha256(migration).hexdigest() == 'fced6e1053cffe371d159dd457589aaed37f2cbc8cd8c39c698b0c5fad469c3c'


def test_exact003_to004_upgrade_preserves_history_and_excludes_existing_completions(repository_factory):
    import pytest
    import psycopg
    from app.repository import PostgresRepository
    from app.planner import qualifying_count
    from app.storage.planner import due
    from test_aud40_schema_bridge import snapshot
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL migration gate')
    for _ in range(3): complete(repo)
    with repo.engine.begin() as db:
        for table in reversed(s.HOMEWORK_TABLES): table.drop(db)
        s.roadmaps.drop(db)
        db.execute(s.versions.delete().where(s.versions.c.version >= 4))
    before = snapshot(repo.engine)
    url = repo.engine.url.set(drivername='postgresql').render_as_string(hide_password=False)
    with pytest.raises(RuntimeError, match='schema version validation'):
        PostgresRepository(url)
    assert snapshot(repo.engine) == before
    with psycopg.connect(url, autocommit=True) as db:
        db.execute((Path(__file__).parents[1] / 'docs/postgres-004.sql').read_text())
    after = snapshot(repo.engine)
    assert all(after[name] == rows for name, rows in before.items() if name != 'schema_migrations')
    assert after['learner_roadmaps'] == []
    restarted = PostgresRepository(url, repo.learner_id)
    try:
        roadmap = restarted.roadmap()
        assert len(roadmap.completed_ids) == len(roadmap.baseline_ids) == 3
        assert qualifying_count(roadmap) == 0 and due(roadmap) is None
        assert restarted.accent_lesson_history() == repo.accent_lesson_history()
    finally:
        restarted.close()
