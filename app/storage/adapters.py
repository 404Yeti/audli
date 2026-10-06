"""Engine selection, additive SQLite migration, and fail-closed Postgres startup."""
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event, inspect, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateIndex

from app.models import LearnerProfile, Conversation
from app.storage import schema as s
from app.storage.repository import SQLProgressRepository, profile_values, utcnow, aware, evaluation_values, event_values
from app.models import Evaluation, EvidenceEvaluation, Adaptation


SCHEMA_VERSION = 1


def sqlite_engine(path):
    engine = create_engine('sqlite:///' + str(path), connect_args={'timeout': 20})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    return engine


def initialize_sqlite(repo):
    inspector = inspect(repo.engine)
    legacy = inspector.has_table('learner_profiles') and not inspector.has_table('schema_migrations')
    if legacy:
        # SQLite backup API produces a consistent backup, including WAL contents.
        backup = repo.path.with_suffix('.pre-postgres.sqlite3')
        if backup.exists():
            raise RuntimeError('SQLite migration backup already exists; inspect it before retrying migration')
        with sqlite3.connect(repo.path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
    with repo.transaction() as db:
        existing = set(inspect(db).get_table_names())
        if 'schema_migrations' in existing:
            if db.execute(select(s.versions.c.version)).scalars().all() != [SCHEMA_VERSION]:
                raise RuntimeError('Unsupported SQLite schema version')
            s.metadata.create_all(db)
            return
        # Original tables keep their names/data. Add nullable projection columns,
        # then backfill before exposing the upgraded repository to application code.
        for table in s.metadata.sorted_tables:
            if table.name in existing:
                names = {column['name'] for column in inspect(db).get_columns(table.name)}
                for column in table.columns:
                    if column.name not in names:
                        db.exec_driver_sql(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(dialect=repo.engine.dialect)}')
        s.metadata.create_all(db)
        if legacy:
            for row in db.execute(select(s.profiles)).mappings().all():
                profile = LearnerProfile.model_validate(row['data'])
                if 'onboarding_status' not in row['data'] and (profile.name != 'Listener' or profile.completed_attempts):
                    profile.onboarding_status = 'profile_saved'
                db.execute(update(s.profiles).where(s.profiles.c.user_id == row['user_id']).values(**profile_values(profile)))
            rows = db.exec_driver_sql('SELECT id FROM exercises ORDER BY rowid').fetchall()
            for position, (exercise_id,) in enumerate(rows):
                row = db.execute(select(s.exercises).where(s.exercises.c.id == exercise_id)).mappings().one()
                attempt = db.execute(select(s.attempts).where((s.attempts.c.exercise_id == exercise_id) & (s.attempts.c.status == 'evaluated'))).mappings().first()
                started_at = aware(row['created_at']) + timedelta(microseconds=position)
                completed_at = None
                if attempt:
                    final_time = db.execute(select(s.events.c.created_at).where(s.events.c.attempt_id == attempt['id'])).scalar_one_or_none()
                    completed_at = max(started_at, aware(final_time or attempt['created_at']))
                session_id = str(uuid4())
                db.execute(s.sessions.insert().values(id=session_id, user_id=row['user_id'],
                    status='completed' if attempt else 'active', started_at=started_at,
                    completed_at=completed_at,
                    starting_difficulty=row['difficulty']))
                db.execute(update(s.exercises).where(s.exercises.c.id == exercise_id).values(session_id=session_id, created_at=started_at))
            for row in db.execute(select(s.attempts)).mappings().all():
                transcription = row['transcription']
                db.execute(update(s.attempts).where(s.attempts.c.id == row['id']).values(recognized_text=transcription['text'],
                    confidence=transcription.get('confidence'), transcription_source=transcription.get('source', 'openai')))
            for row in db.execute(select(s.evaluations)).mappings().all():
                model = EvidenceEvaluation if row['data'].get('version') == 'v0.2' else Evaluation
                values = evaluation_values(model.model_validate(row['data']))
                final_time = db.execute(select(s.events.c.created_at).where(s.events.c.attempt_id == row['attempt_id'])).scalar_one_or_none()
                if final_time:
                    values['created_at'] = aware(final_time)
                db.execute(update(s.evaluations).where(s.evaluations.c.attempt_id == row['attempt_id']).values(**values))
            for row in db.execute(select(s.events)).mappings().all():
                values = event_values(Adaptation.model_validate(row['data']))
                values['created_at'] = aware(row['created_at'])
                db.execute(update(s.events).where(s.events.c.id == row['id']).values(**values))
            original_learner = repo.learner_id
            try:
                for row in db.execute(select(s.conversations)).mappings().all():
                    repo.learner_id = db.execute(select(s.exercises.c.user_id).where(s.exercises.c.id == row['exercise_id'])).scalar_one()
                    repo.write_conversation(db, Conversation.model_validate(row['data']))
                for row in db.execute(select(s.exercises.c.user_id, s.exercises.c.audio_name)).mappings().all():
                    path = repo.path.parent / 'exercise_audio' / row['audio_name']
                    if path.is_file():
                        repo.learner_id = row['user_id']
                        repo.save_audio(db, row['audio_name'], path.read_bytes())
                for row in db.execute(select(s.coach_audio.c.audio_name, s.exercises.c.user_id).join(s.exercises)).mappings().all():
                    path = repo.path.parent / 'exercise_audio' / row['audio_name']
                    if path.is_file():
                        repo.learner_id = row['user_id']
                        repo.save_audio(db, row['audio_name'], path.read_bytes())
            finally:
                repo.learner_id = original_learner
        db.execute(s.versions.insert().values(version=SCHEMA_VERSION, applied_at=utcnow()))
        for table in s.metadata.sorted_tables:
            for index in table.indexes:
                db.execute(CreateIndex(index, if_not_exists=True))


class ProgressRepository(SQLProgressRepository):
    """Compatible local adapter; existing ProgressRepository(Path) callers still work."""
    def __init__(self, path: Path, learner_id: str | None = 'local'):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(sqlite_engine(self.path), learner_id)
        try:
            initialize_sqlite(self)
            if self.learner_id is not None:
                self.initialize_learner()
        except Exception:
            self.close()
            raise

    @contextmanager
    def connect(self):
        """Legacy local inspection/test connection; application methods use Core."""
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()


class PostgresRepository(SQLProgressRepository):
    def __init__(self, database_url: str, learner_id: str | None = None):
        engine = None
        try:
            engine = create_engine(make_url(database_url).set(drivername='postgresql+psycopg'),
                pool_pre_ping=True, pool_size=3, max_overflow=0, pool_timeout=10,
                connect_args={'connect_timeout': 10, 'prepare_threshold': None,
                    'options': '-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000'})
            super().__init__(engine, learner_id)
            with engine.connect() as db:
                if db.execute(select(s.versions.c.version)).scalars().all() != [SCHEMA_VERSION]:
                    raise ValueError('Schema version mismatch')
                inspector = inspect(db)
                for table in s.metadata.sorted_tables:
                    names = {column['name'] for column in inspector.get_columns(table.name)}
                    if not set(table.columns.keys()).issubset(names):
                        raise ValueError('Schema is incomplete')
            if self.learner_id is not None:
                self.initialize_learner()
        except Exception:
            if engine is not None:
                engine.dispose()
            raise RuntimeError('Postgres persistence could not start. Check AUDLI_DATABASE_URL, database access, and apply docs/postgres.sql.') from None


def create_repository(settings):
    learner_id = None if settings.auth_mode == 'supabase' else str(settings.learner_id) if settings.learner_id else 'local'
    if settings.persistence == 'postgres':
        return PostgresRepository(settings.database_url.get_secret_value(), learner_id)
    return ProgressRepository(settings.data_dir / 'audli.sqlite3', learner_id)
