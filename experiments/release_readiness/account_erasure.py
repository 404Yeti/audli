"""Local claims-aware adapter around existing authenticated account erasure."""
from sqlalchemy import select,delete
from app.account import delete_account
from app.storage import schema as s
from experiments.assessment_claims.schema import operations,recovery_audit
from experiments.durable_claims.store import assert_local,slots,operations as uploads,Conflict
from experiments.assessment_claims.bridge import advisory


def erase(repo,audio_dir):
    assert_local(repo.engine)
    def prepare(db):
        # Existing delete_account locks Auth first, matching request initialization.
        # Advisory -> profile -> slot then serializes admission and erasure.
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        slot=db.execute(select(slots).where(slots.c.owner==repo.learner_id).with_for_update()).mappings().first()
        if slot and slot['active']:raise Conflict('Resolve active or expired ownership before deletion')
        for table in (recovery_audit,uploads,operations,slots):
            db.execute(delete(table).where(table.c.owner==repo.learner_id))
    return delete_account(repo,audio_dir,prepare_claim_erasure=prepare)
