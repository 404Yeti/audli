"""Local lifecycle rehearsal. Tombstone keys survive minimization; erasure removes them."""
from sqlalchemy import select,update,delete,func
from app.storage import schema as s
from app.account import erase_application_data
from experiments.durable_claims.store import assert_local,Conflict,slots,operations as uploads
from experiments.assessment_claims.schema import operations
from experiments.assessment_claims.bridge import advisory


def minimize(repo):
    assert_local(repo.engine)
    with repo.transaction() as db:
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        active=db.execute(select(slots.c.active).where(slots.c.owner==repo.learner_id).with_for_update()).scalar_one_or_none()
        open_lesson=db.execute(select(s.lessons.c.id).where(s.lessons.c.user_id==repo.learner_id,s.lessons.c.status.in_(('active','paused')))).first()
        unfinished=db.execute(select(s.exercises.c.id).where(s.exercises.c.user_id==repo.learner_id,~select(s.attempts.c.id).where(s.attempts.c.exercise_id==s.exercises.c.id,s.attempts.c.status=='evaluated').exists())).first()
        if active or open_lesson or unfinished:raise Conflict('Active/current evidence must not be minimized')
        count=db.execute(update(operations).where(operations.c.owner==repo.learner_id,operations.c.status!='running').values(status='invalidated',snapshot={},input={})).rowcount
        return count


def erase_public_account(repo):
    """Privileged local rehearsal, not a replacement for Supabase Auth revocation."""
    assert_local(repo.engine)
    with repo.transaction() as db:
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        slot=db.execute(select(slots).where(slots.c.owner==repo.learner_id).with_for_update()).mappings().first()
        if slot and slot['active']:
            if not db.execute(select(operations.c.id).where(operations.c.owner==repo.learner_id,operations.c.id==slot['active'])).first():raise Conflict('Unknown ownership; explicit drain review required')
            db.execute(update(slots).where(slots.c.owner==repo.learner_id).values(active=None,fence=slots.c.fence+1))
        db.execute(delete(uploads).where(uploads.c.owner==repo.learner_id))
        db.execute(delete(operations).where(operations.c.owner==repo.learner_id))
        db.execute(delete(slots).where(slots.c.owner==repo.learner_id))
        erase_application_data(db,repo.learner_id)
