"""Local operator tool. DB role authorization; no HTTP or client-supplied verdict."""
from uuid import uuid4
from sqlalchemy import select,text,func
from experiments.durable_claims.store import assert_local,slots,Conflict
from experiments.assessment_claims.schema import recovery_audit
from experiments.assessment_claims.store import StageStore
from .quarantine import inspect_quarantine,recover

ROLE='audli_claim_recovery_operator'
REASONS={'worker_terminated','deployment_interrupted','client_cancelled'}


def authorize(db):
    allowed=db.execute(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role AND pg_has_role(current_user,oid,'MEMBER'))"),{'role':ROLE}).scalar_one()
    if not allowed:raise Conflict('Dedicated recovery operator authorization required')
    return db.execute(text('SELECT current_user')).scalar_one()


def execute(repo,*,operation_id,checkpoint=None,acknowledge_unknown=False,reason=None):
    assert_local(repo.engine)
    with repo.transaction() as db:
        actor=authorize(db)
        bound=StageStore(repo,None).repo_bound(db)
        observation=inspect_quarantine(bound,operation_id)
        if checkpoint is None:return observation
        if reason not in REASONS:raise Conflict('Reviewed reason required')
        old=db.execute(select(slots.c.fence).where(slots.c.owner==repo.learner_id)).scalar_one()
        result=recover(bound,operation_id,expected_checkpoint=checkpoint,acknowledge_unknown=acknowledge_unknown)
        new=db.execute(select(slots.c.fence).where(slots.c.owner==repo.learner_id)).scalar_one()
        db.execute(recovery_audit.insert().values(id=str(uuid4()),owner=repo.learner_id,operation=operation_id,
            actor=actor,session_actor=db.execute(text('SELECT session_user')).scalar_one(),
            fence_before=old,fence_after=new,checkpoint=checkpoint,reason=reason,at=func.clock_timestamp()))
        return result


def main():
    import argparse,json,os
    from sqlalchemy import create_engine
    from app.storage.repository import SQLProgressRepository
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation',required=True,type=str)
    parser.add_argument('--checkpoint')
    parser.add_argument('--acknowledge-unknown',action='store_true')
    parser.add_argument('--reason',choices=sorted(REASONS))
    args=parser.parse_args()
    from uuid import UUID
    engine=None
    try:
        operation=str(UUID(args.operation))
        engine=create_engine(os.environ['AUDLI_TEST_POSTGRES_URL'])
        assert_local(engine)
        # Resolve ownership inside the authorized database session, never from a
        # browser UUID or a learner identifier in shell history/process arguments.
        from experiments.assessment_claims.schema import operations
        with engine.begin() as db:
            authorize(db)
            owner=db.execute(select(operations.c.owner).where(operations.c.id==operation)).scalar_one()
        repo=SQLProgressRepository(engine,owner)
        print(json.dumps(execute(repo,operation_id=operation,checkpoint=args.checkpoint,
            acknowledge_unknown=args.acknowledge_unknown,reason=args.reason)))
    except Exception:
        raise SystemExit('Recovery refused; verify authorization, stopped writers and canonical state. No delivery was confirmed.') from None
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':main()
