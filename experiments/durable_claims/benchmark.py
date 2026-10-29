"""AUD-94 real-HTTP/local-PG benchmark; subprocess per architecture for import isolation.

Usage: python -m experiments.durable_claims.benchmark --root ... --mode current|combined|claims
Output includes raw numeric samples; no learner data or credentials.
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import importlib.metadata
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from time import perf_counter

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--mode', choices=['current','combined','claims','bridge','optimized'], required=True)
parser.add_argument('--claims-root',type=Path)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--repetitions',type=int,default=2)
parser.add_argument('--levels',default='1,2,4,8,16')
parser.add_argument('--db-port',type=int,default=55451)
args = parser.parse_args()
POC = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(args.root), str(args.root/'scripts'), str(POC)]
for key in list(os.environ):
    if key.startswith('AUDLI_'): del os.environ[key]
bootstrap = tempfile.TemporaryDirectory(prefix='audli-claims-bootstrap-')
os.environ.update(AUDLI_DATA_DIR=bootstrap.name, AUDLI_PROVIDER='demo', AUDLI_AUTH_MODE='local', AUDLI_PERSISTENCE='sqlite')

from sqlalchemy import select,func
from sqlalchemy.engine import make_url
import httpx
import uvicorn
from app.config import Settings
from app.main import create_app
from app.models import Transcription
from app.repository import PostgresRepository
from app.auth import LearnerAuthentication
from app.storage import schema as s
from bench_app import BenchProvider, learner_uuid, wav
from bench_turns import describe
from experiments.durable_claims.local import database, seed, ADMIN
from experiments.durable_claims.server_profile import ServerProfile
from experiments.durable_claims import metrics as metric_module
from experiments.durable_claims.metrics import Meter, RequestContext, recording_key


async def run():
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=32))
    levels = [int(n) for n in args.levels.split(',')]
    if not levels or any(n not in (1,2,4,8,16) for n in levels) or not 1 <= args.repetitions <= 5:
        raise ValueError('Experiment bounds exceeded')
    tokens = [f'bench-claims-{i}' for i in range(max(levels))]
    # Blocking setup is outside all measurement windows.
    with database(tokens) as direct, tempfile.TemporaryDirectory(prefix='audli-claims-http-') as directory:
        exercises = []
        for token in tokens:
            repo, exercise = await asyncio.to_thread(seed, direct, token)
            repo.close(); exercises.append(exercise)
        if args.mode in ('claims','bridge','optimized'):
            from experiments.durable_claims.store import create_schema
            admin_repo = PostgresRepository(direct, None)
            create_schema(admin_repo.engine)
            if args.mode=='optimized':
                from experiments.durable_claims.optimized_store import install_functions
                install_functions(admin_repo.engine)
            admin_repo.close()
        url = make_url(direct).set(port=args.db_port).render_as_string(hide_password=False)
        repository = PostgresRepository(url, None)
        async def identity(request):
            started=perf_counter()
            await asyncio.sleep(.300)
            meter.duration('auth_ms',(perf_counter()-started)*1000)
            token = request.headers.get('authorization','').removeprefix('Bearer ')
            if token not in tokens: return httpx.Response(401)
            return httpx.Response(200,json={'id':learner_uuid(token),'email_confirmed_at':'synthetic','is_anonymous':False})
        auth_client = httpx.AsyncClient(transport=httpx.MockTransport(identity))
        class Provider(BenchProvider):
            calls = 0
            calls_by_recording = {}
            active = 0
            async def transcribe(self,*unused):
                self.calls += 1
                key = recording_key.get()
                self.calls_by_recording[key] = self.calls_by_recording.get(key,0) + 1
                self.active+=1;started=perf_counter()
                try:
                    meter.stt();await asyncio.sleep(1.100);meter.stt()
                finally:
                    self.active-=1
                    meter.duration('provider_ms',(perf_counter()-started)*1000)
                return Transcription(text='Synthetic local recording response.',confidence=.99,uncertainty=[])
        provider = Provider()
        settings_args = dict(data_dir=Path(directory),provider='demo',persistence='postgres',database_url=url,
            auth_mode='supabase',supabase_url='https://bench.invalid',supabase_publishable_key='sb_publishable_bench',
            db_offload=True,db_lazy_ping=True,db_read_autocommit=True,db_state_only_transitions=True,
            db_state_only_checkpoints=False,per_learner_locks=True,upload_retry=True,upload_idempotency=True,_env_file=None)
        if args.mode == 'combined': settings_args.update(lease_admission=True,lease_admission_limit=1)
        settings = Settings(**settings_args)
        app = create_app(settings,provider,repository,auth_client=auth_client)
        if args.mode in ('claims','bridge','optimized'):
            from experiments.durable_claims.routes import install
            from experiments.durable_claims.store import Store
            store_type=Store
            if args.claims_root:
                from importlib import util
                path=args.claims_root/'experiments/durable_claims/store.py'
                spec=util.spec_from_file_location('poc_original_store',path)
                module=util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
                store_type=module.Store
            if args.mode=='optimized':
                from experiments.durable_claims.optimized_store import OptimizedStore
                store_type=OptimizedStore
            install(app,settings,provider,mode='bridge' if args.mode=='bridge' else 'claims',store_type=store_type)
        if app.middleware_stack is None: app.middleware_stack = app.build_middleware_stack()
        auth = app.middleware_stack
        while not isinstance(auth,LearnerAuthentication): auth = auth.app
        engines = [repository.engine,app.state.accent_controls.engine,auth.lease_engine]
        # Include any startup sessions in steady-state accounting by reconnecting them under instrumentation.
        for engine in engines: engine.dispose()
        meter = Meter(engines)
        metric_module.active_meter=meter
        server_profile=ServerProfile(ADMIN,make_url(direct).database)
        outer = RequestContext(app)
        sock = socket.socket(); sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(outer,lifespan='off',log_config=None,log_level='error',access_log=False))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if server_task.done(): await server_task
            await asyncio.sleep(.005)
        audio = Path(directory)/'synthetic.wav'; audio.write_bytes(wav())
        result = dict(source_hashes={str(p.relative_to(POC)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (POC/'experiments/durable_claims').glob('*') if p.suffix in ('.py','.sql','.mjs')},
            packages={p:importlib.metadata.version(p) for p in ('SQLAlchemy','psycopg','fastapi','httpx','pydantic','openai','uvicorn')},python=sys.version.split()[0],
            mode=args.mode,root_revision=subprocess.check_output(['git','-C',str(args.root),'rev-parse','HEAD'],text=True).strip(),
            claims_store_sha256=hashlib.sha256((args.claims_root/'experiments/durable_claims/store.py').read_bytes()).hexdigest() if args.claims_root else None,
            claims_source_revision=subprocess.check_output(['git','-C',str(args.claims_root),'rev-parse','HEAD'],text=True).strip() if args.claims_root else None,
            database_rtt_ms=72,auth_ms=300,stt_ms=1100,per_learner_locks=True,
            repetitions=args.repetitions,measure='upload start including policy read through complete JSON acknowledgment',waves=[])
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=90) as client:
            # Warm identity lookups and repository pool without creating attempts.
            await asyncio.gather(*(client.get('/api/upload-policy',headers={'Authorization':'Bearer '+token}) for token in tokens))
            for level in levels:
                for repetition in range(args.repetitions):
                    # Clear only pending upload checkpoints, outside measured windows. No assessment is simulated.
                    for token, exercise in zip(tokens,exercises):
                        def clear():
                            repo = PostgresRepository(direct,learner_uuid(token))
                            c = repo.conversation(exercise); c.pending_attempt_id = c.pending_text = None
                            repo.save_conversation(c); repo.close()
                        await asyncio.to_thread(clear)
                    meter.reset(); server_profile.start(); before_calls = provider.calls
                    provider.calls_by_recording.clear()
                    wave = len(result['waves']) + 1
                    spec = dict(root=str(args.root),mode='claims' if args.mode=='optimized' else args.mode,base=f'http://127.0.0.1:{port}',audio=str(audio),learners=[
                        dict(token=t,exercise=e,key=f'{wave:08x}{i:08x}') for i,(t,e) in enumerate(zip(tokens[:level],exercises[:level]))])
                    spec_file = Path(directory)/'wave.json'; spec_file.write_text(json.dumps(spec))
                    started = perf_counter()
                    child = await asyncio.create_subprocess_exec('node','--experimental-strip-types',str(POC/'experiments/durable_claims/client.mjs'),
                        str(spec_file),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    stdout,stderr = await child.communicate()
                    if child.returncode:
                        raise RuntimeError(stderr.decode()[-3000:])
                    rows = json.loads(stdout)
                    # A browser timeout does not cancel server work. Observe all admitted work before counting writes.
                    deadline = perf_counter() + 30
                    while (meter.held or outer.active or provider.active) and perf_counter() < deadline: await asyncio.sleep(.01)
                    if meter.held or outer.active or provider.active: raise RuntimeError('Unsettled connections after bounded observation')
                    metrics = meter.summary()
                    metrics['server']=server_profile.finish()
                    calls = provider.calls-before_calls
                    completed = sum(row['ok'] for row in rows)
                    metrics.update(learners=level,repetition=repetition,automatic=completed,manual=level-completed,
                        latency_ms=describe([row['ms'] for row in rows if row['ok']]),all_outcome_latency_ms=describe([row['ms'] for row in rows]),
                        wall_ms=round((perf_counter()-started)*1000,1),provider_calls=calls,
                        extra_provider_calls=sum(max(0,count-1) for count in provider.calls_by_recording.values()),sends=sum(row['sends'] for row in rows),
                        max_sends=max(row['sends'] for row in rows),polls=sum(row['polls'] for row in rows),replays=sum(row['replay'] for row in rows),raw=rows)
                    def writes():
                        with repository.read() as db:
                            ids=[attempt_id_for(learner_uuid(row['token']),row['exercise'],row['key']) for row in spec['learners']]
                            return db.execute(select(func.count()).select_from(s.attempts).where(s.attempts.c.id.in_(ids))).scalar_one()
                    from app.upload_idempotency import attempt_id_for
                    # Count outside metrics snapshot.
                    metrics['persisted_attempts'] = await asyncio.to_thread(writes)
                    result['waves'].append(metrics)
                    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2)+'\n')
                    print(json.dumps({k:metrics[k] for k in ('learners','repetition','automatic','manual','latency_ms','peak_physical_sessions','connection_seconds','provider_calls')}),flush=True)
        server_profile.close(); metric_module.active_meter=None
        server.should_exit=True; await server_task
        auth.lease_engine.dispose(); app.state.accent_controls.close(); repository.close(); await auth_client.aclose()
    bootstrap.cleanup()


if __name__=='__main__': asyncio.run(run())
