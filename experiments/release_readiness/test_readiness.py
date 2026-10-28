import json
from pathlib import Path
import pytest
from sqlalchemy import text,select,update,func
from sqlalchemy.exc import DBAPIError
from .rehearsal import production_shaped,comparison,ROLES
from .migration import apply_candidate,rollback_candidate
from experiments.durable_claims.store import Conflict
from experiments.assessment_claims.schema import install


def test_production_catalogue_equivalence_and_schema_dependency():
    with production_shaped() as (url,admin,owner):
        report=comparison(admin)
        Path(__file__).with_name('results').joinpath('schema-comparison.json').write_text(json.dumps(report,indent=2))
        assert all(v['equal'] for v in report.values()),report
        with pytest.raises(Conflict):apply_candidate(owner)
        with pytest.raises(DBAPIError):install(owner)  # Approved PR112 assumes optional migration007 exists.
        with admin.connect() as db:
            assert db.execute(text("SELECT to_regclass('poc_stage_operations')")).scalar_one() is None


def test_atomic_candidate_interruption_permissions_and_empty_rollback():
    with production_shaped() as (url,admin,owner):
        roles=tuple(v for k,v in ROLES.items() if k!='postgres')
        with pytest.raises(Conflict):apply_candidate(owner,include_review=True,client_roles=roles,fail_after='ddl')
        with admin.connect() as db:
            assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one()==6
            assert db.execute(text("SELECT to_regclass('poc_stage_operations')")).scalar_one() is None
        apply_candidate(owner,include_review=True,client_roles=roles)
        for role in roles:
            with admin.begin() as db:
                for table in ('poc_account_slots','poc_stage_operations','poc_upload_operations','review_improvements'):
                    assert not db.execute(text('SELECT has_table_privilege(:role,:table,\'SELECT,INSERT,UPDATE,DELETE,TRUNCATE\')'),{'role':role,'table':table}).scalar_one()
        rollback_candidate(owner)
        with admin.connect() as db:
            assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one()==7
            assert db.execute(text("SELECT to_regclass('poc_stage_operations')")).scalar_one() is None


from experiments.assessment_claims.test_stage import fixture,upload,T
from experiments.assessment_claims.bridge import BridgeStore,writer,install_transactions
from experiments.assessment_claims.schema import operations
from experiments.durable_claims.store import slots
from .quarantine import recover,inspect_quarantine,state_hash
from .retention import minimize,erase_public_account


def test_quarantine_recovery_rolls_back_if_slot_cleanup_fails(fixture,monkeypatch):
    st,*_=fixture;store=BridgeStore(st.repo)
    _,ref,old=store.claim('cleanup-fault','a'*64)
    with st.repo.engine.begin() as db:
        db.execute(update(operations).where(operations.c.owner==st.owner).values(expires=func.clock_timestamp()-text("interval '1 second'")))
    current=inspect_quarantine(st.repo,ref['operation_id'])
    from . import quarantine
    real_update=quarantine.update
    def failing_update(table):
        if table is slots:raise RuntimeError('Synthetic cleanup failure')
        return real_update(table)
    with monkeypatch.context() as patch:
        patch.setattr(quarantine,'update',failing_update)
        with pytest.raises(RuntimeError,match='Synthetic cleanup failure'):
            recover(st.repo,ref['operation_id'],expected_checkpoint=current['checkpoint'],acknowledge_unknown=True)
    with st.repo.read() as db:
        assert db.execute(select(operations.c.status).where(operations.c.id==ref['operation_id'])).scalar_one()=='running'
        assert db.execute(select(slots.c.active).where(slots.c.owner==st.owner)).scalar_one()==ref['operation_id']
        assert state_hash(db,st.owner)==current['checkpoint']


@pytest.mark.parametrize('path',['checkin','generation','assessment_legacy','onboarding','planner','homework','review','tts'])
@pytest.mark.parametrize('partial',[False,True])
def test_expired_compatibility_recovery_preserves_all_canonical_state(fixture,path,partial):
    st,ex,other,*_=fixture;store=BridgeStore(st.repo)
    state,ref,old=store.claim('synthetic-'+path,'a'*64,{'path':'/api/synthetic/'+path})
    install_transactions(st.repo.engine)
    if partial:
        token=writer.set(old)
        try:
            profile=st.repo.profile();profile.name='Synthetic committed checkpoint';st.repo.save_profile(profile)
        finally:writer.reset(token)
    current=inspect_quarantine(st.repo,ref['operation_id'])
    with pytest.raises(Conflict):recover(st.repo,ref['operation_id'],expected_checkpoint=current['checkpoint'],acknowledge_unknown=True)
    with pytest.raises(Conflict):inspect_quarantine(other,ref['operation_id'])
    with st.repo.engine.begin() as db:db.execute(update(operations).where(operations.c.owner==st.owner).values(expires=func.clock_timestamp()-text("interval '1 second'")))
    current=inspect_quarantine(st.repo,ref['operation_id'])
    with pytest.raises(Conflict):recover(st.repo,ref['operation_id'],expected_checkpoint=current['checkpoint'])
    with pytest.raises(Conflict):recover(st.repo,ref['operation_id'],expected_checkpoint='0'*64,acknowledge_unknown=True)
    result=recover(st.repo,ref['operation_id'],expected_checkpoint=current['checkpoint'],acknowledge_unknown=True)
    assert result=={'outcome':'resume_canonical_state','delivery_confirmation':False,'automatic_provider_retry':False}
    with st.repo.transaction() as db:assert state_hash(db,st.owner)==current['checkpoint']
    with pytest.raises(Conflict):store.finish(old,{'status':200,'body':{}})
    assert store.claim('synthetic-'+path,'a'*64)[0]=='uncertain'
    assert st.repo.profile().completed_attempts==0 and other.profile().completed_attempts==0


def test_erasure_removes_private_claims_and_retained_rows_without_other_account_loss(fixture):
    st,ex,other,other_ex,*_=fixture
    first=upload(st,ex);old_profile=other.profile().model_dump()
    store=BridgeStore(st.repo);_,ref,old=store.claim('quarantined','a'*64)
    erase_public_account(st.repo)
    from app.account import owner_predicate
    from experiments.assessment_claims.schema import TABLES
    with st.repo.engine.connect() as db:
        for table in TABLES:assert db.execute(select(func.count()).select_from(table).where(owner_predicate(table,st.owner))).scalar_one()==0
        for table in (operations,slots):assert db.execute(select(func.count()).select_from(table).where(table.c.owner==st.owner)).scalar_one()==0
    assert other.profile().model_dump()==old_profile and other.exercise(other_ex)
    with pytest.raises(Exception):store.finish(old,{'status':200,'body':{}})


def test_retention_minimizes_json_but_keeps_non_resurrectable_tombstones(fixture):
    st,ex,*_=fixture;store=BridgeStore(st.repo)
    _,ref,context=store.claim('legacy-receipt','a'*64);store.finish(context,{'status':200,'body':{'synthetic_private_text':'retention fixture'}})
    with pytest.raises(Conflict):minimize(st.repo)
    # Explicit reset closes/discards only the original unevaluated work per existing policy.
    from experiments.assessment_claims.lifecycle import reset
    reset(st.repo,st.settings,st.repo.profile().onboarding.revision)
    assert minimize(st.repo)==1
    with st.repo.read() as db:
        op=db.execute(select(operations).where(operations.c.owner==st.owner)).mappings().one()
        assert op['snapshot']=={} and op['input']=={} and op['status']=='invalidated'
    assert store.claim('legacy-receipt','a'*64)[0]=='uncertain'


def test_populated_schema_cannot_be_destructively_rolled_back_and_old_startup_refuses_version8():
    from app.repository import PostgresRepository
    from sqlalchemy import create_engine,event
    from app.storage.repository import SQLProgressRepository
    from experiments.release_readiness.migration import grant_server
    from experiments.assessment_claims.store import StageStore
    from app.config import Settings
    from scripts.bench_app import learner_uuid
    with production_shaped() as (url,admin,owner):
        apply_candidate(owner,include_review=True,client_roles=tuple(v for k,v in ROLES.items() if k!='postgres'))
        grant_server(owner)
        with pytest.raises(RuntimeError):PostgresRepository(url,learner_uuid('bench-a'))
        engine=create_engine(url)
        @event.listens_for(engine,'connect')
        def role(dbapi,record):
            with dbapi.cursor() as cursor:cursor.execute('SET ROLE audli_audit_app')
            dbapi.commit()
        try:
            repo=SQLProgressRepository(engine,learner_uuid('bench-a'));store=BridgeStore(repo)
            _,ref,context=store.claim('restricted-role','a'*64)
            store.finish(context,{'status':200,'body':{'saved':True}})
            assert store.status(ref['operation_id'])['status']=='complete'
            other=BridgeStore(SQLProgressRepository(engine,learner_uuid('bench-b')))
            assert other.status(ref['operation_id']) is None
            with pytest.raises(Conflict):rollback_candidate(owner)
        finally:engine.dispose()
