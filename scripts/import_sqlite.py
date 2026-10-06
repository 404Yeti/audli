"""Explicit one-learner SQLite import into an empty configured Postgres learner."""
import argparse
import sqlite3
from pathlib import Path
from sqlalchemy import select, update
from app.config import Settings
from app.repository import ProgressRepository, create_repository
from app.storage import schema as s
from app.storage.repository import profile_values
from app.models import LearnerProfile


def import_learner(source_path: Path, target, source_learner_id='local'):
    if not source_path.is_file():
        raise ValueError('SQLite source file does not exist')
    with sqlite3.connect(f'{source_path.resolve().as_uri()}?mode=ro', uri=True) as db:
        if db.execute('SELECT id FROM users WHERE id=?', (source_learner_id,)).fetchone() is None:
            raise ValueError('Source learner does not exist')
    # An old source is upgraded locally with a consistent backup before import.
    source = ProgressRepository(source_path, source_learner_id)
    try:
        with source.transaction() as origin, target.transaction() as destination:
            destination.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id == target.learner_id).with_for_update()).one()
            if destination.execute(select(s.exercises.c.id).where(s.exercises.c.user_id == target.learner_id)).first():
                raise ValueError('Target learner already has exercises; import will not overwrite or merge progress')
            target_data = destination.execute(select(s.profiles.c.data).where(s.profiles.c.user_id == target.learner_id)).scalar_one()
            if target_data != LearnerProfile().model_dump(mode='json'):
                raise ValueError('Target learner profile is not empty; import will not overwrite it')
            source_profile = origin.execute(select(s.profiles).where(s.profiles.c.user_id == source_learner_id)).mappings().one()
            profile = LearnerProfile.model_validate(source_profile['data'])
            destination.execute(update(s.profiles).where(s.profiles.c.user_id == target.learner_id).values(**profile_values(profile)))
            exercise_ids = select(s.exercises.c.id).where(s.exercises.c.user_id == source_learner_id)
            attempt_ids = select(s.attempts.c.id).where(s.attempts.c.exercise_id.in_(exercise_ids))
            for table in s.metadata.sorted_tables:
                if table in (s.versions, s.users, s.profiles):
                    continue
                if 'user_id' in table.c:
                    query = select(table).where(table.c.user_id == source_learner_id)
                elif 'exercise_id' in table.c:
                    query = select(table).where(table.c.exercise_id.in_(exercise_ids))
                else:
                    query = select(table).where(table.c.attempt_id.in_(attempt_ids))
                rows = origin.execute(query).mappings().all()
                for row in rows:
                    values = dict(row)
                    if 'user_id' in values:
                        values['user_id'] = target.learner_id
                    destination.execute(table.insert().values(**values))
        return profile.completed_attempts
    finally:
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sqlite_path', type=Path)
    parser.add_argument('--source-learner-id', default='local')
    args = parser.parse_args()
    target = None
    try:
        settings = Settings()
        if settings.persistence != 'postgres':
            raise ValueError('Import target must be Postgres')
        target = create_repository(settings)
        count = import_learner(args.sqlite_path, target, args.source_learner_id)
        print(f'Learner import committed; {count} completed assessments retained.')
    except Exception:
        raise SystemExit('Import failed; target progress was not overwritten. Check source, target configuration, schema and learner ownership. Credentials were not logged.') from None
    finally:
        if target is not None:
            target.close()
