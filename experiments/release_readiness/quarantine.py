"""Explicit operator recovery of unknown outcomes. No delivery inference or replay."""
from hashlib import sha256
import json
from sqlalchemy import select,update,func
from app.account import owner_predicate
from app.storage import schema as s
from experiments.durable_claims.store import assert_local,Conflict,slots
from experiments.assessment_claims.schema import operations,TABLES
from experiments.assessment_claims.bridge import advisory


def state_hash(db,owner):
    rows=[]
    for table in TABLES:
        predicate=owner_predicate(table,owner)
        # Generated audio bytes are not authoritative state; hash metadata only.
        columns=[c for c in table.c if c.type.__class__.__name__ not in ('LargeBinary','BYTEA')]
        values=db.execute(select(*columns).where(predicate).order_by(*table.primary_key.columns)).mappings().all()
        rows.append((table.name,[dict(row) for row in values]))
    return sha256(json.dumps(rows,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def inspect_quarantine(repo,identifier):
    assert_local(repo.engine)
    with repo.transaction() as db:
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        op=db.execute(select(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier)).mappings().first()
        if not op:raise Conflict('Operation not found')
        now=db.execute(select(func.clock_timestamp())).scalar_one()
        return dict(kind=op['kind'],expired=op['expires']<=now,status=op['status'],checkpoint=state_hash(db,repo.learner_id),delivery='unconfirmed' if op['status']!='complete' else 'receipt_committed')


def recover(repo,identifier,*,expected_checkpoint,acknowledge_unknown=False):
    """Fences an expired request and preserves *all* canonical learner state.

    This does not reconstruct a lost provider result or pretend delivery succeeded.
    Incoming mutations must be paused, and the operator/learner must explicitly
    accept resuming canonical state. Same operation IDs remain tombstoned.
    """
    assert_local(repo.engine)
    if not acknowledge_unknown:raise Conflict('Explicit acknowledgement required; never automatically recover by revision')
    with repo.transaction() as db:
        advisory(db,repo.learner_id)
        db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==repo.learner_id).with_for_update()).one()
        slot=db.execute(select(slots).where(slots.c.owner==repo.learner_id).with_for_update()).mappings().one()
        op=db.execute(select(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier)).mappings().first()
        if not op or op['kind']!='request':raise Conflict('Use native operation recovery for this kind')
        if op['status']=='complete':raise Conflict('Use canonical operation status; recovery cannot confirm delivery')
        if op['status']!='running' or slot['active']!=identifier or slot['fence']!=op['fence']:raise Conflict('Ownership changed')
        if op['expires']>db.execute(select(func.clock_timestamp())).scalar_one():raise Conflict('Live operation cannot be displaced')
        before=state_hash(db,repo.learner_id)
        if before!=expected_checkpoint:raise Conflict('Canonical state changed; read again without inferring delivery')
        db.execute(update(operations).where(operations.c.owner==repo.learner_id,operations.c.id==identifier).values(status='invalidated'))
        db.execute(update(slots).where(slots.c.owner==repo.learner_id).values(active=None,fence=slots.c.fence+1))
        assert state_hash(db,repo.learner_id)==before
        return {'outcome':'resume_canonical_state','delivery_confirmation':False,'automatic_provider_retry':False}
