"""Real PostgreSQL deterministic correctness tests; synthetic providers only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import threading
import uuid

import pytest
from sqlalchemy import select, func, text, update
from app.models import Transcription
from app.storage import schema as s
from .local import database, seed
from .store import Store, Conflict, Claim, create_schema, operations, slots, claims_only_allowed
from .service import process, database_call
from .optimized_store import OptimizedStore, install_functions

T = Transcription(text='Synthetic local answer.', confidence=.99, uncertainty=[])
KEY = '0123456789abcdef'
FINGERPRINT = sha256(b'synthetic').hexdigest()


@pytest.fixture(params=[Store, OptimizedStore], ids=["original", "optimized"])
def fixture(request, monkeypatch):
    import sys
    from . import routes
    store_type = request.param
    monkeypatch.setattr(sys.modules[__name__], "Store", store_type)
    monkeypatch.setattr(routes, "Store", store_type)
    with database() as url:
        repo, exercise = seed(url, 'bench-a')
        other, other_exercise = seed(url, 'bench-b')
        create_schema(repo.engine)
        if store_type is OptimizedStore:
            install_functions(repo.engine)
        now = [datetime(2026, 10, 27, tzinfo=timezone.utc)]
        store = Store(repo, clock=lambda: now[0])
        yield store, exercise, other, other_exercise, now
        repo.close(); other.close()


def counts(store):
    with store.repo.engine.connect() as db:
        return tuple(db.execute(select(func.count()).select_from(table)).scalar_one()
                     for table in (s.attempts, s.evaluations, s.events))


def test_replay_atomic_checkpoint_and_no_assessment_bypass(fixture):
    st, ex, _, _, _ = fixture
    claim = st.claim(ex, KEY, FINGERPRINT)
    first = st.finalize(claim, ex, T)
    assert st.finalize(claim, ex, T) == first
    assert st.claim(ex, KEY, FINGERPRINT).kind == 'replay'
    assert counts(st) == (1, 0, 0)
    assert st.repo.profile().completed_attempts == 0
    assert st.repo.conversation(ex).pending_attempt_id == first['id']
    assert st.repo.conversation(ex).turns == []


def test_concurrent_duplicate_has_one_claim(fixture):
    st, ex, _, _, _ = fixture
    barrier = threading.Barrier(16)
    def call():
        barrier.wait()
        return st.claim(ex, KEY, FINGERPRINT)
    with ThreadPoolExecutor(max_workers=16) as pool:
        claims = list(pool.map(lambda _: call(), range(16)))
    assert sum(c.kind == 'owned' for c in claims) == 1
    assert sum(c.kind == 'processing' for c in claims) == 15
    st.finalize(next(c for c in claims if c.kind == 'owned'), ex, T)
    assert counts(st) == (1, 0, 0)


def test_other_recording_cannot_overwrite_pending(fixture):
    st, ex, _, _, _ = fixture
    claim = st.claim(ex, KEY, FINGERPRINT)
    assert st.claim(ex, '1111111111111111', FINGERPRINT).kind == 'busy'
    st.finalize(claim, ex, T)
    with pytest.raises(Conflict, match='pending'):
        st.claim(ex, '1111111111111111', FINGERPRINT)


def test_account_isolation_same_upload_key(fixture):
    st, ex, other, other_ex, _ = fixture
    second = Store(other)
    a, b = st.claim(ex, KEY, FINGERPRINT), second.claim(other_ex, KEY, FINGERPRINT)
    assert a.operation != b.operation
    with pytest.raises(ValueError):
        second.claim(ex, KEY, FINGERPRINT)
    with pytest.raises(Exception):
        second.finalize(a, other_ex, T)
    st.finalize(a, ex, T); second.finalize(b, other_ex, T)
    assert counts(st) == (2, 0, 0)


def test_id_cannot_be_reused_for_other_audio(fixture):
    st, ex, _, _, _ = fixture
    st.claim(ex, KEY, FINGERPRINT)
    with pytest.raises(Conflict, match='bytes'):
        st.claim(ex, KEY, sha256(b'different').hexdigest())


def test_crash_expiry_takeover_fences_late_worker(fixture):
    st, ex, _, _, now = fixture
    old = st.claim(ex, KEY, FINGERPRINT)
    # Simulate process death: no abandon, all Python ownership discarded.
    replacement = Store(st.repo, clock=lambda: now[0])
    assert replacement.claim(ex, KEY, FINGERPRINT).kind == 'processing'
    now[0] += timedelta(seconds=60)
    with pytest.raises(Conflict, match='Expired'):
        st.finalize(old, ex, T)
    new = replacement.claim(ex, KEY, FINGERPRINT)
    assert new.fence > old.fence
    with pytest.raises(Conflict, match='superseded'):
        st.finalize(old, ex, T)
    replacement.finalize(new, ex, T)
    assert counts(st) == (1, 0, 0)


def test_rollback_preserves_operation_and_checkpoint(fixture):
    st, ex, _, _, _ = fixture
    claim = st.claim(ex, KEY, FINGERPRINT)
    with pytest.raises(RuntimeError):
        st.finalize(claim, ex, T, fail_after_insert=True)
    assert counts(st) == (0, 0, 0)
    assert st.repo.conversation(ex).pending_attempt_id is None
    assert st.claim(ex, KEY, FINGERPRINT).kind == 'processing'
    st.finalize(claim, ex, T)
    assert counts(st) == (1, 0, 0)


def test_reset_invalidates_running_claim(fixture):
    st, ex, _, _, _ = fixture
    claim = st.claim(ex, KEY, FINGERPRINT)
    st.reset(ex)
    with pytest.raises(Conflict):
        st.finalize(claim, ex, T)
    with pytest.raises(Conflict):
        st.claim(ex, KEY, FINGERPRINT)
    assert counts(st) == (0, 0, 0)
    assert st.claim(ex, '1111111111111111', FINGERPRINT).kind == 'owned'


def test_changed_question_rejects_finalization(fixture):
    st, ex, _, _, _ = fixture
    claim = st.claim(ex, KEY, FINGERPRINT)
    conversation = st.repo.conversation(ex)
    conversation.state = 'LISTENING'
    st.repo.save_conversation(conversation)
    with pytest.raises(Conflict):
        st.finalize(claim, ex, T)
    assert counts(st) == (0, 0, 0)


def test_cancel_during_stt_no_attempt_and_retry_same_id(fixture):
    st, ex, _, _, _ = fixture
    async def run():
        entered = asyncio.Event()
        class Provider:
            calls = 0
            async def transcribe(self, *args):
                self.calls += 1
                assert st.repo.engine.pool.checkedout() == 0
                entered.set()
                await asyncio.Event().wait()
        provider = Provider()
        task = asyncio.create_task(process(st, ex, KEY, b'synthetic', 'a.wav', provider))
        await entered.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert counts(st) == (0, 0, 0)
        claim = st.claim(ex, KEY, FINGERPRINT)
        assert claim.kind == 'owned'
        st.finalize(claim, ex, T)
        assert provider.calls == 1
    asyncio.run(run())


def test_cancel_during_commit_waits_for_transaction(fixture):
    st, ex, _, _, _ = fixture
    claim = st.claim(ex, KEY, FINGERPRINT)
    entered, release = threading.Event(), threading.Event()
    def final():
        entered.set(); release.wait(2)
        return st.finalize(claim, ex, T)
    async def run():
        task = asyncio.create_task(database_call(final))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel(); await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
    assert st.claim(ex, KEY, FINGERPRINT).kind == 'replay'
    assert counts(st) == (1, 0, 0)


@pytest.mark.parametrize('old,writers', [(1,True),(2,True),(0,False)])
def test_claims_only_rejects_unsafe_overlap(old, writers):
    with pytest.raises(Conflict):
        claims_only_allowed(old_workers=old, participating_writers=writers)


def test_bridge_old_and_new_share_original_advisory_lock(fixture):
    st, ex, _, _, _ = fixture
    key = int.from_bytes(sha256(('audli-account:' + st.owner).encode()).digest()[:8], 'big', signed=True)
    # Both directions: old holds -> bridge cannot enter; bridge holds -> old cannot enter.
    with st.repo.engine.connect() as old, st.repo.engine.connect() as new:
        assert old.execute(text('SELECT pg_try_advisory_xact_lock(:k)'), {'k':key}).scalar_one()
        assert not new.execute(text('SELECT pg_try_advisory_xact_lock(:k)'), {'k':key}).scalar_one()
        old.rollback(); new.rollback()
        assert new.execute(text('SELECT pg_try_advisory_xact_lock(:k)'), {'k':key}).scalar_one()
        claim = st.claim(ex, KEY, FINGERPRINT)
        assert not old.execute(text('SELECT pg_try_advisory_xact_lock(:k)'), {'k':key}).scalar_one()
        st.finalize(claim, ex, T)
        new.rollback(); old.rollback()
        assert old.execute(text('SELECT pg_try_advisory_xact_lock(:k)'), {'k':key}).scalar_one()


def test_no_database_checkout_during_mocked_stt_and_replay(fixture):
    st, ex, _, _, _ = fixture
    class Provider:
        calls = 0
        async def transcribe(self, *args):
            self.calls += 1
            assert st.repo.engine.pool.checkedout() == 0
            await asyncio.sleep(0)
            assert st.repo.engine.pool.checkedout() == 0
            return T
    async def run():
        provider = Provider()
        assert (await process(st, ex, KEY, b'synthetic', 'a.wav', provider))[0] == 'complete'
        assert (await process(st, ex, KEY, b'synthetic', 'a.wav', provider))[0] == 'replay'
        assert provider.calls == 1
    asyncio.run(run())


def test_actual_worker_death_leaves_recoverable_claim(fixture):
    import subprocess
    import sys
    from pathlib import Path
    st, ex, _, _, now = fixture
    code = '''
import sys,time
from datetime import datetime, timezone
from app.repository import PostgresRepository
from experiments.durable_claims.store import Store
repo=PostgresRepository(sys.argv[1],sys.argv[2])
store=Store(repo,clock=lambda:datetime(2026,10,27,tzinfo=timezone.utc))
claim=store.claim(sys.argv[3],sys.argv[4],sys.argv[5])
assert claim.kind=='owned'
print('claimed',flush=True)
time.sleep(60)
'''
    child = subprocess.Popen([sys.executable,'-c',code,st.repo.engine.url.render_as_string(hide_password=False),
        st.owner,ex,KEY,FINGERPRINT],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        cwd=Path(__file__).resolve().parents[2])
    try:
        assert child.stdout.readline().strip() == 'claimed'
    finally:
        child.kill(); child.communicate(timeout=5)
    assert st.claim(ex,KEY,FINGERPRINT).kind == 'processing'
    now[0] += timedelta(seconds=61)
    fresh = st.claim(ex,KEY,FINGERPRINT)
    assert fresh.kind == 'owned'
    st.finalize(fresh,ex,T)
    assert counts(st) == (1,0,0)


def test_parallel_learners_and_inflight_duplicate_only_call_provider_once(fixture):
    st, ex, other, other_ex, _ = fixture
    second = Store(other)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class Provider:
            calls = 0
            async def transcribe(self,*args):
                self.calls += 1
                if self.calls == 2: entered.set()
                await release.wait()
                return T
        provider=Provider()
        tasks=[asyncio.create_task(process(store,exercise,KEY,b'synthetic','a.wav',provider))
               for store,exercise in ((st,ex),(second,other_ex))]
        await asyncio.wait_for(entered.wait(),3)
        duplicates=await asyncio.gather(*(process(st,ex,KEY,b'synthetic','a.wav',provider) for _ in range(8)))
        assert all(kind=='processing' for kind,_ in duplicates)
        assert provider.calls==2
        assert st.repo.engine.pool.checkedout()==other.engine.pool.checkedout()==0
        release.set()
        assert all(kind=='complete' for kind,_ in await asyncio.gather(*tasks))
    asyncio.run(run())
    assert counts(st)==(2,0,0)


def test_cancel_before_processing_starts_has_no_claim(fixture):
    st,ex,_,_,_=fixture
    async def run():
        task=asyncio.create_task(process(st,ex,KEY,b'synthetic','a.wav',None))
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
    with st.repo.read() as db:
        assert db.execute(select(func.count()).select_from(operations)).scalar_one()==0


def test_expiry_can_duplicate_external_work_but_never_accept_twice(fixture):
    st,ex,_,_,now=fixture
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        class Provider:
            calls=0
            async def transcribe(self,*args):
                self.calls+=1
                if self.calls==1:
                    entered.set();await release.wait()
                return T
        provider=Provider()
        old=asyncio.create_task(process(st,ex,KEY,b'synthetic','a.wav',provider))
        await entered.wait()
        now[0]+=timedelta(seconds=61)
        assert (await process(st,ex,KEY,b'synthetic','a.wav',provider))[0]=='complete'
        release.set()
        kind,result=await old
        assert kind=='complete' and result['id']==st.claim(ex,KEY,FINGERPRINT).result['id']
        assert provider.calls==2 and counts(st)==(1,0,0)
    asyncio.run(run())


def test_cancel_during_claim_leaves_fenced_expiring_operation(fixture):
    st,ex,_,_,now=fixture
    entered,release=threading.Event(),threading.Event()
    def acquire():
        claim=st.claim(ex,KEY,FINGERPRINT)
        entered.set();release.wait(2)
        return claim
    async def run():
        task=asyncio.create_task(database_call(acquire))
        await asyncio.to_thread(entered.wait,2)
        task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
    assert st.claim(ex,KEY,FINGERPRINT).kind=='processing'
    now[0]+=timedelta(seconds=61)
    assert st.claim(ex,KEY,FINGERPRINT).kind=='owned'
    assert counts(st)==(0,0,0)


@pytest.mark.parametrize('first',['old','bridge'])
def test_real_middleware_old_new_overlap_both_directions(fixture,tmp_path,first):
    import httpx
    from pathlib import Path
    from app.config import Settings
    from app.main import create_app
    from app.auth import LearnerAuthentication
    from bench_app import wav
    from .routes import install
    st,ex,_,_,_=fixture
    async def run():
        entered,release,contended=asyncio.Event(),asyncio.Event(),asyncio.Event()
        class Provider:
            calls=0
            async def transcribe(self,*args):
                self.calls+=1;entered.set();await release.wait();return T
        provider=Provider()
        async def identity(request):
            return httpx.Response(200,json={'id':st.owner,'email_confirmed_at':'yes','is_anonymous':False})
        settings=Settings(data_dir=tmp_path,provider='demo',auth_mode='supabase',supabase_url='https://bench.invalid',
            supabase_publishable_key='sb_publishable_bench',persistence='postgres',
            database_url=st.repo.engine.url.render_as_string(hide_password=False),
            db_offload=True,upload_idempotency=True,upload_retry=True,per_learner_locks=True,_env_file=None)
        auth_clients=[httpx.AsyncClient(transport=httpx.MockTransport(identity)) for _ in range(2)]
        apps=[create_app(settings,provider,st.repo,auth_client=c) for c in auth_clients]
        install(apps[1],settings,provider,mode='bridge',old_workers=1)
        for app in apps:
            if app.middleware_stack is None:app.middleware_stack=app.build_middleware_stack()
        order=[0,1] if first=='old' else [1,0]
        second_auth=apps[order[1]].middleware_stack
        while not isinstance(second_auth,LearnerAuthentication):second_auth=second_auth.app
        original=second_auth._take_lease
        loop=asyncio.get_running_loop()
        def take(*args):
            result=original(*args)
            if not result[1]:loop.call_soon_threadsafe(contended.set)
            return result
        second_auth._take_lease=take
        clients=[httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') for app in apps]
        async def send(index):
            return await clients[index].post(f'/api/exercises/{ex}/attempts',
                headers={'Authorization':'Bearer synthetic','X-Audli-Upload-Id':KEY},files={'audio':('a.wav',wav(),'audio/wav')})
        a=asyncio.create_task(send(order[0]))
        await asyncio.wait_for(entered.wait(),3)
        b=asyncio.create_task(send(order[1]))
        await asyncio.wait_for(contended.wait(),3)
        assert provider.calls==1 and not b.done()
        release.set()
        responses=await asyncio.gather(a,b)
        assert responses[0].status_code==200
        assert responses[1].status_code==200
        assert responses[1].json()['id']==responses[0].json()['id']
        assert provider.calls==1 and counts(st)==(1,0,0)
        assert st.repo.conversation(ex).pending_attempt_id==responses[0].json()['id']
        for client in clients+auth_clients:await client.aclose()
        for app in apps:
            auth=app.middleware_stack
            while not isinstance(auth,LearnerAuthentication):auth=auth.app
            auth.lease_engine.dispose()
            app.state.accent_controls.close()
    asyncio.run(run())


def test_concurrent_finalizers_commit_one_attempt_and_one_checkpoint(fixture):
    st,ex,_,_,_=fixture
    claim=st.claim(ex,KEY,FINGERPRINT)
    barrier=threading.Barrier(8)
    def finish(_):
        barrier.wait()
        return st.finalize(claim,ex,T)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(finish,range(8)))
    assert all(result==results[0] for result in results)
    assert counts(st)==(1,0,0)
    assert st.repo.conversation(ex).pending_attempt_id==claim.operation


def test_bounded_cancel_keeps_unknown_transaction_fenced(fixture,monkeypatch):
    import experiments.durable_claims.service as service
    st,ex,_,_,_=fixture
    claim=st.claim(ex,KEY,FINGERPRINT)
    entered,release=threading.Event(),threading.Event()
    def late_finalization():
        entered.set();release.wait(3)
        return st.finalize(claim,ex,T)
    async def run():
        task=asyncio.create_task(service.database_call(late_finalization))
        await asyncio.to_thread(entered.wait,2)
        clock=iter([0,3])
        monkeypatch.setattr(service,'monotonic',lambda:next(clock))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert len(service.pending)==1
        assert st.claim(ex,KEY,FINGERPRINT).kind=='processing'
        # Reset wins before the unknown thread reaches finalization; stale token loses.
        st.reset(ex);release.set()
        while service.pending:await asyncio.sleep(0)
    asyncio.run(run())
    assert counts(st)==(0,0,0)


def test_server_clock_and_owner_filtered_status(fixture):
    st,ex,other,other_ex,_=fixture
    real=Store(st.repo)
    claim=real.claim(ex,KEY,FINGERPRINT)
    assert real.status(ex,KEY) is None
    real.finalize(claim,ex,T)
    assert real.status(ex,KEY)['id']==claim.operation
    with pytest.raises(ValueError):Store(other).status(ex,KEY)
    with pytest.raises(Conflict):Store(other).status(other_ex,KEY)


def test_real_route_processing_replay_ownership_and_confidence_gate(fixture,tmp_path,monkeypatch):
    import httpx
    import app.auth as auth_module
    from app.config import Settings
    from app.main import create_app
    from app.auth import LearnerAuthentication
    from bench_app import wav
    from .routes import install
    st,ex,other,_,_=fixture
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        low=T.model_copy(update={'confidence':.2,'uncertainty':['Synthetic uncertainty']})
        class Provider:
            calls=0
            async def transcribe(self,*args):
                self.calls+=1;entered.set();await release.wait();return low
        provider=Provider()
        async def identity(request):
            token=request.headers.get('authorization')
            if token not in ('Bearer local-a','Bearer local-b'):return httpx.Response(401)
            return httpx.Response(200,json={'id':st.owner if token=='Bearer local-a' else other.learner_id,
                'email_confirmed_at':'yes','is_anonymous':False})
        settings=Settings(data_dir=tmp_path,provider='demo',auth_mode='supabase',supabase_url='https://bench.invalid',
            supabase_publishable_key='sb_publishable_bench',persistence='postgres',
            database_url=st.repo.engine.url.render_as_string(hide_password=False),db_offload=True,
            upload_idempotency=True,upload_retry=True,per_learner_locks=True,_env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(identity)) as auth_client:
            app=create_app(settings,provider,st.repo,auth_client=auth_client)
            # install() mutates the exemption list only inside this local Python process;
            # restore it at test exit so existing application regressions stay meaningful.
            monkeypatch.setattr(auth_module,'lease_exempt',auth_module.lease_exempt)
            install(app,settings,provider,mode='claims')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
                headers={'Authorization':'Bearer local-a','X-Audli-Upload-Id':KEY}
                async def send():
                    return await client.post(f'/api/exercises/{ex}/attempts',headers=headers,
                        files={'audio':('a.wav',wav(),'audio/wav')})
                first=asyncio.create_task(send());await asyncio.wait_for(entered.wait(),3)
                assert (await send()).status_code==202
                path=f'/api/poc/uploads/{ex}/{KEY}'
                assert (await client.get(path,headers=headers)).status_code==202
                assert provider.calls==1
                assert (await client.get(path,headers={'Authorization':'Bearer local-b'})).status_code==404
                release.set();accepted=await first
                assert accepted.status_code==200
                assert (await client.get(path,headers=headers)).json()['id']==accepted.json()['id']
                replay=await send()
                assert replay.status_code==200 and replay.headers['x-audli-replayed']=='true'
                assert provider.calls==1
                result=await client.post(f"/api/attempts/{accepted.json()['id']}/assess",headers=headers,
                    json={'text':low.text,'confirmed':True,'hands_free':True})
                assert result.status_code==422 and result.json()['uncertain'] is True
                assert counts(st)==(1,0,0) and st.repo.profile().completed_attempts==0
            auth=app.middleware_stack
            while not isinstance(auth,LearnerAuthentication):auth=auth.app
            auth.lease_engine.dispose();app.state.accent_controls.close()
    asyncio.run(run())


@pytest.mark.parametrize('fixture',[OptimizedStore],indirect=True)
@pytest.mark.parametrize('original_first',[True,False])
def test_original_optimized_claim_records_are_bidirectionally_compatible(fixture,original_first):
    from .store import Store as BaseStore
    st,ex,_,_,now=fixture
    original=BaseStore(st.repo,clock=lambda:now[0])
    first,second=(original,st) if original_first else (st,original)
    old=first.claim(ex,KEY,FINGERPRINT)
    assert second.claim(ex,KEY,FINGERPRINT).kind=='processing'
    now[0]+=timedelta(seconds=61)
    fresh=second.claim(ex,KEY,FINGERPRINT)
    assert fresh.fence>old.fence
    with pytest.raises(Conflict):first.finalize(old,ex,T)
    second.finalize(fresh,ex,T)
    assert first.claim(ex,KEY,FINGERPRINT).kind=='replay'
    assert counts(st)==(1,0,0)


@pytest.mark.parametrize('fixture',[OptimizedStore],indirect=True)
def test_expired_other_recording_is_cancelled_atomically(fixture):
    st,ex,_,_,now=fixture
    old=st.claim(ex,KEY,FINGERPRINT)
    now[0]+=timedelta(seconds=61)
    fresh=st.claim(ex,'1111111111111111',FINGERPRINT)
    with st.repo.read() as db:
        assert db.execute(select(operations.c.status).where(operations.c.id==old.operation)).scalar_one()=='cancelled'
    with pytest.raises(Conflict):st.finalize(old,ex,T)
    st.finalize(fresh,ex,T)
    assert counts(st)==(1,0,0)


@pytest.mark.parametrize('fixture',[OptimizedStore],indirect=True)
def test_finalization_participant_failure_rolls_everything_back(fixture,monkeypatch):
    import experiments.durable_claims.optimized_store as optimized
    st,ex,_,_,_=fixture
    claim=st.claim(ex,KEY,FINGERPRINT)
    broken=text(optimized.FINAL_WRITE.text.replace('AND fence=:fence','AND fence=:fence+1'))
    monkeypatch.setattr(optimized,'FINAL_WRITE',broken)
    with pytest.raises(Conflict,match='every participant'):st.finalize(claim,ex,T)
    assert counts(st)==(0,0,0)
    assert st.repo.conversation(ex).pending_attempt_id is None
    assert st.claim(ex,KEY,FINGERPRINT).kind=='processing'


@pytest.mark.parametrize('fixture',[OptimizedStore],indirect=True)
def test_projection_mismatch_cannot_hide_existing_pending_evidence(fixture):
    st,ex,_,_,_=fixture
    c=st.repo.conversation(ex)
    data=c.model_dump(mode='json');data['pending_attempt_id']=str(uuid.uuid4())
    with st.repo.transaction() as db:
        db.execute(update(s.conversations).where(s.conversations.c.exercise_id==ex).values(data=data))
    with pytest.raises(Conflict,match='projections'):st.claim(ex,KEY,FINGERPRINT)
    assert counts(st)==(0,0,0)


@pytest.mark.parametrize('fixture',[OptimizedStore],indirect=True)
def test_function_invoker_permissions_and_transaction_locking(fixture):
    st,ex,_,_,_=fixture
    with st.repo.read() as db:
        row=db.execute(text("SELECT p.prosecdef,p.proconfig,NOT EXISTS(SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE p.proname='poc_upload_read' AND n.nspname='public'" )).one()
        assert row[0] is False and 'search_path=pg_catalog, public' in row[1] and row[2] is True
    key=dict(owner=st.owner,exercise=ex,id=str(uuid.uuid4()))
    with st.repo.engine.connect() as first,st.repo.engine.connect() as second:
        first.execute(text('SELECT public.poc_upload_read(CAST(:owner AS uuid),CAST(:exercise AS uuid),CAST(:id AS uuid))'),key)
        second.execute(text("SET LOCAL lock_timeout='10ms'"))
        from sqlalchemy.exc import OperationalError
        with pytest.raises(OperationalError):
            second.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==st.owner).with_for_update())
        second.rollback();first.rollback()
        assert second.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==st.owner).with_for_update()).scalar_one()==st.owner


@pytest.mark.parametrize('fixture',[OptimizedStore],indirect=True)
def test_client_deadline_does_not_expire_server_claim(fixture):
    st,ex,_,_,now=fixture
    initial=st.claim(ex,KEY,FINGERPRINT)
    now[0]+=timedelta(seconds=50)
    assert st.claim(ex,KEY,FINGERPRINT).kind=='processing'
    now[0]+=timedelta(seconds=9,milliseconds=999)
    assert st.claim(ex,KEY,FINGERPRINT).kind=='processing'
    now[0]+=timedelta(milliseconds=1)
    replacement=st.claim(ex,KEY,FINGERPRINT)
    assert replacement.kind=='owned' and replacement.fence>initial.fence
    with pytest.raises(Conflict):st.finalize(initial,ex,T)
    st.finalize(replacement,ex,T)
    assert counts(st)==(1,0,0)


@pytest.mark.parametrize('fixture',[OptimizedStore],indirect=True)
def test_followup_upload_preserves_issued_turns_and_evidence(fixture):
    from app.models import Followup,ConversationTurn,EvidenceAssessment
    st,ex,_,_,_=fixture
    prior=st.repo.add_attempt(ex,T)
    question=Followup(id=str(uuid.uuid4()),dimension='inference',index=0,question='Synthetic local follow-up?')
    conversation=st.repo.conversation(ex)
    conversation.state='AWAITING_FOLLOWUP'
    conversation.turns=[ConversationTurn(attempt_id=prior,text=T.text)]
    conversation.followups=[question];conversation.active_followup=question
    conversation.assessment=EvidenceAssessment(units=[dict(dimension='inference',index=0,status='insufficient_evidence',evidence='',question=question.question)],feedback='Synthetic local feedback.',transcription_concern=False,concern_reason='')
    st.repo.save_conversation(conversation)
    def snapshot():
        with st.repo.read() as db:
            return [list(db.execute(select(table).order_by(*table.primary_key.columns)).mappings())
                    for table in (s.followups,s.turns,s.assessments,s.evidence)]
    before=snapshot()
    claim=st.claim(ex,KEY,FINGERPRINT)
    st.finalize(claim,ex,T)
    assert snapshot()==before
    saved=st.repo.conversation(ex)
    assert saved.active_followup==question and saved.turns==conversation.turns
    assert saved.pending_attempt_id==claim.operation and saved.pending_text is None
    assert counts(st)==(2,0,0)
