"""Read-only-state-safe route benchmark: disposable PG/schema008, fake providers.

Use the existing pg_delay_proxy.py for72ms RTT. AUDLI_ROUTE_BENCH_ROOT selects
baseline code. Never load production/provider credentials. No learner data used.
"""
import argparse,asyncio,json,os,sys,tempfile,threading
from contextvars import ContextVar
from datetime import datetime,timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
ROOT=Path(os.environ.get('AUDLI_ROUTE_BENCH_ROOT',Path(__file__).resolve().parents[1]));sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
for name in ('OPENAI_API_KEY','ELEVENLABS_API_KEY','AUDLI_DATABASE_URL'):
    if os.environ.get(name):raise ValueError('Remove production/provider environment values')
bootstrap=tempfile.TemporaryDirectory(prefix='audli-route-bootstrap-')
os.environ.update(AUDLI_PROVIDER='demo',AUDLI_AUTH_MODE='local',AUDLI_DATA_DIR=bootstrap.name,AUDLI_DURABLE_OPERATIONS='off')
import httpx
from sqlalchemy import event,text
from sqlalchemy.engine import make_url
from app.config import Settings
from app.main import create_app
from app.lesson import Lesson
from app.models import Transcription
from app.repository import PostgresRepository
from app.homework import Capabilities
from app.services.youtube import YouTubeProvider,parse_video
from scripts.bench_app import BenchProvider,learner_uuid,wav
from scripts.bench_turns import describe
from experiments.durable_claims.local import ADMIN
from experiments.release_readiness.rehearsal import production_shaped,ROLES
from experiments.release_readiness.migration import apply_candidate
current=ContextVar('route_benchmark',default=None)


async def run(url,seeds,folder,port):
    target=make_url(url).set(username='audli_route_bench',password='disposable-only',port=port)
    repository=PostgresRepository(target.render_as_string(hide_password=False),None)
    provider_connections=[]
    class Speech(BenchProvider):
        async def transcribe(self,*args):
            provider_connections.append(repository.engine.pool.checkedout());assert provider_connections[-1]==0
            await asyncio.sleep(.5);return Transcription(text='Yes, please.',confidence=.99,uncertainty=[])
    class Discovery:
        def capabilities(self):return Capabilities()
        async def search(self,plan):
            provider_connections.append(repository.engine.pool.checkedout());assert provider_connections[-1]==0
            await asyncio.sleep(.1);return ['abcdefghijk']
        async def metadata(self,ids,topic):
            return [parse_video({'id':identifier,'snippet':{'title':'Science of everyday life','description':'A short science explanation.','channelTitle':'Synthetic publisher','defaultAudioLanguage':'en-US','liveBroadcastContent':'none'},'contentDetails':{'duration':'PT3M','caption':'true'},'status':{'privacyStatus':'public','uploadStatus':'processed','embeddable':False}},topic) for identifier in ids]
        def validate(self,content,plan):return YouTubeProvider('').validate(content,plan)
    async def auth(request):
        await asyncio.sleep(.15)
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        if token not in {t for t,_ in seeds}:return httpx.Response(401)
        return httpx.Response(200,json={'id':learner_uuid(token),'email_confirmed_at':'yes','is_anonymous':False})
    identity=httpx.AsyncClient(transport=httpx.MockTransport(auth))
    settings=Settings(provider='demo',persistence='postgres',database_url=target.render_as_string(hide_password=False),auth_mode='supabase',supabase_url='https://bench.invalid',supabase_publishable_key='sb_publishable_fixture',data_dir=Path(folder),db_offload=True,db_lazy_ping=True,db_read_autocommit=True,db_state_only_transitions=True,per_learner_locks=False,lesson_limit_mode='enforce',homework_enabled=True,homework_daily_search_limit=10,upload_retry=True,upload_idempotency=True,durable_operations='claims',durable_fleet_drained=True,_env_file=None)
    app=create_app(settings,Speech(),repository,auth_client=identity);app.state.homework_engine.provider=Discovery()
    rows=[];peak=0;loopthread=threading.get_ident();lags=[];stopped=False
    @event.listens_for(repository.engine,'before_cursor_execute')
    def before(db,cursor,statement,parameters,ctx,many):ctx._route_started=perf_counter()
    @event.listens_for(repository.engine,'after_cursor_execute')
    def after(db,cursor,statement,parameters,ctx,many):
        record=current.get()
        if record is not None:
            ms=(perf_counter()-ctx._route_started)*1000;record['queries']+=1;record['database_ms']+=ms
            if threading.get_ident()==loopthread:record['loop_sql_ms']+=ms
    @event.listens_for(repository.engine,'checkout')
    def checkout(*args):
        nonlocal peak
        peak=max(peak,repository.engine.pool.checkedout())
    class Observe:
        async def __call__(self,scope,receive,send):
            if scope['type']!='http':return await app(scope,receive,send)
            path=scope['path'];route='plan' if path=='/api/plan' else 'decision' if path.endswith('/decision/attempts') else 'creation' if path.endswith('/homework') else 'other'
            row=dict(route=route,queries=0,database_ms=0,loop_sql_ms=0);token=current.set(row);started=perf_counter()
            try:await app(scope,receive,send)
            finally:
                row['http_ms']=(perf_counter()-started)*1000
                row['fresh_read_projection']=bool(scope.get('_audli_readonly_plan'))
                rows.append(row);current.reset(token)
    async def heartbeat():
        previous=perf_counter()
        while not stopped:
            await asyncio.sleep(.01);now=perf_counter();lags.append(max(0,(now-previous-.01)*1000));previous=now
    try:
      async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Observe()),base_url='http://lab',timeout=55) as client:
            for token,_ in seeds:assert (await client.get('/api/upload-policy',headers={'Authorization':'Bearer '+token})).status_code==200
            probe=asyncio.create_task(heartbeat())
            try:
                for _ in range(6):
                    response=await client.get('/api/plan',headers={'Authorization':'Bearer '+seeds[0][0]});assert response.status_code==200,response.text
                for token,lesson in seeds:
                    headers={'Authorization':'Bearer '+token,'X-Audli-Operation-Id':uuid4().hex}
                    offered=await client.post(f'/api/lessons/{lesson}/homework',headers=headers,json={});assert offered.status_code==200,offered.text
                    headers['X-Audli-Upload-Id']=uuid4().hex[:16];headers['X-Audli-Operation-Id']=headers['X-Audli-Upload-Id']
                    chosen=await client.post(f"/api/homework/{offered.json()['id']}/decision/attempts",headers=headers,files={'audio':('synthetic.wav',wav(),'audio/wav')});assert chosen.status_code==200,chosen.text
                    assert chosen.json()['status']=='assigned',chosen.text
            finally:stopped=True;await probe
      result={'schema':8,'claims':'claims','restricted_login':True,'database_rtt_ms':72,'no_state_cache':True,'routes':{}}
      for route in ('plan','creation','decision'):
        samples=[x for x in rows if x['route']==route]
        result['routes'][route]={k:describe([r[k] for r in samples]) for k in ('http_ms','database_ms','loop_sql_ms')}
        result['routes'][route]['queries']=[r['queries'] for r in samples]
        if route=='plan':result['routes'][route]['cold']=samples[0];result['routes'][route]['warm']=samples[1:]
      result['fresh_plan_projection_hits']=sum(bool(r.get('fresh_read_projection')) for r in rows if r['route']=='plan')
      result.update(max_event_loop_lag_ms=round(max(lags),2),peak_checked_out=peak,provider_connections_max=max(provider_connections),connections_after=repository.engine.pool.checkedout(),samples=rows)
      return result
    finally:repository.close();await identity.aclose()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--proxy-port',type=int,default=55498);args=parser.parse_args()
    assert make_url(ADMIN).host in ('127.0.0.1','localhost')
    with production_shaped() as (url,admin,owner),tempfile.TemporaryDirectory(prefix='audli-route-benchmark-') as folder:
        seeds=[]
        for i in range(6):
            token='bench-route-'+str(i);repo=PostgresRepository(url,learner_uuid(token));profile=repo.profile();profile.onboarding_status='complete';profile.interests=['science'];repo.save_profile(profile)
            at=datetime.now(timezone.utc);lesson=Lesson(id=str(uuid4()),name='Synthetic',focus='details',started_at=at,active_since=None,status='completed',phase='COMPLETED',completed_at=at)
            repo.create_lesson(lesson,limit_mode='enforce');repo.close();seeds.append((token,lesson.id))
        with admin.begin() as db:db.exec_driver_sql("CREATE ROLE audli_route_bench LOGIN PASSWORD 'disposable-only' NOSUPERUSER NOBYPASSRLS NOINHERIT")
        try:
            apply_candidate(owner,include_review=True,client_roles=tuple(v for k,v in ROLES.items() if k!='postgres'),server_role='audli_route_bench')
            result=asyncio.run(run(url,seeds,folder,args.proxy_port));args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='samples'}))
        finally:
            with admin.begin() as db:db.exec_driver_sql('DROP OWNED BY audli_route_bench');db.exec_driver_sql('DROP ROLE audli_route_bench')


if __name__=='__main__':main()
