"""Audli 2.0 account erasure: everything the 2.0 feature holds about one learner, removed in the caller's transaction.

Covers the learner's attempts, answers, responses, spoken-answer records and any recording still stored, their jobs and generation requests, and every
lesson they generated: versions, exercises, keys, rubrics, audio records and the audio objects in private storage. Shared library lessons are never touched.

Order and failure handling (the account must never be half-deleted):
1. All database rows are deleted first, inside the caller's transaction. On PostgreSQL the immutability guards (migration 013) allow deleting a lesson only
   while `audli.erasing_owner` names the lesson's own owner, which this function sets transaction-locally.
2. Only then are the stored objects deleted (deletion is idempotent). If any object cannot be deleted, ErasureFailed is raised and the caller's transaction
   rolls back, so the account, its rows and the lesson records all remain and the learner can simply retry.
3. The remaining window (objects gone, final commit fails) leaves lessons whose audio is missing, never audio without an owner. A retry completes it.
Aggregate spending counters are not learner data and are kept.
"""
from sqlalchemy import delete, inspect, select, text

from app.listening import schema as t
from app.listening.objects import RECORDING_PREFIX, valid_key


_SQLITE_DELETE_GUARDS = ('listening_version_delete', 'listening_exercise_delete', 'listening_key_delete', 'listening_rubric_delete', 'listening_audio_delete')


def _sqlite_guards_off(db):
    """Local development and tests only (production is PostgreSQL, which uses migration 013): SQLite has no per-transaction setting, so the delete guards are
    dropped inside the erasure transaction and recreated before it ends. SQLite DDL is transactional, so a rollback restores them too."""
    for name in _SQLITE_DELETE_GUARDS:
        db.execute(text(f'DROP TRIGGER IF EXISTS {name}'))


def _sqlite_guards_on(db):
    for statement in t.SQLITE_TRIGGERS:
        if any(f' {name} ' in statement for name in _SQLITE_DELETE_GUARDS):
            db.execute(text(statement))


class ErasureRefused(Exception):
    """Nothing was changed: the data is in a state this code will not erase on its own."""


class ErasureFailed(Exception):
    """Stored objects could not be deleted: the caller must roll back."""


def _lesson_ids(db, owner):
    return list(db.execute(select(t.lesson_owners.c.lesson_id).where(t.lesson_owners.c.user_id == owner)).scalars())


def erase_learner_data(db, owner: str, lesson_objects, recordings) -> dict:
    """Delete the learner's 2.0 data in `db`'s transaction, then its stored objects. Returns counts (no identifiers) for the audit trail."""
    present = set(inspect(db).get_table_names())
    summary = {'lessons': 0, 'attempts': 0, 'objects': 0}
    if t.attempts.name not in present:
        return summary                                                   # 2.0 was never installed here: nothing to erase
    if db.dialect.name == 'postgresql':
        db.execute(text("SELECT set_config('audli.erasing_owner', :owner, true)"), {'owner': str(owner)})
    elif db.dialect.name == 'sqlite':
        _sqlite_guards_off(db)
    generation = t.lesson_owners.name in present
    lessons = _lesson_ids(db, owner) if generation else []
    versions = list(db.execute(select(t.versions.c.id).where(t.versions.c.lesson_id.in_(lessons))).scalars()) if lessons else []

    # Someone else's attempt on this learner's private lesson cannot exist; if it somehow does, refuse rather than delete another person's record.
    if versions and db.execute(select(t.attempts.c.id).where(t.attempts.c.version_id.in_(versions), t.attempts.c.user_id != owner)).first():
        raise ErasureRefused('Another account has activity on one of your lessons; contact Audli support. Nothing was deleted.')

    lesson_keys = [k for k in db.execute(select(t.audio.c.object_key).where(t.audio.c.version_id.in_(versions))).scalars() if k] if versions else []
    if generation and t.generation_requests.name in present:
        lesson_keys += [k for k in db.execute(select(t.generation_requests.c.audio_key).where(t.generation_requests.c.user_id == owner)).scalars() if k]
    recording_keys = [k for k in db.execute(select(t.spoken.c.recording_key).join(t.attempts, t.attempts.c.id == t.spoken.c.attempt_id)
                                            .where(t.attempts.c.user_id == owner)).scalars() if k]
    # A key must belong to the store it is deleted from: recordings only under the recording prefix, lesson audio never under it.
    if any(not valid_key(k) or k.startswith(RECORDING_PREFIX) for k in lesson_keys) or any(not valid_key(k) or not k.startswith(RECORDING_PREFIX) for k in recording_keys):
        raise ErasureRefused('A stored object has an unexpected name; contact Audli support. Nothing was deleted.')

    mine = select(t.attempts.c.id).where(t.attempts.c.user_id == owner)
    for table in (t.responses, t.answers, t.spoken):
        db.execute(delete(table).where(table.c.attempt_id.in_(mine)))
    summary['attempts'] = db.execute(delete(t.attempts).where(t.attempts.c.user_id == owner)).rowcount or 0
    db.execute(delete(t.jobs).where(t.jobs.c.user_id == owner))
    if generation:
        db.execute(delete(t.generation_requests).where(t.generation_requests.c.user_id == owner))
        if versions:
            exercises = select(t.exercises.c.id).where(t.exercises.c.version_id.in_(versions))
            for table in (t.keys, t.rubrics):
                db.execute(delete(table).where(table.c.exercise_id.in_(exercises)))
            db.execute(delete(t.exercises).where(t.exercises.c.version_id.in_(versions)))
            db.execute(delete(t.audio).where(t.audio.c.version_id.in_(versions)))
            db.execute(delete(t.versions).where(t.versions.c.id.in_(versions)))
        if lessons:
            db.execute(delete(t.lesson_owners).where(t.lesson_owners.c.lesson_id.in_(lessons)))
            db.execute(delete(t.lessons).where(t.lessons.c.id.in_(lessons)))
        summary['lessons'] = len(lessons)

    if db.dialect.name == 'sqlite':
        _sqlite_guards_on(db)
    failed = 0
    for store, keys in ((lesson_objects, sorted(set(lesson_keys))), (recordings, sorted(set(recording_keys)))):
        for key in keys:
            try:
                store.delete(key)
                summary['objects'] += 1
            except Exception:
                failed += 1                                              # keep going: one retry should finish as much as possible
    if failed:
        raise ErasureFailed(f'{failed} stored object(s) could not be deleted')
    return summary
