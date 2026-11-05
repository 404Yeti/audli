"""2.0 listening tables (migration 009). A separate MetaData on purpose: the 1.x schema inventory, claims guards and the
account-erasure plan enumerate ``app.storage.schema.metadata`` and must not change. Learner rows cascade from ``users`` so
account deletion stays complete without touching that plan; lesson content is shared catalog data and is never learner-owned.
"""
from datetime import datetime, timezone
from sqlalchemy import select, insert
from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, Boolean, ForeignKey, ForeignKeyConstraint,
                        PrimaryKeyConstraint, UniqueConstraint, CheckConstraint, Index, BigInteger)

from app.storage.schema import document, identifier, timestamp

metadata = MetaData()
# Reference-only stub so foreign keys resolve. Never created or migrated here: 1.x owns the users table.
users_ref = Table('users', metadata, Column('id', identifier, primary_key=True))

# Own ledger: 1.x's schema_migrations.max(version) gates claims startup (app/claims/runtime.py), so 009 must not be recorded there.
ledger = Table('listening_migrations', metadata,
    Column('version', Integer, primary_key=True, autoincrement=False), Column('applied_at', timestamp, nullable=False))

lessons = Table('listening_lessons', metadata,
    Column('id', identifier, primary_key=True), Column('slug', String(80), nullable=False, unique=True),
    Column('created_at', timestamp, nullable=False))

versions = Table('listening_lesson_versions', metadata,
    Column('id', identifier, primary_key=True),
    Column('lesson_id', identifier, ForeignKey('listening_lessons.id'), nullable=False),
    Column('version_no', Integer, nullable=False), Column('status', String(10), nullable=False),
    Column('content_hash', String(64), nullable=False),
    Column('title', String(120), nullable=False), Column('topic', String(120), nullable=False),
    Column('language', String(8), nullable=False), Column('cefr', String(2), nullable=False),
    Column('listen_for', String(200), nullable=False),
    Column('speakers', document, nullable=False), Column('sentences', document, nullable=False),
    Column('created_at', timestamp, nullable=False), Column('published_at', timestamp),
    UniqueConstraint('lesson_id', 'version_no', name='listening_version_number'),
    UniqueConstraint('lesson_id', 'content_hash', name='listening_version_content'),
    CheckConstraint("status IN ('draft','published')", name='listening_version_status'),
    CheckConstraint("version_no >= 1 AND ((status = 'published') = (published_at IS NOT NULL))", name='listening_version_published'))

exercises = Table('listening_exercises', metadata,
    Column('id', identifier, primary_key=True),
    Column('version_id', identifier, ForeignKey('listening_lesson_versions.id'), nullable=False),
    Column('position', Integer, nullable=False), Column('kind', String(20), nullable=False),
    Column('prompt', Text, nullable=False), Column('options', document, nullable=False),
    UniqueConstraint('version_id', 'position', name='listening_exercise_position'),
    UniqueConstraint('id', 'version_id', name='listening_exercise_version'),
    # 009 shipped this check as multiple_choice only; 010 widens it for the four-exercise journey (see migrations.py).
    CheckConstraint("kind IN ('multiple_choice','spoken','fill_blank','sequencing') AND position >= 1", name='listening_exercise_kind'))

# Private: only the scoring path reads this table; no learner response model contains these columns before an answer exists.
keys = Table('listening_exercise_keys', metadata,
    Column('exercise_id', identifier, ForeignKey('listening_exercises.id'), primary_key=True),
    Column('correct_option_id', String(32), nullable=False), Column('explanation', Text, nullable=False))

audio = Table('listening_audio_variants', metadata,
    Column('id', identifier, primary_key=True),
    Column('version_id', identifier, ForeignKey('listening_lesson_versions.id'), nullable=False),
    Column('accent', String(30), nullable=False), Column('voice', String(60), nullable=False),
    Column('provider', String(30), nullable=False), Column('object_key', String(200), nullable=False),  # content-addressed: versions with identical audio share one object
    Column('content_type', String(40), nullable=False), Column('sha256', String(64), nullable=False),
    Column('size_bytes', Integer, nullable=False), Column('duration_ms', Integer, nullable=False),
    Column('alignment', document, nullable=False), Column('created_at', timestamp, nullable=False),
    UniqueConstraint('version_id', 'accent', 'voice', name='listening_audio_variant'),
    CheckConstraint('size_bytes > 0 AND duration_ms > 0', name='listening_audio_positive'))

attempts = Table('listening_attempts', metadata,
    Column('id', identifier, primary_key=True),
    Column('user_id', identifier, ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    Column('version_id', identifier, ForeignKey('listening_lesson_versions.id'), nullable=False),
    Column('audio_variant_id', identifier, ForeignKey('listening_audio_variants.id'), nullable=False),
    Column('client_request_id', String(64), nullable=False), Column('status', String(12), nullable=False),
    Column('total', Integer, nullable=False), Column('correct', Integer),
    Column('created_at', timestamp, nullable=False), Column('completed_at', timestamp),
    UniqueConstraint('user_id', 'client_request_id', name='listening_attempt_request'),
    UniqueConstraint('id', 'version_id', name='listening_attempt_version'),
    CheckConstraint("status IN ('in_progress','completed')", name='listening_attempt_status'),
    CheckConstraint("total >= 1 AND ((status = 'completed') = (completed_at IS NOT NULL AND correct IS NOT NULL)) "
                    "AND (correct IS NULL OR (correct >= 0 AND correct <= total))", name='listening_attempt_state'))
Index('listening_one_open_attempt', attempts.c.user_id, attempts.c.version_id, unique=True,
      sqlite_where=attempts.c.status == 'in_progress', postgresql_where=attempts.c.status == 'in_progress')
Index('listening_audio_by_object', audio.c.object_key)
Index('listening_attempts_by_learner', attempts.c.user_id, attempts.c.created_at)

responses = Table('listening_responses', metadata,
    Column('attempt_id', identifier, nullable=False), Column('exercise_id', identifier, nullable=False),
    Column('version_id', identifier, nullable=False),
    Column('option_id', String(32), nullable=False), Column('is_correct', Boolean, nullable=False),
    Column('submitted_at', timestamp, nullable=False),
    # Composite keys make the database refuse an answer to a question from a different version than the attempt's.
    ForeignKeyConstraint(['attempt_id', 'version_id'], ['listening_attempts.id', 'listening_attempts.version_id'],
                         ondelete='CASCADE', name='listening_response_attempt'),
    ForeignKeyConstraint(['exercise_id', 'version_id'], ['listening_exercises.id', 'listening_exercises.version_id'],
                         name='listening_response_exercise'),
    UniqueConstraint('attempt_id', 'exercise_id', name='listening_response_once'))

# ---- Milestone 02 (migration 010): fill-in-the-blank, sequencing, spoken response and the durable job queue ------------------

SINCE = {'since': 10}
# Private, like listening_exercise_keys: accepted blank answers, the correct order and the spoken key points. Only the scoring path
# and the assessment worker read it; a learner response model never contains these columns before the learner has answered.
rubrics = Table('listening_exercise_rubrics', metadata,
    Column('exercise_id', identifier, ForeignKey('listening_exercises.id'), primary_key=True),
    Column('data', document, nullable=False), info=SINCE)

# Deterministic answers other than multiple choice (fill in the blank, sequencing). Same composite keys as listening_responses.
answers = Table('listening_answers', metadata,
    Column('attempt_id', identifier, nullable=False), Column('exercise_id', identifier, nullable=False),
    Column('version_id', identifier, nullable=False), Column('answer', document, nullable=False),
    Column('is_correct', Boolean, nullable=False), Column('submitted_at', timestamp, nullable=False),
    ForeignKeyConstraint(['attempt_id', 'version_id'], ['listening_attempts.id', 'listening_attempts.version_id'],
                         ondelete='CASCADE', name='listening_answer_attempt'),
    ForeignKeyConstraint(['exercise_id', 'version_id'], ['listening_exercises.id', 'listening_exercises.version_id'],
                         name='listening_answer_exercise'),
    UniqueConstraint('attempt_id', 'exercise_id', name='listening_answer_once'), info=SINCE)

# One row per attempt+exercise. 'receiving' is claimed before the recording is stored so an abandoned upload can always be found
# and cleaned; the temporary recording key is cleared the moment the audio is deleted. The transcript is the learner's own words.
spoken = Table('listening_spoken', metadata,
    Column('attempt_id', identifier, nullable=False), Column('exercise_id', identifier, nullable=False),
    Column('version_id', identifier, nullable=False), Column('status', String(10), nullable=False),
    Column('generation', Integer, nullable=False), Column('upload_id', String(64)),
    Column('recording_key', String(200)), Column('recording_type', String(40)),
    Column('transcript', Text), Column('assessment', document), Column('failure_code', String(40)),
    Column('created_at', timestamp, nullable=False), Column('updated_at', timestamp, nullable=False),
    PrimaryKeyConstraint('attempt_id', 'exercise_id', name='listening_spoken_pk'),
    ForeignKeyConstraint(['attempt_id', 'version_id'], ['listening_attempts.id', 'listening_attempts.version_id'],
                         ondelete='CASCADE', name='listening_spoken_attempt'),
    ForeignKeyConstraint(['exercise_id', 'version_id'], ['listening_exercises.id', 'listening_exercises.version_id'],
                         name='listening_spoken_exercise'),
    CheckConstraint("status IN ('skipped','receiving','pending','assessed','failed')", name='listening_spoken_status'),
    CheckConstraint('generation >= 0', name='listening_spoken_generation'), info=SINCE)
Index('listening_spoken_by_status', spoken.c.status, spoken.c.updated_at)

# Durable jobs for the separate worker process. A claim bumps `fence`; every later write by that worker is conditional on it, so a
# worker that lost its lease can never overwrite the work of the one that took over.
jobs = Table('listening_jobs', metadata,
    Column('id', identifier, primary_key=True),
    Column('user_id', identifier, ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    Column('kind', String(20), nullable=False), Column('dedupe_key', String(120), nullable=False, unique=True),
    Column('payload', document, nullable=False), Column('status', String(10), nullable=False),
    Column('attempts', Integer, nullable=False), Column('max_attempts', Integer, nullable=False),
    Column('run_after', timestamp, nullable=False), Column('lease_owner', String(64)), Column('lease_expires_at', timestamp),
    Column('fence', Integer, nullable=False), Column('last_error', String(60)),
    Column('created_at', timestamp, nullable=False), Column('updated_at', timestamp, nullable=False), Column('finished_at', timestamp),
    CheckConstraint("status IN ('queued','running','succeeded','failed')", name='listening_job_status'),
    CheckConstraint('attempts >= 0 AND max_attempts >= 1 AND fence >= 0', name='listening_job_counts'), info=SINCE)
Index('listening_jobs_ready', jobs.c.status, jobs.c.run_after)

# ---- Milestone 04 (migration 011): the global AI spending ceiling and kill switch ------------------------------------------------
# One row per UTC day ('2026-11-04') counts what has been RESERVED for AI calls that day, in millionths of a US dollar; the row 'control'
# carries the operator's halt flag. Reservations are conditional UPDATEs, so concurrent workers can never reserve past the ceiling.
# These are estimates, not billing: the provider's own spend limit stays the real backstop.
ai_limits = Table('listening_ai_limits', metadata,
    Column('scope', String(16), primary_key=True), Column('reserved_micro', BigInteger, nullable=False),
    Column('calls', Integer, nullable=False), Column('halted', Boolean, nullable=False), Column('updated_at', timestamp, nullable=False),
    CheckConstraint('reserved_micro >= 0 AND calls >= 0', name='listening_ai_limits_nonnegative'), info={'since': 11})

OWNED = [t for t in metadata.sorted_tables if t is not users_ref]
NAMES = tuple(t.name for t in OWNED)
NAMES_010 = tuple(t.name for t in OWNED if t.info.get('since') == 10)
NAMES_011 = tuple(t.name for t in OWNED if t.info.get('since') == 11)
PRIVATE_TABLES = ('listening_exercise_keys', 'listening_exercise_rubrics')

_MSG = {'answer': 'listening answers are final', 'version': 'published listening versions are immutable', 'audio': 'listening audio variants are append-only',
        'response': 'listening answers are final', 'attempt': 'listening attempt identity is fixed and completed attempts are final'}

POSTGRES_TRIGGERS = [
    f"""CREATE FUNCTION listening_guard_version() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF OLD.status = 'published' THEN RAISE EXCEPTION '{_MSG['version']}' USING ERRCODE = '23000'; END IF;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $f$;""",
    "CREATE TRIGGER listening_version_guard BEFORE UPDATE OR DELETE ON listening_lesson_versions FOR EACH ROW EXECUTE FUNCTION listening_guard_version();",
    f"""CREATE FUNCTION listening_guard_children() RETURNS trigger LANGUAGE plpgsql AS $f$
DECLARE vid uuid; st text;
BEGIN
    FOR i IN 0..1 LOOP
        IF (i = 0 AND TG_OP = 'INSERT') OR (i = 1 AND TG_OP = 'DELETE') THEN CONTINUE; END IF;
        IF TG_TABLE_NAME = 'listening_exercises' THEN
            vid := CASE WHEN i = 0 THEN OLD.version_id ELSE NEW.version_id END;
        ELSE
            SELECT version_id INTO vid FROM listening_exercises WHERE id = CASE WHEN i = 0 THEN OLD.exercise_id ELSE NEW.exercise_id END;
        END IF;
        SELECT status INTO st FROM listening_lesson_versions WHERE id = vid;
        IF st = 'published' THEN RAISE EXCEPTION '{_MSG['version']}' USING ERRCODE = '23000'; END IF;
    END LOOP;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $f$;""",
    "CREATE TRIGGER listening_exercise_guard BEFORE INSERT OR UPDATE OR DELETE ON listening_exercises FOR EACH ROW EXECUTE FUNCTION listening_guard_children();",
    "CREATE TRIGGER listening_key_guard BEFORE INSERT OR UPDATE OR DELETE ON listening_exercise_keys FOR EACH ROW EXECUTE FUNCTION listening_guard_children();",
    f"""CREATE FUNCTION listening_guard_audio() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN RAISE EXCEPTION '{_MSG['audio']}' USING ERRCODE = '23000'; END $f$;""",
    "CREATE TRIGGER listening_audio_guard BEFORE UPDATE OR DELETE ON listening_audio_variants FOR EACH ROW EXECUTE FUNCTION listening_guard_audio();",
    f"""CREATE FUNCTION listening_guard_response() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN RAISE EXCEPTION '{_MSG['response']}' USING ERRCODE = '23000'; END $f$;""",
    "CREATE TRIGGER listening_response_guard BEFORE UPDATE ON listening_responses FOR EACH ROW EXECUTE FUNCTION listening_guard_response();",
    f"""CREATE FUNCTION listening_guard_attempt() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF OLD.status = 'completed' OR NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.version_id IS DISTINCT FROM OLD.version_id
       OR NEW.audio_variant_id IS DISTINCT FROM OLD.audio_variant_id OR NEW.client_request_id IS DISTINCT FROM OLD.client_request_id
       OR NEW.total IS DISTINCT FROM OLD.total THEN
        RAISE EXCEPTION '{_MSG['attempt']}' USING ERRCODE = '23000';
    END IF;
    RETURN NEW;
END $f$;""",
    "CREATE TRIGGER listening_attempt_guard BEFORE UPDATE ON listening_attempts FOR EACH ROW EXECUTE FUNCTION listening_guard_attempt();",
]
# Migration 010 reuses the 009 functions: rubrics follow the same published-version rule as the keys, answers are as final as responses.
POSTGRES_TRIGGERS_010 = [
    "CREATE TRIGGER listening_rubric_guard BEFORE INSERT OR UPDATE OR DELETE ON listening_exercise_rubrics FOR EACH ROW EXECUTE FUNCTION listening_guard_children();",
    "CREATE TRIGGER listening_answer_guard BEFORE UPDATE ON listening_answers FOR EACH ROW EXECUTE FUNCTION listening_guard_response();",
]

SQLITE_TRIGGERS = [
    f"CREATE TRIGGER IF NOT EXISTS listening_version_update BEFORE UPDATE ON listening_lesson_versions WHEN OLD.status = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_version_delete BEFORE DELETE ON listening_lesson_versions WHEN OLD.status = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_exercise_insert BEFORE INSERT ON listening_exercises WHEN (SELECT status FROM listening_lesson_versions WHERE id = NEW.version_id) = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_exercise_update BEFORE UPDATE ON listening_exercises WHEN (SELECT status FROM listening_lesson_versions WHERE id IN (OLD.version_id, NEW.version_id)) = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_exercise_delete BEFORE DELETE ON listening_exercises WHEN (SELECT status FROM listening_lesson_versions WHERE id = OLD.version_id) = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_key_insert BEFORE INSERT ON listening_exercise_keys WHEN (SELECT v.status FROM listening_exercises e JOIN listening_lesson_versions v ON v.id = e.version_id WHERE e.id = NEW.exercise_id) = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_key_update BEFORE UPDATE ON listening_exercise_keys WHEN (SELECT v.status FROM listening_exercises e JOIN listening_lesson_versions v ON v.id = e.version_id WHERE e.id = OLD.exercise_id) = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_key_delete BEFORE DELETE ON listening_exercise_keys WHEN (SELECT v.status FROM listening_exercises e JOIN listening_lesson_versions v ON v.id = e.version_id WHERE e.id = OLD.exercise_id) = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END",
    *[f"CREATE TRIGGER IF NOT EXISTS listening_rubric_{op.lower()} BEFORE {op} ON listening_exercise_rubrics WHEN (SELECT v.status FROM listening_exercises e JOIN listening_lesson_versions v ON v.id = e.version_id WHERE e.id = {'NEW' if op == 'INSERT' else 'OLD'}.exercise_id) = 'published' BEGIN SELECT RAISE(ABORT, '{_MSG['version']}'); END" for op in ('INSERT', 'UPDATE', 'DELETE')],
    f"CREATE TRIGGER IF NOT EXISTS listening_answer_update BEFORE UPDATE ON listening_answers BEGIN SELECT RAISE(ABORT, '{_MSG['answer']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_audio_update BEFORE UPDATE ON listening_audio_variants BEGIN SELECT RAISE(ABORT, '{_MSG['audio']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_audio_delete BEFORE DELETE ON listening_audio_variants BEGIN SELECT RAISE(ABORT, '{_MSG['audio']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_response_update BEFORE UPDATE ON listening_responses BEGIN SELECT RAISE(ABORT, '{_MSG['response']}'); END",
    f"CREATE TRIGGER IF NOT EXISTS listening_attempt_update BEFORE UPDATE ON listening_attempts WHEN OLD.status = 'completed' OR NEW.user_id IS NOT OLD.user_id OR NEW.version_id IS NOT OLD.version_id OR NEW.audio_variant_id IS NOT OLD.audio_variant_id OR NEW.client_request_id IS NOT OLD.client_request_id OR NEW.total IS NOT OLD.total BEGIN SELECT RAISE(ABORT, '{_MSG['attempt']}'); END",
]


def ensure_sqlite(engine) -> None:
    """Local/test only. Production Postgres is changed solely by the reviewed numbered migration."""
    with engine.begin() as db:
        existing = {row[0] for row in db.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'listening_exercises' in existing and 'listening_spoken' not in existing:
            # create_all would add the new tables, but SQLite cannot widen the old kind CHECK in place.
            raise RuntimeError('This local listening database predates Milestone 02. Delete the local SQLite file and start again.')
        metadata.create_all(db, tables=OWNED)
        for statement in SQLITE_TRIGGERS:
            db.exec_driver_sql(statement)
        have = {row[0] for row in db.execute(select(ledger.c.version))}
        for version in (9, 10, 11):
            if version not in have:
                db.execute(insert(ledger).values(version=version, applied_at=datetime.now(timezone.utc)))
