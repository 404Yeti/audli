"""A receipt shares its begin-hook fence, never another transaction's proof."""
from dataclasses import replace

import pytest
from sqlalchemy import event,select,text,update
from sqlalchemy.exc import DBAPIError,NoResultFound

from experiments.assessment_claims.test_stage import fixture
from app.claims.assessment_claims.bridge import BridgeStore,writer,fence,install_transactions
from app.claims.assessment_claims.schema import operations
from app.claims.durable_claims.store import Conflict,slots
from app.storage import schema as s


def claim(fixture):
    st,*_=fixture;install_transactions(st.repo.engine);store=BridgeStore(st.repo)
    _,_,context=store.claim('synthetic-audio','a'*64)
    return st,store,context


def test_fence_once_per_connection_transaction_and_again_after_commit(fixture):
    st,store,context=claim(fixture);clocks=[]
    def observe(db,cursor,statement,*args):
        if statement.startswith('SELECT clock_timestamp'):clocks.append(True)
    event.listen(st.repo.engine,'before_cursor_execute',observe)
    token=writer.set(context)
    try:
        for expected in [1,2]:
            with st.repo.transaction() as db:
                fence(db,context);fence(db,context)
                assert len(clocks)==expected
    finally:writer.reset(token);event.remove(st.repo.engine,'before_cursor_execute',observe)
    store.finish(context,{'status':200,'body':{'audio_cached':True},'stream':True})
    assert st.repo.engine.pool.checkedout()==0


def test_fence_proof_does_not_survive_rollback_or_expiry(fixture):
    st,store,context=claim(fixture)
    token=writer.set(context)
    try:
        with pytest.raises(RuntimeError):
            with st.repo.transaction() as db:
                fence(db,context);raise RuntimeError('synthetic rollback')
    finally:writer.reset(token)
    with st.repo.engine.begin() as db:
        db.execute(update(operations).where(operations.c.id==context.identifier).values(expires=text("clock_timestamp()-interval '1 second'")))
    token=writer.set(context)
    try:
        with pytest.raises(Conflict):
            with st.repo.transaction() as db:fence(db,context)
    finally:writer.reset(token)
    assert st.repo.engine.pool.checkedout()==0


def test_changed_context_cannot_use_cached_fence(fixture):
    st,store,context=claim(fixture);token=writer.set(context)
    try:
        with st.repo.transaction() as db:
            previous=context.fence
            context.fence+=1
            try:
                with pytest.raises(Conflict):fence(db,context)
            finally:context.fence=previous
            with pytest.raises((Conflict,NoResultFound)):fence(db,replace(context,owner=fixture[2].learner_id))
    finally:writer.reset(token)


def test_sql_guard_rejects_expiry_during_already_fenced_transaction(fixture):
    st,store,context=claim(fixture);before=st.repo.profile();token=writer.set(context)
    try:
        with pytest.raises(DBAPIError):
            with st.repo.transaction() as db:
                db.execute(update(operations).where(operations.c.id==context.identifier).values(expires=text("clock_timestamp()-interval '1 second'")))
                fence(db,context)  # Same transaction proof; the write guard must still reject expiry.
                db.execute(update(s.profiles).where(s.profiles.c.user_id==st.owner).values(name='Must not persist'))
    finally:writer.reset(token)
    assert st.repo.profile()==before


@pytest.mark.parametrize('method',['finish','reject'])
def test_receipt_write_checks_live_expiry_even_after_cached_fence(fixture,method,monkeypatch):
    st,store,context=claim(fixture);token=writer.set(context)
    # Make expiry happen after begin's fence and before the internal receipt UPDATE.
    original=fence
    def expire_after_validation(db,value):
        original(db,value)
        db.execute(update(operations).where(operations.c.id==value.identifier).values(expires=text("clock_timestamp()-interval '1 second'")))
    monkeypatch.setattr('app.claims.assessment_claims.bridge.fence',expire_after_validation)
    try:
        with pytest.raises(Conflict):
            if method=='finish':store.finish(context,{'status':200,'body':{'audio_cached':True}})
            else:store.reject(context)
    finally:writer.reset(token)
    with st.repo.read() as db:
        row=db.execute(select(operations.c.status).where(operations.c.id==context.identifier)).scalar_one()
        active=db.execute(select(slots.c.active).where(slots.c.owner==st.owner)).scalar_one()
    assert row=='running' and active==context.identifier  # Rollback preserves quarantined ownership.
    assert st.repo.engine.pool.checkedout()==0


def test_savepoint_rollback_requires_new_fence_proof(fixture):
    st,store,context=claim(fixture);clocks=[]
    def observe(db,cursor,statement,*args):
        if statement.startswith('SELECT clock_timestamp'):clocks.append(True)
    event.listen(st.repo.engine,'before_cursor_execute',observe)
    try:
        # No automatic writer context: the first proof is acquired inside the savepoint.
        with st.repo.transaction() as db:
            nested=db.begin_nested()
            fence(db,context);assert len(clocks)==1
            nested.rollback()
            fence(db,context);assert len(clocks)==2
    finally:event.remove(st.repo.engine,'before_cursor_execute',observe)
    assert st.repo.engine.pool.checkedout()==0


def test_invalidated_connection_cleanup_does_not_reconnect(fixture):
    from sqlalchemy.exc import PendingRollbackError
    st,store,context=claim(fixture);connected=[]
    def checkout(*args):connected.append(True)
    event.listen(st.repo.engine,'checkout',checkout)
    token=writer.set(context)
    try:
        with pytest.raises(PendingRollbackError):
            with st.repo.transaction() as db:db.invalidate()
    finally:writer.reset(token);event.remove(st.repo.engine,'checkout',checkout)
    assert len(connected)==1 and st.repo.engine.pool.checkedout()==0
