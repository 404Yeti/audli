"""Real disposable PostgreSQL, real domain functions and deterministic providers."""
import asyncio
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import threading
from uuid import uuid4

import pytest
from sqlalchemy import select,func,text,update
from sqlalchemy.exc import DBAPIError
from app.config import Settings
from app.models import Transcription,ExerciseContent,Conversation
from app.lesson import Lesson
from app.storage import schema as s
from scripts.bench_app import BenchProvider
from experiments.durable_claims.local import database,seed
from experiments.durable_claims.store import Conflict
from experiments.durable_claims.service import database_call
from .schema import install,operations
from .store import StageStore

T=Transcription(text='Team rolled back the update because the database migration removed a field.',confidence=.99,uncertainty=[])


@pytest.fixture
def fixture(tmp_path):
    with database() as url:
        repo,ex=seed(url,'bench-a');other,other_ex=seed(url,'bench-b')
        now=[datetime.now(timezone.utc)]
        for r,e in ((repo,ex),(other,other_ex)):
            lesson=Lesson(id=str(uuid4()),started_at=now[0],active_since=now[0],phase='EXERCISE',exercise_id=e,name='Synthetic',focus='details')
            r.create_lesson(lesson)
            with r.transaction() as db:db.execute(s.lesson_exercises.insert().values(exercise_id=e,lesson_id=lesson.id,user_id=r.learner_id))
        install(repo.engine)
        settings=Settings(data_dir=tmp_path,provider='demo',_env_file=None)
        st=StageStore(repo,settings,clock=lambda:now[0])
        yield st,ex,other,other_ex,now
        repo.close();other.close()


def upload(st,ex,key='0123456789abcdef'):
    kind,_,lease=st.claim(ex,'upload',{'upload_id':key,'bytes_hash':sha256(b'synthetic').hexdigest()})
    assert kind=='owned'
    return st.finish(lease,T)['id']


def body(st,ex,attempt):
    c=st.repo.conversation(ex)
    return dict(attempt_id=attempt,text=T.text,confirmed=True,hands_free=True,followup_id=c.active_followup.id if c.active_followup else None)


def assessment(st,ex,lease):
    c=st.repo.conversation(ex)
    from app.models import ConversationTurn
    row=st.repo.exercise(ex)
    turns=[*c.turns,ConversationTurn(attempt_id=lease.request['attempt_id'],text=T.text,followup=c.active_followup)]
    provider=BenchProvider()
    # Bypass sleep only; reuse the deterministic evidence fixture exactly.
    from app.evaluation import expected_units
    from app.models import EvidenceAssessment
    content=ExerciseContent.model_validate_json(row['content'])
    return EvidenceAssessment(units=[dict(dimension=d,index=i,status='insufficient_evidence' if d=='inference' and len(turns)==1 else 'demonstrated',
        evidence='' if d=='inference' and len(turns)==1 else T.text,question='Why did they wait?' if d=='inference' and len(turns)==1 else '')
        for d,units in expected_units(content).items() for i in range(len(units))],feedback='Synthetic feedback.',transcription_concern=False,concern_reason='')


def counts(st):
    with st.repo.read() as db:return {t.name:db.execute(select(func.count()).select_from(t)).scalar_one() for t in (s.attempts,s.turns,s.evaluations,s.events)}


def complete(st,ex):
    first=upload(st,ex)
    kind,_,lease=st.claim(ex,'assess',body(st,ex,first));assert kind=='owned'
    result=st.finish(lease,assessment(st,ex,lease));assert result['state']=='AWAITING_FOLLOWUP'
    second=upload(st,ex,'1111111111111111')
    kind,_,lease=st.claim(ex,'assess',body(st,ex,second));assert kind=='owned'
    result=st.finish(lease,assessment(st,ex,lease));assert result['state']=='GIVING_FEEDBACK'
    return first,second,result


def test_atomic_assessment_followup_final_adaptation_and_lesson_progress(fixture):
    st,ex,*_=fixture
    a,b,result=complete(st,ex)
    assert counts(st)=={'attempts':2,'conversation_turns':2,'evaluation_results':1,'adaptation_events':1}
    assert st.repo.profile().completed_attempts==1
    lesson=st.repo.lesson();assert lesson.exercises_completed==0
    _,_,lease=st.claim(ex,'ready',{});st.finish(lease)
    _,_,lease=st.claim(ex,'advance',{'revision':lesson.revision});st.finish(lease)
    assert st.repo.lesson().exercises_completed==1 and st.repo.lesson().exercise_id is None
    assert st.claim(ex,'advance',{'revision':lesson.revision})[0]=='done'
    assert st.repo.profile().completed_attempts==1
    assert st.claim(ex,'assess',body(st,ex,b))[0]=='done'


def test_concurrent_duplicates_and_finalizers(fixture):
    st,ex,*_=fixture;a=upload(st,ex);request=body(st,ex,a)
    barrier=threading.Barrier(8)
    def claim(_):barrier.wait();return st.claim(ex,'assess',request)
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(claim,range(8)))
    assert sum(r[0]=='owned' for r in results)==1 and sum(r[0]=='processing' for r in results)==7
    lease=next(r[2] for r in results if r[0]=='owned');value=assessment(st,ex,lease)
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:st.finish(lease,value),range(8)))
    assert all(r['active_followup']['id']==results[0]['active_followup']['id'] for r in results)
    assert counts(st)['conversation_turns']==1 and counts(st)['evaluation_results']==0


@pytest.mark.parametrize('action',['reset','pause','expire','onboarding'])
def test_conflicting_writers_invalidate_before_mutation(fixture,action):
    st,ex,*_=fixture;a=upload(st,ex)
    _,_,lease=st.claim(ex,'assess',body(st,ex,a));value=assessment(st,ex,lease)
    st.invalidate(ex,action)
    with pytest.raises((Conflict,DBAPIError)):st.finish(lease,value)
    assert counts(st)['evaluation_results']==0 and st.repo.profile().completed_attempts==0
    if action!='onboarding':assert st.repo.attempt(a) is not None


def test_unconverted_direct_writer_is_rejected_and_rolls_back(fixture):
    st,ex,*_=fixture;a=upload(st,ex)
    _,_,lease=st.claim(ex,'assess',body(st,ex,a))
    c=st.repo.conversation(ex);c.state='LISTENING'
    for mutate in [lambda:st.repo.save_conversation(c),lambda:st.repo.save_profile(st.repo.profile()),lambda:st.repo.save_lesson(st.repo.lesson(),st.repo.lesson().revision)]:
        with pytest.raises(DBAPIError):mutate()
    assert st.repo.conversation(ex).state=='ASSESSING'
    st.finish(lease,assessment(st,ex,lease))


def test_stale_expired_claim_cannot_commit(fixture):
    st,ex,_,_,now=fixture;a=upload(st,ex)
    request=body(st,ex,a);_,_,old=st.claim(ex,'assess',request);value=assessment(st,ex,old)
    now[0]+=timedelta(seconds=61)
    _,_,new=st.claim(ex,'assess',request)
    assert new.fence>old.fence
    with pytest.raises(Conflict):st.finish(old,value)
    st.finish(new,value)
    assert counts(st)['conversation_turns']==1


def test_transaction_failure_preserves_pending_evidence(fixture):
    st,ex,*_=fixture;a=upload(st,ex)
    _,_,lease=st.claim(ex,'assess',body(st,ex,a));value=assessment(st,ex,lease)
    with pytest.raises(DBAPIError):st.finish(lease,value,fail=True)
    assert st.repo.conversation(ex).pending_attempt_id==a and st.repo.attempt(a)
    assert counts(st)['conversation_turns']==0
    st.finish(lease,value)


def test_low_confidence_and_uncertain_assessment_never_adapt(fixture):
    st,ex,*_=fixture
    _,_,lease=st.claim(ex,'upload',{'upload_id':'0123456789abcdef','bytes_hash':'a'})
    st.finish(lease,T.model_copy(update={'confidence':.2}))
    assert st.claim(ex,'assess',body(st,ex,lease.id))[0]=='uncertain'
    request=body(st,ex,lease.id);request['hands_free']=False
    _,_,assessment_lease=st.claim(ex,'assess',request)
    value=assessment(st,ex,assessment_lease).model_copy(update={'transcription_concern':True})
    assert st.finish(assessment_lease,value)['uncertain']
    assert counts(st)['conversation_turns']==0 and st.repo.profile().completed_attempts==0


def test_concurrent_learners_and_account_isolation(fixture):
    st,ex,other,other_ex,_=fixture
    second=StageStore(other,st.settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda pair:complete(*pair),[(st,ex),(second,other_ex)]))
    assert st.repo.profile().completed_attempts==other.profile().completed_attempts==1
    with pytest.raises(DBAPIError):second.claim(ex,'upload',{'upload_id':'2222222222222222','bytes_hash':'a'})


def test_worker_process_death_and_stale_takeover(fixture):
    import subprocess,sys
    from pathlib import Path
    st,ex,_,_,now=fixture;a=upload(st,ex)
    request=body(st,ex,a)
    child=subprocess.Popen([sys.executable,'-c',"""
import sys,json,time
from app.config import Settings
from app.repository import PostgresRepository
from experiments.assessment_claims.store import StageStore
st=StageStore(PostgresRepository(sys.argv[1],sys.argv[2]),Settings(provider='demo',_env_file=None))
assert st.claim(sys.argv[3],'assess',json.loads(sys.argv[4]))[0]=='owned'
print('claimed',flush=True)
time.sleep(60)
""",st.repo.engine.url.render_as_string(hide_password=False),st.owner,ex,__import__('json').dumps(request)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=Path(__file__).resolve().parents[2])
    try:assert child.stdout.readline().strip()=='claimed'
    finally:child.kill();child.communicate(timeout=5)
    assert st.claim(ex,'assess',request)[0]=='processing'
    with st.repo.read() as db:now[0]=db.execute(select(operations.c.expires).where(operations.c.owner==st.owner,operations.c.kind=='assess')).scalar_one()+timedelta(seconds=1)
    _,_,lease=st.claim(ex,'assess',request);st.finish(lease,assessment(st,ex,lease))
    assert counts(st)['conversation_turns']==1 and st.repo.profile().completed_attempts==0


def test_cancel_during_provider_preserves_evidence_and_releases_claim(fixture):
    st,ex,*_=fixture;a=upload(st,ex)
    async def execute():
        entered=asyncio.Event()
        async def work():
            _,_,lease=await database_call(st.claim,ex,'assess',body(st,ex,a))
            try:
                assert st.repo.engine.pool.checkedout()==0
                entered.set();await asyncio.Event().wait()
            except asyncio.CancelledError:
                await database_call(st.abandon,lease);raise
        task=asyncio.create_task(work());await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(execute())
    assert st.claim(ex,'assess',body(st,ex,a))[0]=='owned'
    assert st.repo.conversation(ex).pending_attempt_id==a
    assert counts(st)['conversation_turns']==0


def test_cancel_during_final_transaction_keeps_ownership_until_settled(fixture):
    st,ex,*_=fixture;a=upload(st,ex)
    _,_,lease=st.claim(ex,'assess',body(st,ex,a));value=assessment(st,ex,lease)
    entered,release=threading.Event(),threading.Event()
    def finish():entered.set();release.wait(3);return st.finish(lease,value)
    async def execute():
        task=asyncio.create_task(database_call(finish));await asyncio.to_thread(entered.wait,2)
        task.cancel();await asyncio.sleep(0)
        assert not task.done();release.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(execute())
    assert st.claim(ex,'assess',body(st,ex,a))[0]=='done'
    assert counts(st)['conversation_turns']==1


def test_expiry_settlement_and_deadline_crossing(fixture):
    st,ex,_,_,now=fixture
    st.settings.lesson_limit_mode='enforce'
    a=upload(st,ex);_,_,lease=st.claim(ex,'assess',body(st,ex,a));value=assessment(st,ex,lease)
    # Keep claim fresh near the lesson deadline; an admitted answer may finish in grace.
    now[0]+=timedelta(seconds=590)
    _,_,lease=st.claim(ex,'assess',body(st,ex,a));value=assessment(st,ex,lease)
    now[0]+=timedelta(seconds=11)
    result=st.finish(lease,value)
    assert result['state']=='GIVING_FEEDBACK' and result['result']['evaluation']['insufficient_evidence']
    assert result['result']['adaptation']['decision']=='same'
    assert st.repo.profile().completed_attempts==1
    now[0]+=timedelta(seconds=180)
    st.settle(ex)
    assert st.repo.latest_lesson().status=='expired'


def test_expiry_invalidates_inflight_and_preserves_recording(fixture):
    st,ex,_,_,now=fixture;a=upload(st,ex)
    _,_,lease=st.claim(ex,'assess',body(st,ex,a));value=assessment(st,ex,lease)
    st.settings.lesson_limit_mode='enforce';now[0]+=timedelta(seconds=781)
    st.settle(ex)
    with pytest.raises(Conflict):st.finish(lease,value)
    assert st.repo.latest_lesson().status=='expired' and st.repo.attempt(a) is not None
    assert st.repo.profile().completed_attempts==0


def test_wrong_question_changed_input_and_bad_provider_evidence(fixture):
    st,ex,*_=fixture;a=upload(st,ex)
    request=body(st,ex,a);request['followup_id']=str(uuid4())
    with pytest.raises(Conflict):st.claim(ex,'assess',request)
    request=body(st,ex,a);_,_,lease=st.claim(ex,'assess',request)
    from dataclasses import replace
    forged=replace(lease,request={**request,'text':'different'})
    with pytest.raises(Conflict):st.finish(forged,assessment(st,ex,lease))
    invalid=assessment(st,ex,lease);invalid.units[0].evidence='invented quote'
    with pytest.raises(ValueError):st.finish(lease,invalid)
    assert st.repo.profile().completed_attempts==0 and st.repo.conversation(ex).pending_attempt_id==a


def test_owner_reassignment_and_lock_inversion_fail_closed(fixture):
    st,ex,other,_,_=fixture
    with pytest.raises(DBAPIError):
        with st.repo.transaction() as db:db.execute(update(s.exercises).where(s.exercises.c.id==ex).values(user_id=other.learner_id))
    with st.repo.engine.connect() as held:
        held.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==st.owner).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(st.repo.save_conversation,st.repo.conversation(ex))
            with pytest.raises(DBAPIError) as error:future.result(timeout=2)
            assert error.value.orig.sqlstate=='55P03'


async def http_app(st,tmp_path,monkeypatch,provider,mode='claims'):
    import httpx
    import app.auth as auth_module
    from app.main import create_app
    from .routes import install as install_routes
    async def identity(request):
        if request.headers.get('authorization')!='Bearer local':return httpx.Response(401)
        return httpx.Response(200,json={'id':st.owner,'email_confirmed_at':'yes','is_anonymous':False})
    config=Settings(provider='demo',data_dir=tmp_path,auth_mode='supabase',supabase_url='https://bench.invalid',supabase_publishable_key='sb_publishable_bench',persistence='postgres',database_url=st.repo.engine.url.render_as_string(hide_password=False),db_offload=True,db_lazy_ping=True,db_read_autocommit=True,db_state_only_transitions=True,per_learner_locks=True,upload_retry=True,upload_idempotency=True,_env_file=None)
    auth_client=httpx.AsyncClient(transport=httpx.MockTransport(identity))
    app=create_app(config,provider,st.repo,auth_client=auth_client)
    monkeypatch.setattr(auth_module,'lease_exempt',auth_module.lease_exempt)
    install_routes(app,config,provider,mode=mode)
    return app,auth_client


def test_real_http_full_pipeline_replay_and_conflicting_gate(fixture,tmp_path,monkeypatch):
    import httpx
    from scripts.bench_app import wav
    st,ex,*_=fixture
    class Provider:
        transcriptions=assessments=0
        async def transcribe(self,*args):
            self.transcriptions+=1;assert st.repo.engine.pool.checkedout()==0;return T
        async def assess(self,content,turns):
            self.assessments+=1;assert st.repo.engine.pool.checkedout()==0
            # Reuse the same pure deterministic fixture without DB access during wait.
            from app.models import EvidenceAssessment
            from app.evaluation import expected_units
            return EvidenceAssessment(units=[dict(dimension=d,index=i,status='insufficient_evidence' if d=='inference' and len(turns)==1 else 'demonstrated',evidence='' if d=='inference' and len(turns)==1 else T.text,question='Why did they wait?' if d=='inference' and len(turns)==1 else '') for d,units in expected_units(content).items() for i in range(len(units))],feedback='Synthetic.',transcription_concern=False,concern_reason='')
    async def run():
        p=Provider();app,auth_client=await http_app(st,tmp_path,monkeypatch,p)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local'}) as c:
            question=None
            for n,key in enumerate(['0123456789abcdef','1111111111111111']):
                path=f'/api/exercises/{ex}/attempts'
                response=await c.post(path,headers={'X-Audli-Upload-Id':key},files={'audio':('a.wav',wav(),'audio/wav')});assert response.status_code==200,response.text
                attempt=response.json()['id']
                replay=await c.post(path,headers={'X-Audli-Upload-Id':key},files={'audio':('a.wav',wav(),'audio/wav')})
                assert replay.json()['id']==attempt and replay.headers['x-audli-replayed']=='true'
                body={'text':T.text,'confirmed':True,'hands_free':True,'followup_id':question['id'] if question else None}
                assert (await c.post(f'/api/attempts/{attempt}/assess',json={**body,'evaluation':{'overall':1}})).status_code==422
                response=await c.post(f'/api/attempts/{attempt}/assess',json=body);assert response.status_code==200,response.text
                question=response.json()['active_followup']
                replay=await c.post(f'/api/attempts/{attempt}/assess',json=body);assert replay.status_code==200
            assert p.transcriptions==p.assessments==2
            assert (await c.post('/api/exercises')).status_code==409
            lesson=st.repo.lesson()
            assert (await c.post(f'/api/exercises/{ex}/conversation/ready')).status_code==200
            response=await c.post(f'/api/lessons/{lesson.id}/advance',json={'revision':lesson.revision})
            assert response.status_code==200,response.text
            assert response.json()['exercises_completed']==1
            assert 'script' not in str(response.json())
        await auth_client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('first',['legacy','bridge'])
def test_old_new_middleware_overlap_preserves_one_assessment(fixture,tmp_path,monkeypatch,first):
    import httpx
    from app.main import create_app
    from app.auth import LearnerAuthentication
    st,ex,*_=fixture;a=upload(st,ex);request=body(st,ex,a)
    async def run():
        entered,release,contended=asyncio.Event(),asyncio.Event(),asyncio.Event()
        class Provider:
            calls=0
            async def assess(self,content,turns):
                self.calls+=1;entered.set();await release.wait()
                from app.models import EvidenceAssessment
                from app.evaluation import expected_units
                return EvidenceAssessment(units=[dict(dimension=d,index=i,status='insufficient_evidence' if d=='inference' else 'demonstrated',evidence='' if d=='inference' else T.text,question='Why did they wait?' if d=='inference' else '') for d,units in expected_units(content).items() for i in range(len(units))],feedback='Synthetic.',transcription_concern=False,concern_reason='')
        p=Provider();new,auth_client=await http_app(st,tmp_path,monkeypatch,p,mode='bridge')
        # The legacy app has a separate per-learner asyncio lock; exclusion is PostgreSQL.
        settings=Settings(provider='demo',data_dir=tmp_path/'old',auth_mode='supabase',supabase_url='https://bench.invalid',supabase_publishable_key='sb_publishable_bench',persistence='postgres',database_url=st.repo.engine.url.render_as_string(hide_password=False),db_offload=True,per_learner_locks=True,_env_file=None)
        old=create_app(settings,p,st.repo,auth_client=auth_client);old.middleware_stack=old.build_middleware_stack()
        apps=[old,new] if first=='legacy' else [new,old]
        auth=apps[1].middleware_stack
        while not isinstance(auth,LearnerAuthentication):auth=auth.app
        take=auth._take_lease;loop=asyncio.get_running_loop()
        def attempt(*args):
            result=take(*args)
            if not result[1]:loop.call_soon_threadsafe(contended.set)
            return result
        auth._take_lease=attempt
        clients=[httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local'}) for app in apps]
        one=asyncio.create_task(clients[0].post(f'/api/attempts/{a}/assess',json={k:v for k,v in request.items() if k!='attempt_id'}))
        await asyncio.wait_for(entered.wait(),3)
        two=asyncio.create_task(clients[1].post(f'/api/attempts/{a}/assess',json={k:v for k,v in request.items() if k!='attempt_id'}))
        await asyncio.wait_for(contended.wait(),3)
        assert p.calls==1 and not two.done();release.set()
        responses=await asyncio.gather(one,two)
        assert all(r.status_code==200 for r in responses),[r.text for r in responses]
        assert responses[0].json()['active_followup']['id']==responses[1].json()['active_followup']['id']
        assert counts(st)['conversation_turns']==1 and p.calls==1
        for c in clients:await c.aclose()
        await auth_client.aclose()
    asyncio.run(run())


def test_wrong_reset_revision_rolls_back_invalidation(fixture):
    from fastapi import HTTPException
    st,ex,*_=fixture;a=upload(st,ex);request=body(st,ex,a)
    _,_,lease=st.claim(ex,'assess',request)
    with pytest.raises(HTTPException):st.invalidate(ex,'onboarding',expected_revision=123)
    assert st.claim(ex,'assess',request)[0]=='processing'
    st.finish(lease,assessment(st,ex,lease))


def test_duplicate_inflight_http_and_lost_wire_response(fixture,tmp_path,monkeypatch):
    import httpx,socket,uvicorn
    st,ex,*_=fixture;a=upload(st,ex);request=body(st,ex,a)
    request.pop('attempt_id')
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        class Provider:
            calls=0
            async def assess(self,content,turns):
                self.calls+=1;assert st.repo.engine.pool.checkedout()==0
                entered.set();await release.wait()
                from app.models import EvidenceAssessment
                from app.evaluation import expected_units
                return EvidenceAssessment(units=[dict(dimension=d,index=i,status='insufficient_evidence' if d=='inference' else 'demonstrated',evidence='' if d=='inference' else T.text,question='Why did they wait?' if d=='inference' else '') for d,units in expected_units(content).items() for i in range(len(units))],feedback='Synthetic.',transcription_concern=False,concern_reason='')
        p=Provider();app,auth_client=await http_app(st,tmp_path,monkeypatch,p)
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,lifespan='off',log_config=None,access_log=False));task=asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:await asyncio.sleep(.005)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer local'}) as client:
                first=asyncio.create_task(client.post(f'/api/attempts/{a}/assess',json=request))
                await asyncio.wait_for(entered.wait(),3);first.cancel()
                with pytest.raises(asyncio.CancelledError):await first
                duplicate=await client.post(f'/api/attempts/{a}/assess',json=request)
                assert duplicate.status_code==202 and p.calls==1
                release.set()
                for _ in range(100):
                    response=await client.get(f'/api/exercises/{ex}/conversation')
                    if response.json()['state']=='AWAITING_FOLLOWUP':break
                    await asyncio.sleep(.01)
                replay=await client.post(f'/api/attempts/{a}/assess',json=request)
                assert replay.status_code==200 and p.calls==1
                assert counts(st)['conversation_turns']==1
        finally:
            release.set();server.should_exit=True;await task;await auth_client.aclose()
    asyncio.run(run())


def test_explicit_record_again_never_drops_or_overwrites_ambiguous_evidence(fixture):
    st,ex,*_=fixture;a=upload(st,ex)
    request=body(st,ex,a);_,_,lease=st.claim(ex,'assess',request);value=assessment(st,ex,lease)
    with pytest.raises(Conflict):st.invalidate(ex,'record_again',expected_pending=str(uuid4()))
    assert st.claim(ex,'assess',request)[0]=='processing'
    st.invalidate(ex,'record_again',expected_pending=a)
    with pytest.raises(Conflict):st.finish(lease,value)
    assert st.repo.attempt(a) and st.repo.conversation(ex).pending_attempt_id is None
    new=upload(st,ex,'2222222222222222');assert new!=a
    assert counts(st)['attempts']==2 and counts(st)['conversation_turns']==0


def test_uncertain_finalizer_replays_uncertainty_not_success(fixture):
    st,ex,*_=fixture;a=upload(st,ex);_,_,lease=st.claim(ex,'assess',body(st,ex,a))
    value=assessment(st,ex,lease).model_copy(update={'transcription_concern':True})
    assert st.finish(lease,value)==st.finish(lease,value)=={'uncertain':True}
    assert counts(st)['conversation_turns']==0 and st.repo.profile().completed_attempts==0
