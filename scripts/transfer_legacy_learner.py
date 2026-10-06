"""Operator-only transfer of the temporary shared learner to one verified account.

No HTTP endpoint. Preview by default; explicit --apply is required. The operator
must verify both UUIDs and entitlement outside the browser before using this tool.
"""
import argparse
from uuid import UUID, uuid4

from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import Settings
from app.models import LearnerProfile
from app.repository import create_repository
from app.storage import schema as s
from app.storage.repository import utcnow, profile_values


def transfer_legacy_learner(repository, source_id, target_id, apply=False):
    source_id, target_id = str(UUID(source_id)), str(UUID(target_id))
    if source_id == target_id:
        raise ValueError('Source and target must be different identities')
    with repository.transaction() as db:
        if apply:
            # Materialize the target inside this transaction before taking locks;
            # otherwise an absent target profile could be created concurrently.
            insert = postgres_insert if repository.engine.dialect.name == 'postgresql' else sqlite_insert
            db.execute(insert(s.users).values(id=target_id, created_at=utcnow()).on_conflict_do_nothing())
            db.execute(insert(s.profiles).values(user_id=target_id,
                **profile_values(LearnerProfile())).on_conflict_do_nothing())
        # Lock in deterministic order; never merge or overwrite an account's work.
        rows = db.execute(select(s.profiles).where(s.profiles.c.user_id.in_([source_id, target_id]))
            .order_by(s.profiles.c.user_id).with_for_update()).mappings().all()
        profiles = {row['user_id']: dict(row) for row in rows}
        if source_id not in profiles:
            raise ValueError('Legacy learner does not exist')
        if target_id in profiles and profiles[target_id]['data'] != LearnerProfile().model_dump(mode='json'):
            raise ValueError('Target profile is not empty; transfer refused')
        for table in (s.sessions, s.exercises, s.audio_assets):
            if db.execute(select(table).where(table.c.user_id == target_id)).first():
                raise ValueError('Target has learner resources; transfer refused')
        sessions = db.execute(select(s.sessions).where(s.sessions.c.user_id == source_id)).mappings().all()
        summary = {'sessions': len(sessions), 'completed_attempts': profiles[source_id]['completed_attempts'], 'applied': apply}
        if not apply:
            return summary
        values = profiles[source_id] | {'user_id': target_id}
        repository.upsert(db, s.profiles, values, ['user_id'])
        for session in sessions:
            temporary_id = str(uuid4())
            # Keep original session/exercise IDs and immediate composite FKs valid
            # throughout: move exercises through a private temporary session.
            db.execute(s.sessions.insert().values(**(dict(session) | {'id': temporary_id, 'user_id': target_id})))
            db.execute(update(s.exercises).where((s.exercises.c.session_id == session['id']) &
                (s.exercises.c.user_id == source_id)).values(user_id=target_id, session_id=temporary_id))
            db.execute(update(s.sessions).where(s.sessions.c.id == session['id']).values(user_id=target_id))
            db.execute(update(s.exercises).where(s.exercises.c.session_id == temporary_id).values(session_id=session['id']))
            db.execute(delete(s.sessions).where(s.sessions.c.id == temporary_id))
        db.execute(update(s.audio_assets).where(s.audio_assets.c.user_id == source_id).values(user_id=target_id))
        db.execute(delete(s.profiles).where(s.profiles.c.user_id == source_id))
        db.execute(delete(s.users).where(s.users.c.id == source_id))
        return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-learner-id', type=UUID, required=True)
    parser.add_argument('--account-user-id', type=UUID, required=True)
    parser.add_argument('--apply', action='store_true', help='Commit the reviewed transfer; omit for preview')
    args = parser.parse_args()
    repository = None
    try:
        settings = Settings()
        if settings.auth_mode != 'supabase' or settings.persistence != 'postgres':
            raise ValueError('Transfer requires authenticated Postgres configuration')
        repository = create_repository(settings)
        summary = transfer_legacy_learner(repository, str(args.legacy_learner_id), str(args.account_user_id), args.apply)
        print(f"{'Transferred' if summary['applied'] else 'Preview only'}: {summary['sessions']} sessions, {summary['completed_attempts']} completed assessments.")
    except Exception:
        raise SystemExit('Transfer failed; no partial transfer committed. Check verified account ownership, UUIDs, empty target and database access.') from None
    finally:
        if repository is not None:
            repository.close()
