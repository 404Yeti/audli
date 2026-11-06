"""Audli 2.0 account erasure: everything the 2.0 feature holds about one learner, removed in three steps that never hold a database transaction open
across storage calls.

Covers the learner's attempts, answers, responses, spoken-answer records and any recording still stored, their jobs and generation requests, and every
lesson they generated: versions, exercises, keys, rubrics, audio records and the audio objects in private storage. Shared library lessons are never touched.

1. PLAN (short read): list every stored object that belongs only to this learner and refuse, changing nothing, if anything is unexpected (another account's
   activity on a private lesson, an object name outside its store's prefix).
2. OBJECTS (no transaction, no lock on any row): delete the objects. Deletion is idempotent, so a retry simply repeats it. Recordings go first (temporary
   anyway), then lesson audio. If ANY object cannot be deleted we stop with ErasureFailed before touching the database: the account is intact and the learner
   is told to retry. Object deletion cannot be rolled back, so the one honest consequence of a partial failure is that some generated lessons may have lost
   their audio until the retry completes; nothing is ever reported as deleted.
3. ROWS (the caller's short transaction, with the account row lock): re-derive the object list; if it contains anything the plan did not delete (a lesson
   finished generating, or a recording arrived, in between) raise ErasureChanged and roll back so the learner retries; otherwise delete the rows. On PostgreSQL
   the immutability guards (migration 013) allow deleting a lesson only while `audli.erasing_owner` names the lesson's own owner, set transaction-locally.
Only a committed transaction is reported as success. Aggregate spending counters are not learner data and are kept.
"""
from dataclasses import dataclass

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
    """Stored objects could not all be deleted. The database was not touched; retry."""


class ErasureChanged(Exception):
    """Something new appeared between deleting the objects and deleting the rows. The transaction rolls back; retry."""


@dataclass(frozen=True)
class ErasurePlan:
    lesson_keys: frozenset
    recording_keys: frozenset


def _present(db) -> set:
    return set(inspect(db).get_table_names())


def plan_erasure(db, owner: str) -> ErasurePlan:
    """Read-only. Raises ErasureRefused if anything is unexpected."""
    present = _present(db)
    if t.attempts.name not in present:
        return ErasurePlan(frozenset(), frozenset())               # 2.0 was never installed here
    generation = t.lesson_owners.name in present
    lessons = list(db.execute(select(t.lesson_owners.c.lesson_id).where(t.lesson_owners.c.user_id == owner)).scalars()) if generation else []
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
    return ErasurePlan(frozenset(lesson_keys), frozenset(recording_keys))


def delete_objects(plan: ErasurePlan, lesson_objects, recordings) -> int:
    """Delete the planned objects (idempotent): recordings first, then lesson audio. Within a store every object is tried; if any failed we stop BEFORE the next
    store, so a recording outage never costs the learner their lesson audio. Raises ErasureFailed; the database has not been touched."""
    done = 0
    for store, keys in ((recordings, sorted(plan.recording_keys)), (lesson_objects, sorted(plan.lesson_keys))):
        failed = 0
        for key in keys:
            try:
                store.delete(key)
                done += 1
            except Exception:
                failed += 1
        if failed:
            raise ErasureFailed(f'{failed} stored object(s) could not be deleted')
    return done


def erase_rows(db, owner: str, plan: ErasurePlan) -> dict:
    """In the caller's (short) transaction. Raises ErasureChanged if the learner now has stored objects the plan did not delete."""
    present = _present(db)
    summary = {'lessons': 0, 'attempts': 0}
    if t.attempts.name not in present:
        return summary
    current = plan_erasure(db, owner)
    if not (current.lesson_keys <= plan.lesson_keys and current.recording_keys <= plan.recording_keys):
        raise ErasureChanged('New data appeared while deleting; nothing was deleted from your account. Please try again.')
    if db.dialect.name == 'postgresql':
        db.execute(text("SELECT set_config('audli.erasing_owner', :owner, true)"), {'owner': str(owner)})
    elif db.dialect.name == 'sqlite':
        _sqlite_guards_off(db)
    generation = t.lesson_owners.name in present
    lessons = list(db.execute(select(t.lesson_owners.c.lesson_id).where(t.lesson_owners.c.user_id == owner)).scalars()) if generation else []
    versions = list(db.execute(select(t.versions.c.id).where(t.versions.c.lesson_id.in_(lessons))).scalars()) if lessons else []
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
    return summary


class Eraser:
    """What account deletion talks to. Stores are built on first use so deletion works (or refuses safely) whether or not 2.0 is switched on."""
    def __init__(self, build_stores):
        self._build, self._stores = build_stores, None

    def stores(self):
        if self._stores is None:
            try:
                self._stores = self._build()
            except Exception:
                raise ErasureRefused('Private storage is not available right now, so nothing was deleted. Please try again later.') from None
        return self._stores

    def prepare(self, engine, owner: str) -> ErasurePlan:
        """Steps 1 and 2: plan, then delete the objects, with no transaction held open. Blocking: call from a worker thread."""
        with engine.connect() as db:
            plan = plan_erasure(db, owner)
        if plan.lesson_keys or plan.recording_keys:
            lesson_objects, recordings = self.stores()
            delete_objects(plan, lesson_objects, recordings)
        return plan

    def rows(self, db, owner: str, plan: ErasurePlan) -> dict:
        return erase_rows(db, owner, plan)
