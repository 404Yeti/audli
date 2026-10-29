"""Release storage preserves schema-3/4 state and known bridge snapshots."""
import pytest
from sqlalchemy import text
from pydantic import ValidationError
from app.lesson import Lesson, now, prompt
from app.models import ExerciseContent
from app.repository import PostgresRepository
from app.storage import schema as s
from test_persistence import repository_factory, test_full_evidence_loop_survives_restart_and_empty_audio_cache as evidence_loop


def test_release_restart_on_schema3_preserves_profiles_and_rejects_future_schema(repository_factory):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL startup bridge; SQLite migration remains unchanged')
    profile = repo.profile()
    profile.name = 'Bridge fixture'
    profile.accent_preferences.accents = ['british']
    profile.accent_preferences.status = 'preferred'
    repo.save_profile(profile)
    with repo.engine.begin() as db:
        before = db.execute(text('SELECT data::text FROM learner_profiles')).scalar_one()
        assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one() in (3, 4, 5, 6, 7)
    for _ in range(2):
        restarted = PostgresRepository(repo.engine.url.render_as_string(hide_password=False))
        assert restarted.for_learner(repo.learner_id).profile() == profile
        with restarted.engine.connect() as db:
            assert db.execute(text('SELECT data::text FROM learner_profiles')).scalar_one() == before
        restarted.close()
    # AUD-47/AUD-77: the fixture database is already at schema 7, which startup accepts.
    accepted = PostgresRepository(repo.engine.url.render_as_string(hide_password=False))
    assert accepted.for_learner(repo.learner_id).profile() == profile
    accepted.close()
    with repo.engine.begin() as db:
        db.execute(s.versions.insert().values(version=9, applied_at=now()))
    with pytest.raises(RuntimeError, match='schema version validation'):
        PostgresRepository(repo.engine.url.render_as_string(hide_password=False))


def test_known_accent_snapshot_fields_survive_bridge_without_practice(exercise):
    at = now()
    fields = dict(accent_offered=None, accent_discovery=False,
        accent_discovery_resolved=True, accent_resolved=True,
        accent_consent=dict(state='consumed', accent='british', accepted_at=at.isoformat(), exercise_number=0),
        accent_confirmation=False, accent_topic_review_pending=False,
        passage_accent=None, passage_fallback=False)
    snapshot = dict(id='fixture', started_at=at.isoformat(), active_since=at.isoformat(),
        name='Maya', focus='details', **fields)
    lesson = Lesson.model_validate(snapshot)
    assert 'How’s your day' in prompt(lesson)
    assert Lesson.model_validate(lesson.model_dump(mode='json')).accent_consent == lesson.accent_consent
    content = exercise.model_dump(mode='json')
    content['passage_voice'] = dict(accent='default', provider='default', voice_id='default', validation=None, fallback=False)
    assert ExerciseContent.model_validate(content).model_dump(mode='json')['passage_voice'] == content['passage_voice']
    with pytest.raises(ValidationError):
        Lesson.model_validate({**snapshot, 'unknown_future_state': True})
    with pytest.raises(ValidationError):
        ExerciseContent.model_validate({**content, 'unknown_future_state': True})


def test_schema3_assessment_adaptation_replay_and_restart(repository_factory, tmp_path, audio_bytes):
    repo = repository_factory()
    if repo.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL schema-3 assessment compatibility')
    with repo.engine.begin() as db:
        assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one() in (3, 4, 5, 6, 7)
    # Run the existing evidence-loop contract on schema 3, including restart,
    # persisted evaluation, deterministic adaptation and gated replay.
    evidence_loop(repository_factory, tmp_path, audio_bytes)
