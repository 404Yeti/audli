"""Deterministic local PostgreSQL P0 release blocker regressions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import text,select,update,func
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException
from app.storage import schema as s
from app.repository import PostgresRepository
from experiments.assessment_claims.test_stage import fixture,http_app,upload,T
from experiments.assessment_claims.bridge import BridgeStore
from experiments.assessment_claims.schema import operations,recovery_audit
from experiments.durable_claims.store import slots,Conflict
from scripts.bench_app import BenchProvider,learner_uuid
from .rehearsal import production_shaped,ROLES
from .migration import apply_candidate,rollback_candidate
from .account_erasure import erase
from .recovery_tool import execute,ROLE


def auth_surface(repo,other):
    # Reproduce the production-catalog ownership/FK actions, not private data.
    cascade=('identities','mfa_factors','mfa_recovery_code_sets','oauth_authorizations',
        'oauth_consents','one_time_tokens','webauthn_challenges','webauthn_credentials')
    with repo.engine.begin() as db:
        db.exec_driver_sql('CREATE SCHEMA auth; CREATE SCHEMA storage')
        db.exec_driver_sql('CREATE TABLE auth.users(id uuid PRIMARY KEY)')
        db.exec_driver_sql('CREATE TABLE auth.sessions(id uuid PRIMARY KEY,user_id uuid REFERENCES auth.users ON DELETE CASCADE)')
        db.exec_driver_sql('CREATE TABLE auth.refresh_tokens(id uuid PRIMARY KEY,user_id text,session_id uuid REFERENCES auth.sessions ON DELETE CASCADE)')
        for table in (*cascade,'flow_state','scim_users'):
            action='CASCADE' if table in cascade else 'SET NULL'
            db.exec_driver_sql(f'CREATE TABLE auth.{table}(id uuid PRIMARY KEY,user_id uuid REFERENCES auth.users ON DELETE {action})')
        db.exec_driver_sql('CREATE TABLE storage.buckets(id text PRIMARY KEY)')
        db.exec_driver_sql('CREATE TABLE storage.objects(id uuid PRIMARY KEY,bucket_id text REFERENCES storage.buckets)')
        for owner in (repo.learner_id,other.learner_id):
            db.execute(text('INSERT INTO auth.users VALUES(:o)'),{'o':owner})
            session=str(uuid4())
            db.execute(text('INSERT INTO auth.sessions VALUES(:i,:o)'),{'i':session,'o':owner})
            db.execute(text('INSERT INTO auth.refresh_tokens VALUES(:i,:o,:s)'),{'i':str(uuid4()),'o':owner,'s':session})
            for table in (*cascade,'flow_state','scim_users'):
                db.execute(text(f'INSERT INTO auth.{table} VALUES(:i,:o)'),{'i':str(uuid4()),'o':owner})
    return ('users','sessions','refresh_tokens',*cascade,'flow_state','scim_users')


def test_schema_chain_atomic_backend_grants_and_missing_guard_refusal():
    roles=tuple(v for k,v in ROLES.items() if k!='postgres')
    with production_shaped() as (url,admin,owner):
        repo=PostgresRepository(url,learner_uuid('bench-a'));repo.close() # real schema006
        with pytest.raises(Conflict):apply_candidate(owner,include_review=False)
        with pytest.raises(Conflict):apply_candidate(owner,include_review=True,server_role='missing_backend',client_roles=roles)
        with admin.connect() as db:
            assert list(db.execute(text('SELECT version FROM schema_migrations ORDER BY version')).scalars())==list(range(1,7))
        apply_candidate(owner,include_review=True,server_role='audli_audit_app',client_roles=roles)
        repo=PostgresRepository(url,learner_uuid('bench-a'));repo.close()
        for role in roles:
            for table in ('poc_account_slots','poc_upload_operations','poc_stage_operations','poc_recovery_audit','review_improvements'):
                with pytest.raises(DBAPIError) as denied:
                    with admin.begin() as db:
                        db.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                        db.exec_driver_sql(f'TRUNCATE public.{table}')
                assert denied.value.orig.sqlstate=='42501'
        with admin.begin() as db:
            for table in ('poc_account_slots','poc_upload_operations','poc_stage_operations','poc_recovery_audit','review_improvements'):
                for role in roles:
                    assert not db.execute(text("SELECT has_table_privilege(:r,:t,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')"),{'r':role,'t':table}).scalar_one()
            assert db.execute(text("SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='S'")).scalar_one()==0
            assert not db.execute(text("SELECT has_table_privilege('audli_audit_app','poc_stage_operations','TRUNCATE,TRIGGER,REFERENCES,MAINTAIN')")).scalar_one()
            db.exec_driver_sql('ALTER TABLE learner_roadmaps DISABLE TRIGGER poc_claim_guard')
        with pytest.raises(RuntimeError):PostgresRepository(url,learner_uuid('bench-a'))
        with admin.begin() as db:
            db.exec_driver_sql('ALTER TABLE learner_roadmaps ENABLE TRIGGER poc_claim_guard')
            db.exec_driver_sql('ALTER TABLE poc_recovery_audit RENAME COLUMN session_actor TO broken_actor')
        with pytest.raises(RuntimeError):PostgresRepository(url,learner_uuid('bench-a'))
        with admin.begin() as db:
            db.exec_driver_sql('ALTER TABLE poc_recovery_audit RENAME COLUMN broken_actor TO session_actor')
            db.exec_driver_sql('ALTER TABLE conversations DISABLE TRIGGER poc_checkpoint_parent_guard')
        with pytest.raises(RuntimeError):PostgresRepository(url,learner_uuid('bench-a'))
        with admin.begin() as db:
            db.exec_driver_sql('ALTER TABLE conversations ENABLE TRIGGER poc_checkpoint_parent_guard')
            db.exec_driver_sql('ALTER FUNCTION poc_claim_guard() SECURITY DEFINER')
        with pytest.raises(RuntimeError):PostgresRepository(url,learner_uuid('bench-a'))
        with admin.begin() as db:db.exec_driver_sql('ALTER FUNCTION poc_claim_guard() SECURITY INVOKER')
        rollback_candidate(owner)


def test_planner_get_admitted_and_does_not_displace_active_or_expired_claim(fixture,tmp_path,monkeypatch):
    st,*_=fixture;store=BridgeStore(st.repo)
    async def run():
        app,auth=await http_app(st,tmp_path,monkeypatch,BenchProvider(),full=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local'}) as client:
            assert (await client.get('/api/plan')).status_code==200
            _,ref,context=store.claim('active-assessment','a'*64)
            before=st.repo.roadmap().revision
            assert (await client.get('/api/plan')).status_code==409
            with st.repo.engine.begin() as db:db.execute(update(operations).where(operations.c.id==ref['operation_id']).values(expires=func.clock_timestamp()-text("interval '1 second'")))
            assert (await client.get('/api/plan')).status_code==409
            assert st.repo.roadmap().revision==before
        await auth.aclose()
    asyncio.run(run())


def test_recovery_role_audit_and_audit_failure_roll_back_fencing(fixture):
    st,*_=fixture;store=BridgeStore(st.repo);_,ref,old=store.claim('expired','a'*64)
    with st.repo.engine.begin() as db:
        db.execute(update(operations).where(operations.c.id==ref['operation_id']).values(expires=func.clock_timestamp()-text("interval '1 second'")))
        db.exec_driver_sql(f'DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=\'{ROLE}\') THEN CREATE ROLE {ROLE} NOLOGIN; END IF; END $$')
        # Explicit role membership, required even though this is a local operator.
        login=db.execute(text('SELECT current_user')).scalar_one()
        db.exec_driver_sql(f'GRANT {ROLE} TO {db.dialect.identifier_preparer.quote(login)}')
    observation=execute(st.repo,operation_id=ref['operation_id'])
    with st.repo.engine.begin() as db:
        db.exec_driver_sql("CREATE FUNCTION audit_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit failure'; END $$")
        db.exec_driver_sql('CREATE TRIGGER audit_fail BEFORE INSERT ON poc_recovery_audit FOR EACH ROW EXECUTE FUNCTION audit_fail()')
    args=dict(operation_id=ref['operation_id'],checkpoint=observation['checkpoint'],reason='worker_terminated',acknowledge_unknown=True)
    with pytest.raises(DBAPIError):execute(st.repo,**args)
    with st.repo.engine.begin() as db:
        assert db.execute(select(slots.c.active).where(slots.c.owner==st.owner)).scalar_one()==ref['operation_id']
        db.exec_driver_sql('DROP TRIGGER audit_fail ON poc_recovery_audit')
    assert not execute(st.repo,**args)['automatic_provider_retry']
    with st.repo.read() as db:
        audit=db.execute(select(recovery_audit).where(recovery_audit.c.owner==st.owner)).mappings().one()
        assert audit['fence_after']==audit['fence_before']+1 and audit['reason']=='worker_terminated'
    with pytest.raises(Conflict):store.finish(old,{'status':200,'body':{}})
    with pytest.raises(Conflict):execute(st.repo,**args)


def test_unauthorized_recovery_is_refused(fixture):
    st,*_=fixture
    with st.repo.engine.begin() as db:
        db.exec_driver_sql(f'DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=\'{ROLE}\') THEN CREATE ROLE {ROLE} NOLOGIN; END IF; END $$')
        db.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='audli_no_recovery') THEN CREATE ROLE audli_no_recovery NOLOGIN; END IF; END $$")
        db.exec_driver_sql('GRANT USAGE ON SCHEMA public TO audli_no_recovery')
        from .recovery_tool import authorize
        db.exec_driver_sql('SET LOCAL ROLE audli_no_recovery')
        with pytest.raises(Conflict):authorize(db)


@pytest.mark.parametrize('expired',[False,True])
def test_delete_refuses_active_and_expired_claims_without_data_loss(fixture,tmp_path,expired):
    st,ex,other,*_=fixture;auth_surface(st.repo,other)
    _,ref,old=BridgeStore(st.repo).claim('in-flight','a'*64)
    if expired:
        with st.repo.engine.begin() as db:db.execute(update(operations).where(operations.c.id==ref['operation_id']).values(expires=func.clock_timestamp()-text("interval '1 second'")))
    with pytest.raises(Conflict):erase(st.repo,tmp_path)
    assert st.repo.exercise(ex) and other.profile()
    with st.repo.read() as db:assert db.execute(text('SELECT count(*) FROM auth.users')).scalar_one()==2


def test_complete_erasure_cascades_auth_and_preserves_other_account(fixture,tmp_path):
    st,ex,other,*_=fixture;tables=auth_surface(st.repo,other)
    upload(st,ex)
    assert erase(st.repo,tmp_path)=={'deleted':True}
    with other.read() as db:
        for table in tables:
            key='id' if table=='users' else 'user_id'
            assert db.execute(text(f'SELECT count(*) FROM auth.{table} WHERE {key}=:o'),{'o':st.owner}).scalar_one()==0
            assert db.execute(text(f'SELECT count(*) FROM auth.{table} WHERE {key}=:o'),{'o':other.learner_id}).scalar_one()==1
        assert db.execute(select(func.count()).select_from(s.users).where(s.users.c.id==st.owner)).scalar_one()==0
    st.repo.require_auth_user=True
    from app.storage.repository import AccountUnavailable
    with pytest.raises(AccountUnavailable):st.repo.initialize_learner()


def test_storage_presence_and_late_auth_failure_leave_account_intact(fixture,tmp_path):
    st,ex,other,*_=fixture;auth_surface(st.repo,other)
    with st.repo.engine.begin() as db:db.exec_driver_sql("INSERT INTO storage.buckets VALUES('synthetic')")
    with pytest.raises(HTTPException):erase(st.repo,tmp_path)
    assert st.repo.exercise(ex)
    with st.repo.engine.begin() as db:
        db.exec_driver_sql('DELETE FROM storage.buckets')
        db.exec_driver_sql("CREATE FUNCTION auth.fail_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic final delete failure'; END $$")
        db.exec_driver_sql('CREATE TRIGGER fail_delete BEFORE DELETE ON auth.users FOR EACH ROW EXECUTE FUNCTION auth.fail_delete()')
    with pytest.raises(DBAPIError):erase(st.repo,tmp_path)
    assert st.repo.exercise(ex) and st.repo.profile() and other.profile()


def test_original_delete_mutation_cannot_bypass_claim_admission(fixture,tmp_path):
    from app.account import delete_account
    st,ex,other,*_=fixture;auth_surface(st.repo,other)
    with pytest.raises(HTTPException) as error:delete_account(st.repo,tmp_path)
    assert error.value.status_code==409 and st.repo.exercise(ex)


def test_actual_post_delete_is_authenticated_admitted_and_acknowledged_after_commit(fixture,tmp_path,monkeypatch):
    st,ex,other,*_=fixture;auth_surface(st.repo,other)
    import app.account as account
    checks=[]
    def live(repo,request):checks.append('session')
    async def reauth(*args):checks.append('reauth')
    monkeypatch.setattr(account,'require_live_session',live)
    monkeypatch.setattr(account,'reauthenticate',reauth)
    async def run():
        app,auth=await http_app(st,tmp_path,monkeypatch,BenchProvider(),full=True,account_deletion_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local'}) as client:
            store=BridgeStore(st.repo);_,ref,old=store.claim('processing','a'*64)
            payload={'confirmation':'DELETE MY ACCOUNT','password':'synthetic-only'}
            blocked=await client.post('/api/account/delete',json=payload)
            assert blocked.status_code==409 and st.repo.exercise(ex)
            unauthorized=await client.post('/api/account/delete',json=payload,headers={'Authorization':'Bearer wrong'})
            assert unauthorized.status_code==401
            store.reject(old)
            response=await client.post('/api/account/delete',json=payload)
            assert response.status_code==200 and response.json()=={'deleted':True}
            assert (await client.post('/api/account/delete',json=payload)).status_code==401
            with other.read() as db:assert db.execute(select(func.count()).select_from(s.users).where(s.users.c.id==st.owner)).scalar_one()==0
            assert checks==['session','reauth','session','reauth']
        await auth.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('iteration',range(3))
def test_concurrent_claim_and_delete_have_one_serialized_outcome(fixture,tmp_path,iteration):
    st,ex,other,*_=fixture;auth_surface(st.repo,other)
    import threading
    barrier=threading.Barrier(2)
    def claim():
        barrier.wait(timeout=3)
        try:return st.claim(ex,'upload',{'upload_id':'0123456789abcdef','bytes_hash':'a'})[0]
        except (Conflict,DBAPIError):return 'refused'
    def deletion():
        barrier.wait(timeout=3)
        try:erase(st.repo,tmp_path);return 'deleted'
        except Conflict:return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(claim);removal=pool.submit(deletion)
        claim_result=pending.result(timeout=10);delete_result=removal.result(timeout=10)
    assert (claim_result,delete_result) in [('owned','blocked'),('refused','deleted')]
    assert other.profile() and st.repo.engine.pool.checkedout()==0


def test_numbered_sql_artifact_interrupts_atomically_then_applies_007_008():
    from pathlib import Path
    from .migration import ROOT
    with production_shaped() as (url,admin,owner):
        source=(ROOT/'docs/postgres-008.sql').read_text()
        for actual,alias in ROLES.items():source=source.replace('"'+actual+'"','"'+alias+'"')
        # The new numbered migration cannot hide an unrun007.
        with pytest.raises(DBAPIError):
            with owner.begin() as db:db.exec_driver_sql(source)
        seven=(ROOT/'docs/postgres-007.sql').read_text()
        with owner.begin() as db:db.exec_driver_sql(seven)
        with pytest.raises(DBAPIError):
            with owner.begin() as db:db.exec_driver_sql(source.replace('INSERT INTO public.schema_migrations','SELECT 1/0; INSERT INTO public.schema_migrations'))
        with admin.connect() as db:
            assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one()==7
            assert db.execute(text("SELECT to_regclass('public.poc_stage_operations')")).scalar_one() is None
        with owner.begin() as db:db.exec_driver_sql(source)
        repo=PostgresRepository(url,learner_uuid('bench-a'));repo.close()
        rollback_candidate(owner)


def test_cancelled_erasure_waits_for_transaction_outcome(fixture,tmp_path,monkeypatch):
    st,ex,other,*_=fixture;auth_surface(st.repo,other)
    from . import account_erasure
    import threading
    entered=threading.Event();release=threading.Event()
    original=account_erasure.delete_account
    def slow(*args,**kwargs):
        entered.set();assert release.wait(3)
        return original(*args,**kwargs)
    monkeypatch.setattr(account_erasure,'delete_account',slow)
    from experiments.durable_claims.service import database_call
    async def run():
        task=asyncio.create_task(database_call(erase,st.repo,tmp_path))
        assert await asyncio.to_thread(entered.wait,3)
        task.cancel();await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
    with other.read() as db:assert db.execute(text('SELECT count(*) FROM auth.users WHERE id=:o'),{'o':st.owner}).scalar_one()==0
    assert other.profile() and st.repo.engine.pool.checkedout()==0


def test_recovery_works_as_dedicated_non_owner_role_without_progression_or_audit_delete_privileges():
    from sqlalchemy import create_engine,event
    from app.storage.repository import SQLProgressRepository
    with production_shaped() as (url,admin,owner):
        with admin.begin() as db:
            db.exec_driver_sql(f'DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=\'{ROLE}\') THEN CREATE ROLE {ROLE} NOLOGIN; END IF; END $$')
        apply_candidate(owner,include_review=True,client_roles=tuple(v for k,v in ROLES.items() if k!='postgres'),server_role='audli_audit_app',recovery_role=ROLE)
        repo=SQLProgressRepository(admin,learner_uuid('bench-a'));store=BridgeStore(repo)
        _,ref,old=store.claim('unknown-result','a'*64)
        with admin.begin() as db:db.execute(update(operations).where(operations.c.id==ref['operation_id']).values(expires=func.clock_timestamp()-text("interval '1 second'")))
        engine=create_engine(url)
        @event.listens_for(engine,'connect')
        def operator(dbapi,record):
            with dbapi.cursor() as cursor:cursor.execute(f'SET ROLE {ROLE}')
            dbapi.commit()
        try:
            restricted=SQLProgressRepository(engine,repo.learner_id)
            observation=execute(restricted,operation_id=ref['operation_id'])
            assert not execute(restricted,operation_id=ref['operation_id'],checkpoint=observation['checkpoint'],acknowledge_unknown=True,reason='worker_terminated')['delivery_confirmation']
            with pytest.raises(DBAPIError):
                with engine.begin() as db:db.execute(update(s.profiles).values(name='Forbidden'))
            with pytest.raises(DBAPIError):
                with engine.begin() as db:db.exec_driver_sql('DELETE FROM poc_recovery_audit')
            with admin.connect() as db:assert db.execute(select(recovery_audit.c.actor)).scalar_one()==ROLE
            with pytest.raises(Conflict):store.finish(old,{'status':200,'body':{}})
        finally:engine.dispose()


def test_account_erasure_uses_reviewed_non_owner_backend_grants(tmp_path):
    from sqlalchemy import create_engine,event
    from app.storage.repository import SQLProgressRepository
    with production_shaped() as (url,admin,owner):
        apply_candidate(owner,include_review=True,client_roles=tuple(v for k,v in ROLES.items() if k!='postgres'),server_role='audli_audit_app')
        repo=SQLProgressRepository(admin,learner_uuid('bench-a'));other=SQLProgressRepository(admin,learner_uuid('bench-b'))
        auth_surface(repo,other)
        with admin.begin() as db:
            db.exec_driver_sql('GRANT USAGE ON SCHEMA auth,storage TO audli_audit_app')
            db.exec_driver_sql('GRANT SELECT ON storage.buckets TO audli_audit_app')
            for table in ('users','sessions','refresh_tokens','flow_state','scim_users'):
                db.exec_driver_sql(f'GRANT SELECT,DELETE ON auth.{table} TO audli_audit_app')
            db.exec_driver_sql('GRANT UPDATE(id) ON auth.users TO audli_audit_app')
        engine=create_engine(url)
        @event.listens_for(engine,'connect')
        def backend(dbapi,record):
            with dbapi.cursor() as cursor:cursor.execute('SET ROLE audli_audit_app')
            dbapi.commit()
        try:
            restricted=SQLProgressRepository(engine,repo.learner_id)
            assert erase(restricted,tmp_path)=={'deleted':True}
            assert other.profile()
            with admin.connect() as db:
                assert db.execute(text('SELECT count(*) FROM auth.users')).scalar_one()==1
                assert db.execute(select(func.count()).select_from(s.users).where(s.users.c.id==repo.learner_id)).scalar_one()==0
        finally:engine.dispose()
