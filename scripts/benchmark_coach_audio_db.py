"""Six synthetic upload/assessment/audio turns; disposable PG, schema008, restricted LOGIN.

Run with AUDLI_TEST_POSTGRES_URL pointing to a disposable loopback cluster. Optional
--proxy-port uses the existing pg_delay_proxy.py (72ms RTT). No live providers.
The measured full turn is upload-start to audio-body-ready, excluding browser VAD
and the real phone/network. SQL text is normalized; parameters are never exported.
"""
import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
import json
import hashlib
import os
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter
from uuid import uuid4

ROOT = Path(os.environ.get('AUDLI_AUDIO_BENCH_ROOT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
# Collection-time app creation must never inherit deployment/provider credentials.
for key in ('OPENAI_API_KEY','ELEVENLABS_API_KEY','AUDLI_DATABASE_URL'):
    if os.environ.get(key):raise ValueError('Remove production/provider environment values')
bootstrap = tempfile.TemporaryDirectory(prefix='audli-audio-bootstrap-')
os.environ.update(AUDLI_PROVIDER='demo',AUDLI_AUTH_MODE='local',AUDLI_DATA_DIR=bootstrap.name,AUDLI_DURABLE_OPERATIONS='off')

import httpx
from sqlalchemy import event,text
from sqlalchemy.engine import make_url
from app.config import Settings
from app.main import create_app
from app.models import EvidenceAssessment,Transcription
from app.lesson import Lesson
from app.repository import PostgresRepository
from app.evaluation import expected_units
from app.storage import schema as s
from scripts.bench_app import BenchProvider,learner_uuid,wav,QUESTIONS
from experiments.durable_claims.local import seed,ADMIN
from experiments.release_readiness.rehearsal import production_shaped,ROLES
from experiments.release_readiness.migration import apply_candidate

active = ContextVar('audio_benchmark_request',default=None)


def describe(values):
    return dict(n=len(values),mean_ms=round(statistics.mean(values),2),median_ms=round(statistics.median(values),2),max_ms=round(max(values),2))


async def measure(url, seeds, directory, proxy_port, closing_grace=False):
    runtime = make_url(url).set(username='audli_audio_bench',password='disposable-only',port=proxy_port)
    repo = PostgresRepository(runtime.render_as_string(hide_password=False),None)
    provider_connections=[];provider_tts=[];tts_delays=[268,531,663,422,297,448];assess_delays=[6464,3606,4829,3211,3337,3720]
    class Provider(BenchProvider):
        calls=0
        async def transcribe(self,*args):
            provider_connections.append(repo.engine.pool.checkedout());assert provider_connections[-1]==0
            await asyncio.sleep(1.5)
            return Transcription(text='The team restored the previous version and tested the database before retrying.',confidence=.99)
        async def assess(self,content,turns):
            provider_connections.append(repo.engine.pool.checkedout());assert provider_connections[-1]==0
            await asyncio.sleep(assess_delays[self.calls]/1000)
            self.calls+=1
            missing={'details','inference'} if len(turns)==1 else {'inference'} if len(turns)==2 else set()
            return EvidenceAssessment(units=[dict(dimension=dim,index=i,status='insufficient_evidence' if dim in missing else 'demonstrated',evidence='' if dim in missing else turns[-1].text,question=QUESTIONS[dim] if dim in missing else '') for dim,units in expected_units(content).items() for i in range(len(units))],feedback='You understood the important information.',transcription_concern=False,concern_reason='')
        async def speak(self,*args,**kwargs):
            provider_connections.append(repo.engine.pool.checkedout());assert provider_connections[-1]==0
            before=perf_counter();await asyncio.sleep(tts_delays[len(provider_tts)]/1000);provider_tts.append((perf_counter()-before)*1000)
            return wav()
    tokens={token:learner_uuid(token) for token,_,_,_ in seeds}
    async def verify(request):
        await asyncio.sleep(.150)
        owner=tokens.get(request.headers.get('authorization','').removeprefix('Bearer '))
        return httpx.Response(200,json={'id':owner,'email_confirmed_at':'yes','is_anonymous':False}) if owner else httpx.Response(401)
    identity=httpx.AsyncClient(transport=httpx.MockTransport(verify))
    settings=Settings(provider='demo',persistence='postgres',database_url=runtime.render_as_string(hide_password=False),auth_mode='supabase',supabase_url='https://bench.invalid',supabase_publishable_key='sb_publishable_fixture',data_dir=Path(directory),db_offload=True,db_lazy_ping=True,db_read_autocommit=True,db_state_only_transitions=True,db_state_only_checkpoints=False,per_learner_locks=False,lesson_limit_mode='enforce',upload_retry=True,upload_idempotency=True,durable_operations='claims',durable_fleet_drained=True,_env_file=None)
    app=create_app(settings,Provider(),repo,auth_client=identity)
    statements=[];requests=[];peak=0;checkouts=0;lag=[];stop=False
    @event.listens_for(repo.engine,'before_cursor_execute')
    def before(db,cursor,statement,parameters,context,many):
        context._audio_started=perf_counter()
    @event.listens_for(repo.engine,'after_cursor_execute')
    def after(db,cursor,statement,parameters,context,many):
        current=active.get()
        if current is not None:
            item={'route':current['route'],'ms':round((perf_counter()-context._audio_started)*1000,3),'sql':' '.join(statement.split())}
            statements.append(item);current['sql'].append(item)
    @event.listens_for(repo.engine,'checkout')
    def checkout(*args):
        nonlocal peak,checkouts
        peak=max(peak,repo.engine.pool.checkedout());checkouts+=1
    class Observe:
        async def __call__(self,scope,receive,send):
            if scope['type']!='http':return await app(scope,receive,send)
            path=scope['path'];route='coach' if path.endswith('coach-audio') else 'assess' if path.endswith('/assess') else 'upload' if path.endswith('/attempts') else 'other'
            record={'route':route,'sql':[]};token=active.set(record);started=perf_counter()
            try:return await app(scope,receive,send)
            finally:
                record['total_ms']=(perf_counter()-started)*1000;requests.append(record);active.reset(token)
    async def heartbeat():
        previous=perf_counter()
        while not stop:
            await asyncio.sleep(.010);now=perf_counter();lag.append(max(0,(now-previous-.010)*1000));previous=now
    rows=[]
    try:
      async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Observe()),base_url='http://lab',timeout=55) as client:
            for token,_,_,_ in seeds:assert (await client.get('/api/upload-policy',headers={'Authorization':'Bearer '+token})).status_code==200
            probe=asyncio.create_task(heartbeat())
            try:
              for token,exercise,lesson,lesson_started in seeds:
                followup=None
                for index in range(3):
                    headers={'Authorization':'Bearer '+token,'X-Audli-Upload-Id':uuid4().hex[:16],'X-Audli-Operation-Id':uuid4().hex}
                    # P2 uses the same stable recording identity in both headers.
                    headers['X-Audli-Operation-Id']=headers['X-Audli-Upload-Id']
                    started=perf_counter()
                    upload=await client.post(f'/api/exercises/{exercise}/attempts',headers=headers,files={'audio':('synthetic.wav',wav(),'audio/wav')});assert upload.status_code==200,upload.text
                    answer=upload.json()
                    result=await client.post(f"/api/attempts/{answer['id']}/assess",headers={'Authorization':'Bearer '+token,'X-Audli-Operation-Id':uuid4().hex},json={'text':answer['transcription']['text'],'confirmed':True,'followup_id':followup,'hands_free':True});assert result.status_code==200,result.text
                    body=result.json();cue='feedback' if body['result'] else body['active_followup']['id']
                    # Optional coverage of the bounded closing-grace budget transaction.
                    if closing_grace and len(rows)==5:app.state.limits.clock=lambda:lesson_started+timedelta(seconds=600,milliseconds=100)
                    response=await client.post(f'/api/exercises/{exercise}/coach-audio',headers={'Authorization':'Bearer '+token,'X-Audli-Operation-Id':uuid4().hex,'Accept':'audio/*'},json={'cue_id':cue,'lesson_id':lesson,'lesson_revision':0});assert response.status_code==200,response.text
                    rows.append((perf_counter()-started)*1000)
                    followup=body.get('active_followup',{});followup=followup['id'] if followup else None
            finally:
                stop=True;await probe
      coaches=[r for r in requests if r['route']=='coach']
      with repo.engine.connect() as db:
        counts={table:int(db.execute(text('SELECT count(*) FROM '+table)).scalar_one()) for table in ['attempts','evaluation_results','adaptation_events']}
        active_slots=int(db.execute(text('SELECT count(*) FROM poc_account_slots WHERE active IS NOT NULL')).scalar_one())
      return {'scope':'local mocked upload-start to coaching-body-ready, not production speech-end latency','database_rtt_ms':72,'final_feedback_closing_grace':closing_grace,'claims':'claims','schema':8,'restricted_login':True,'full_turn':describe(rows),'audio_http':describe([r['total_ms'] for r in coaches]),'audio_database':describe([sum(q['ms'] for q in r['sql']) for r in coaches]),'audio_query_counts':[len(r['sql']) for r in coaches],'tts_provider':describe(provider_tts),'max_event_loop_lag_ms':round(max(lag),2),'peak_checked_out':peak,'provider_connections_max':max(provider_connections),'connections_after':repo.engine.pool.checkedout(),'active_slots_after':active_slots,'persisted':counts,'sql_profile':statements,'turns_ms':rows}
    finally:
        repo.close();await identity.aclose()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--proxy-port',type=int,default=55498);parser.add_argument('--closing-grace',action='store_true');args=parser.parse_args()
    assert make_url(ADMIN).host in ('127.0.0.1','localhost')
    with production_shaped() as (url,admin,owner),tempfile.TemporaryDirectory(prefix='audli-audio-bench-') as directory:
        seeds=[]
        for token in ['bench-a','bench-b']:
            repo,exercise=seed(url,token)
            profile=repo.profile();profile.onboarding_status='complete';repo.save_profile(profile)
            lesson=Lesson(id=str(uuid4()),started_at=datetime.now(timezone.utc),active_since=datetime.now(timezone.utc),phase='EXERCISE',exercise_id=exercise,name='Synthetic',focus='details')
            repo.create_lesson(lesson,limit_mode='enforce')
            with repo.transaction() as db:db.execute(s.lesson_exercises.insert().values(exercise_id=exercise,lesson_id=lesson.id,user_id=repo.learner_id))
            repo.close();seeds.append((token,exercise,lesson.id,lesson.started_at))
        with admin.begin() as db:db.exec_driver_sql("CREATE ROLE audli_audio_bench LOGIN PASSWORD 'disposable-only' NOSUPERUSER NOBYPASSRLS NOINHERIT")
        try:
            apply_candidate(owner,include_review=True,client_roles=tuple(v for k,v in ROLES.items() if k!='postgres'),server_role='audli_audio_bench')
            result=asyncio.run(measure(url,seeds,directory,args.proxy_port,args.closing_grace))
            result['source_hashes']=[{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'path':str(p.relative_to(ROOT))} for p in [ROOT/'app/conversation_api.py',ROOT/'app/storage/repository.py',ROOT/'app/claims/assessment_claims/bridge.py']]
            args.output.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({k:v for k,v in result.items() if k not in ('sql_profile','turns_ms')}))
        finally:
            with admin.begin() as db:db.exec_driver_sql('DROP OWNED BY audli_audio_bench');db.exec_driver_sql('DROP ROLE audli_audio_bench')


if __name__=='__main__':main()
