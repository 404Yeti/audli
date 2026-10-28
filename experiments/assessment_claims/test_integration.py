"""Real local PostgreSQL and HTTP, deterministic providers only."""
import asyncio
from datetime import timedelta
import pytest
import httpx
from sqlalchemy import select,update,text,func
from sqlalchemy.exc import DBAPIError
from .test_stage import fixture,http_app,complete
from .bridge import BridgeStore,writer,install_transactions
from .schema import operations
from experiments.durable_claims.store import Conflict,slots
from app.storage import schema as s
from scripts.bench_app import BenchProvider,wav


def test_bridge_fences_direct_engine_writes_and_expiration(fixture):
    st,ex,_,_,_=fixture
    install_transactions(st.repo.engine)
    store=BridgeStore(st.repo);state,ref,context=store.claim('synthetic','a'*64)
    assert state=='owned' and st.repo.engine.pool.checkedout()==0
    token=writer.set(context)
    try:
        # This path deliberately bypasses SQLProgressRepository.transaction.
        with st.repo.engine.begin() as db:
            db.execute(update(s.profiles).where(s.profiles.c.user_id==st.owner).values(name='Synthetic'))
        assert st.repo.profile().name=='Synthetic'
    finally:writer.reset(token)
    with st.repo.engine.begin() as db:
        db.execute(update(operations).where(operations.c.owner==st.owner).values(expires=func.clock_timestamp()-text("interval '1 second'")))
    token=writer.set(context)
    try:
        with pytest.raises(Conflict):st.repo.save_profile(st.repo.profile())
    finally:writer.reset(token)
    assert store.claim('synthetic','a'*64)[0]=='uncertain'
    with pytest.raises(Conflict):store.claim('different','b'*64)


def test_bridge_receipt_replay_and_changed_input(fixture):
    st,*_=fixture;store=BridgeStore(st.repo)
    _,ref,context=store.claim('synthetic','a'*64)
    store.finish(context,dict(status=200,body={'saved':True}))
    assert store.claim('synthetic','a'*64)[0]=='complete'
    assert store.status(ref['operation_id'])['result']=={'saved':True}
    with pytest.raises(Conflict):store.claim('synthetic','b'*64)
    st.repo.save_profile(st.repo.profile().model_copy(update={'name':'Changed'}))
    with pytest.raises(Conflict):store.claim('synthetic','a'*64)
    assert store.status(ref['operation_id'])['status']=='uncertain'


def test_legacy_advisory_ownership_prevents_new_provider_admission(fixture):
    from hashlib import sha256
    st,*_=fixture
    key=int.from_bytes(sha256(('audli-account:'+st.owner).encode()).digest()[:8],'big',signed=True)
    with st.repo.engine.connect() as old:
        old.execute(text('SELECT pg_advisory_xact_lock(:key)'),dict(key=key))
        with pytest.raises(DBAPIError):BridgeStore(st.repo).claim('synthetic','a'*64)
    assert BridgeStore(st.repo).claim('synthetic','a'*64)[0]=='owned'


def test_full_http_checkin_duplicate_processing_status_and_no_provider_connections(fixture,tmp_path,monkeypatch):
    st,ex,*_=fixture
    lesson=st.repo.lesson();lesson.phase='WELCOME';st.repo.save_lesson(lesson,lesson.revision)
    async def run():
        class Provider(BenchProvider):
            calls=0
            def __init__(self):self.entered=asyncio.Event();self.release=asyncio.Event()
            async def transcribe(self,*args):
                self.calls+=1;assert st.repo.engine.pool.checkedout()==0
                self.entered.set();await self.release.wait()
                from .test_stage import T
                return T
        provider=Provider();app,auth=await http_app(st,tmp_path,monkeypatch,provider,full=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local','X-Audli-Upload-Id':'0123456789abcdef'}) as client:
            path=f'/api/lessons/{lesson.id}/attempts'
            async def send():return await client.post(path,data={'revision':str(lesson.revision)},files={'audio':('answer.wav',wav(),'audio/wav')})
            first=asyncio.create_task(send());await asyncio.wait_for(provider.entered.wait(),5)
            second=await send();assert second.status_code==202,second.text
            reference=second.json();status=await client.get('/api/operations/'+reference['operation_id'])
            assert status.json()['status']=='processing'
            provider.release.set();response=await first;assert response.status_code==200,response.text
            replay=await send();assert replay.status_code==200,replay.text
            assert replay.json()['revision']==response.json()['revision'] and provider.calls==1
            status=await client.get('/api/operations/'+reference['operation_id'])
            assert status.json()['status']=='complete'
        await auth.aclose()
    asyncio.run(run())


def test_full_http_closing_is_persisted_once(fixture,tmp_path,monkeypatch):
    st,ex,*_=fixture;complete(st,ex)
    lesson=st.repo.lesson();lesson.phase='CLOSING';st.repo.save_lesson(lesson,lesson.revision)
    async def run():
        app,auth=await http_app(st,tmp_path,monkeypatch,BenchProvider(),full=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local','X-Audli-Operation-Id':'closing-operation'}) as client:
            path=f'/api/lessons/{lesson.id}/heard'
            first=await client.post(path,json={'revision':lesson.revision});assert first.status_code==200,first.text
            second=await client.post(path,json={'revision':lesson.revision});assert second.status_code==200,second.text
            saved=st.repo.lesson(lesson.id)
            assert saved.status=='completed' and saved.completed_at and saved.phase=='COMPLETED'
            assert st.repo.profile().completed_attempts==1
        await auth.aclose()
    asyncio.run(run())


def test_status_is_owner_scoped_and_invalidated_on_reset(fixture):
    from .lifecycle import reset
    st,ex,other,*_=fixture
    store=BridgeStore(st.repo);_,ref,_=store.claim('synthetic','a'*64)
    assert BridgeStore(other).status(ref['operation_id']) is None
    revision=st.repo.profile().onboarding.revision
    with pytest.raises(Exception):reset(st.repo,st.settings,revision+50)
    assert store.status(ref['operation_id'])['status']=='processing'
    reset(st.repo,st.settings,revision)
    assert store.status(ref['operation_id'])['status']=='invalidated'
    assert st.repo.current_exercise() is None


def test_internal_schema_permissions_deny_browser_roles_and_guc_spoof(fixture):
    from .security import harden,TABLES
    st,*_=fixture
    # Cluster-global role names are synthetic and reused only on the disposable server.
    with st.repo.engine.begin() as db:
        for role in ('audli_claim_rehearsal','anon','authenticated','service_role'):
            db.execute(text(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$"))
        db.execute(text('GRANT USAGE ON SCHEMA public TO audli_claim_rehearsal,anon,authenticated,service_role'))
    harden(st.repo.engine,'audli_claim_rehearsal')
    for role in ('anon','authenticated','service_role'):
        for name in TABLES:
            with pytest.raises(DBAPIError):
                with st.repo.engine.begin() as db:
                    db.exec_driver_sql(f'SET LOCAL ROLE {role}')
                    db.execute(text("SELECT set_config('audli.claim_owner',:owner,true)"),dict(owner=st.owner))
                    db.exec_driver_sql(f'SELECT * FROM public.{name}')
        with pytest.raises(DBAPIError):
            with st.repo.engine.begin() as db:
                db.exec_driver_sql(f'SET LOCAL ROLE {role}')
                db.execute(text('SELECT poc_stage_read(CAST(:owner AS uuid),CAST(:owner AS uuid),CAST(:owner AS uuid))'),dict(owner=st.owner))
    with st.repo.engine.begin() as db:
        db.exec_driver_sql('SET LOCAL ROLE audli_claim_rehearsal')
        db.exec_driver_sql('SELECT * FROM poc_stage_operations')


def test_retry_storm_and_concurrent_accounts_claim_once(fixture):
    from concurrent.futures import ThreadPoolExecutor
    st,_,other,*_=fixture
    def claim(i):return BridgeStore(st.repo if i%2==0 else other).claim('same-recording','a'*64)[0]
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(claim,range(16)))
    assert results.count('owned')==2 and results.count('processing')==14
    assert st.repo.engine.pool.checkedout()==0 and other.engine.pool.checkedout()==0


def test_cancelled_legacy_provider_stays_quarantined_without_duplicate_stt(fixture,tmp_path,monkeypatch):
    st,ex,*_=fixture;lesson=st.repo.lesson();lesson.phase='WELCOME';st.repo.save_lesson(lesson,lesson.revision)
    async def run():
        class Provider(BenchProvider):
            calls=0
            def __init__(self):self.entered=asyncio.Event()
            async def transcribe(self,*args):
                self.calls+=1;self.entered.set();await asyncio.Event().wait()
        provider=Provider();app,auth=await http_app(st,tmp_path,monkeypatch,provider,full=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local','X-Audli-Upload-Id':'0123456789abcdef'}) as client:
            async def send():return await client.post(f'/api/lessons/{lesson.id}/attempts',data={'revision':str(lesson.revision)},files={'audio':('answer.wav',wav(),'audio/wav')})
            task=asyncio.create_task(send());await asyncio.wait_for(provider.entered.wait(),5);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            duplicate=await send();assert duplicate.status_code==202 and provider.calls==1
            assert st.repo.engine.pool.checkedout()==0
            # Explicit learner action fences the cancelled callback and preserves state.
            ref=duplicate.json();response=await client.post('/api/operations/'+ref['operation_id']+'/record-again',json={'kind':'request'})
            assert response.status_code==200,response.text
        await auth.aclose()
    asyncio.run(run())


def test_expiry_without_an_exercise_invalidates_provider_claim(fixture):
    from .lifecycle import settle
    st,ex,*_=fixture
    lesson=st.repo.lesson();lesson.exercise_id=None;lesson.phase='WELCOME'
    lesson.started_at-=timedelta(seconds=900);lesson.active_since=lesson.started_at
    st.repo.save_lesson(lesson,lesson.revision)
    store=BridgeStore(st.repo);_,ref,context=store.claim('synthetic','a'*64)
    settings=st.settings.model_copy(update={'lesson_limit_mode':'enforce'})
    settle(st.repo,settings,lesson.id)
    assert store.status(ref['operation_id'])['status']=='invalidated'
    assert st.repo.lesson(lesson.id).status=='expired'
    with pytest.raises(Conflict):store.finish(context,{'status':200,'body':{}})


def test_non_owner_backend_role_executes_fenced_claim_and_finalize(fixture):
    from .security import harden
    from .schema import TABLES
    from sqlalchemy import create_engine,event
    from app.storage.repository import SQLProgressRepository
    st,*_=fixture
    with st.repo.engine.begin() as db:
        db.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='audli_claim_rehearsal') THEN CREATE ROLE audli_claim_rehearsal NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$")
        db.exec_driver_sql('GRANT USAGE ON SCHEMA public TO audli_claim_rehearsal')
        for table in TABLES:
            db.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON public."{table.name}" TO audli_claim_rehearsal')
            db.exec_driver_sql(f'CREATE POLICY internal_backend_rehearsal ON public."{table.name}" TO audli_claim_rehearsal USING (true) WITH CHECK (true)')
    harden(st.repo.engine,'audli_claim_rehearsal')
    engine=create_engine(st.repo.engine.url)
    @event.listens_for(engine,'connect')
    def role(dbapi,record):
        with dbapi.cursor() as cursor:cursor.execute('SET ROLE audli_claim_rehearsal')
        dbapi.commit()
    try:
        repo=SQLProgressRepository(engine,st.owner);store=BridgeStore(repo)
        _,ref,context=store.claim('restricted-role','a'*64)
        install_transactions(engine);token=writer.set(context)
        try:
            profile=repo.profile();profile.name='Restricted backend';repo.save_profile(profile)
            store.finish(context,{'status':200,'body':{'saved':True}})
        finally:writer.reset(token)
        assert store.status(ref['operation_id'])['result']=={'saved':True}
    finally:engine.dispose()


def test_native_claim_cannot_steal_expired_compatibility_outcome(fixture):
    from .test_stage import T
    from hashlib import sha256
    st,ex,*_=fixture
    store=BridgeStore(st.repo);_,ref,_=store.claim('ambiguous-legacy','a'*64)
    with st.repo.engine.begin() as db:
        db.execute(update(operations).where(operations.c.owner==st.owner).values(expires=func.clock_timestamp()-text("interval '1 second'")))
    with pytest.raises(Conflict):st.claim(ex,'upload',{'upload_id':'0123456789abcdef','bytes_hash':sha256(b'synthetic').hexdigest()})
    assert store.status(ref['operation_id'])['status']=='uncertain'


def test_old_record_again_receipt_cannot_clear_newer_checkin(fixture):
    from .lifecycle import record_again
    st,ex,*_=fixture;lesson=st.repo.lesson();lesson.phase='WELCOME';st.repo.save_lesson(lesson,lesson.revision)
    store=BridgeStore(st.repo)
    _,ref,context=store.claim('first','a'*64,{'path':f'/api/lessons/{lesson.id}/attempts'})
    assert record_again(st.repo,st.settings,ref['operation_id'],'request')['record_again']
    # Reusing an invalidated receipt must not discard a later pending answer.
    from .test_stage import T
    lesson=st.repo.lesson();lesson.pending=T;st.repo.save_lesson(lesson,lesson.revision)
    with pytest.raises(Conflict):record_again(st.repo,st.settings,ref['operation_id'],'request')
    assert st.repo.lesson().pending is not None


def test_drain_refuses_unknown_active_and_expired_claims_then_accepts_explicit_recovery(fixture):
    from .recovery import drain_report,require_drained
    from .lifecycle import reset
    from uuid import uuid4
    st,*_=fixture
    assert not any(require_drained(st.repo.engine,target_protocol='guard-aware-v1').values())
    with pytest.raises(Conflict):require_drained(st.repo.engine,target_protocol='unaware')
    _,_,old=BridgeStore(st.repo).claim('crashed-worker','a'*64)
    assert drain_report(st.repo.engine)['processing']==1
    with pytest.raises(Conflict):require_drained(st.repo.engine,target_protocol='guard-aware-v1')
    with st.repo.engine.begin() as db:
        db.execute(update(operations).where(operations.c.owner==st.owner).values(expires=func.clock_timestamp()-text("interval '1 second'")))
    assert drain_report(st.repo.engine)['unresolved']==1
    with pytest.raises(Conflict):require_drained(st.repo.engine,target_protocol='guard-aware-v1')
    reset(st.repo,st.settings,st.repo.profile().onboarding.revision)
    assert not any(require_drained(st.repo.engine,target_protocol='guard-aware-v1').values())
    with pytest.raises(Conflict):BridgeStore(st.repo).finish(old,{'status':200,'body':{}})
    with st.repo.engine.begin() as db:
        db.execute(update(slots).where(slots.c.owner==st.owner).values(active=str(uuid4())))
    assert drain_report(st.repo.engine)['unknown']==1
    with pytest.raises(Conflict):require_drained(st.repo.engine,target_protocol='guard-aware-v1')


def test_real_http_generation_listening_upload_assessment_and_closing(fixture,tmp_path,monkeypatch):
    from .test_stage import body,T
    from app.services.demo import DemoProvider
    st,ex,*_=fixture
    complete(st,ex)
    _,_,ready=st.claim(ex,'ready',{});st.finish(ready)
    _,_,advance=st.claim(ex,'advance',{'revision':st.repo.lesson().revision});st.finish(advance)
    async def run():
        class Provider(BenchProvider):
            calls={'generate':0,'speak':0,'transcribe':0,'assess':0}
            async def generate(self,profile):
                assert st.repo.engine.pool.checkedout()==0;self.calls['generate']+=1
                return await DemoProvider.generate(self,profile)
            async def speak(self,*args,**kwargs):
                assert st.repo.engine.pool.checkedout()==0;self.calls['speak']+=1;return wav()
            async def transcribe(self,*args):
                assert st.repo.engine.pool.checkedout()==0;self.calls['transcribe']+=1;return T
            async def assess(self,content,turns):
                assert st.repo.engine.pool.checkedout()==0;self.calls['assess']+=1
                # Reuse the baseline fixture's grounded evidence logic without sleep.
                from app.evaluation import expected_units
                from app.models import EvidenceAssessment
                return EvidenceAssessment(units=[dict(dimension=d,index=i,status='demonstrated',evidence=T.text,question='')
                    for d,units in expected_units(content).items() for i in range(len(units))],feedback='Synthetic feedback.',transcription_concern=False,concern_reason='')
        p=Provider();app,auth=await http_app(st,tmp_path,monkeypatch,p,full=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local'}) as client:
            lesson=st.repo.lesson()
            response=await client.post(f'/api/lessons/{lesson.id}/exercise',json={'revision':lesson.revision},headers={'X-Audli-Operation-Id':'generate-once'})
            assert response.status_code==200,response.text
            generated=response.json();new_ex=generated['exercise']['id']
            response=await client.post(f'/api/exercises/{new_ex}/conversation/listened',headers={'X-Audli-Operation-Id':'listened-once'})
            assert response.status_code==200 and response.json()['state']=='AWAITING_SUMMARY',response.text
            response=await client.post(f'/api/exercises/{new_ex}/attempts',files={'audio':('answer.wav',wav(),'audio/wav')},headers={'X-Audli-Upload-Id':'1111111111111111'})
            assert response.status_code==200,response.text
            attempt=response.json()['id']
            response=await client.post(f'/api/attempts/{attempt}/assess',json=dict(confirmed=True,text=T.text,hands_free=True,followup_id=None))
            assert response.status_code==200 and response.json()['state']=='GIVING_FEEDBACK',response.text
            response=await client.post(f'/api/exercises/{new_ex}/conversation/ready');assert response.status_code==200,response.text
            lesson=st.repo.lesson();lesson.elapsed_seconds=600;lesson.active_since=None;st.repo.save_lesson(lesson,lesson.revision)
            response=await client.post(f'/api/lessons/{lesson.id}/advance',json={'revision':lesson.revision})
            assert response.status_code==200 and response.json()['phase']=='CLOSING',response.text
            assert response.json()['prompt'] and 'remaining_seconds' in response.json()
            response=await client.post(f'/api/lessons/{lesson.id}/heard',json={'revision':response.json()['revision']},headers={'X-Audli-Operation-Id':'closing-once'})
            assert response.status_code==200 and response.json()['status']=='completed',response.text
            assert p.calls=={'generate':1,'speak':1,'transcribe':1,'assess':1}
            assert st.repo.profile().completed_attempts==2
        await auth.aclose()
    asyncio.run(run())
