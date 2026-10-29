"""Real LOGIN role from first connection, including the independent shared pool."""
from uuid import uuid4
import asyncio
import pytest
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.repository import PostgresRepository
from app.config import Settings
from scripts.bench_app import BenchProvider, wav, learner_uuid
from experiments.durable_claims.local import seed
from .rehearsal import production_shaped, ROLES
from .migration import apply_candidate
from contextlib import contextmanager
from sqlalchemy import create_engine
from experiments.durable_claims.local import database


@contextmanager
def source_database(source):
    if source=='006':
        with production_shaped() as value:yield value
    else:
        # The actual fresh-install migration runner, not the 006 reconstruction.
        with database() as url:
            admin=create_engine(url)
            try:yield url,admin,admin
            finally:admin.dispose()


@pytest.mark.parametrize('source',['006','fresh'])
@pytest.mark.parametrize('mode',['bridge','claims','paused'])
def test_restricted_login_from_first_query(source,mode,tmp_path):
    with source_database(source) as (url,admin,owner):
        name='audli_runtime_'+uuid4().hex[:12]
        password=uuid4().hex
        with admin.begin() as db:
            # Synthetic local credential only. Never printed or used externally.
            db.exec_driver_sql(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS NOINHERIT")
        try:
            seeded,exercise=seed(url,'bench-a');seeded.close()
            apply_candidate(owner,include_review=source=='006',server_role=name,
                            client_roles=tuple(v for k,v in ROLES.items() if k!='postgres'))
            restricted=make_url(url).set(username=name,password=password).render_as_string(hide_password=False)
            repo=PostgresRepository(restricted,None)
            try:
                with repo.engine.connect() as db:
                    assert db.execute(text('SELECT current_user')).scalar_one()==name
                    assert db.execute(text('SELECT max(version) FROM schema_migrations')).scalar_one()==8
                    assert not db.execute(text("SELECT has_table_privilege(current_user,'schema_migrations','INSERT,UPDATE,DELETE,TRUNCATE')")).scalar_one()
                if mode=='claims':
                    from sqlalchemy.exc import DBAPIError
                    with pytest.raises(DBAPIError):
                        with repo.engine.begin() as db:db.exec_driver_sql('UPDATE accent_control SET enabled=1')
                    with admin.begin() as db:db.exec_driver_sql(f'REVOKE SELECT ON schema_migrations FROM {name}')
                    try:
                        with pytest.raises(RuntimeError):PostgresRepository(restricted,None)
                    finally:
                        with admin.begin() as db:db.exec_driver_sql(f'GRANT SELECT ON schema_migrations TO {name}')
                from app.main import create_app
                settings=Settings(provider='demo',auth_mode='supabase',supabase_url='https://bench.invalid',supabase_publishable_key='sb_publishable_bench',persistence='postgres',database_url=restricted,data_dir=tmp_path,db_offload=True,db_lazy_ping=True,db_read_autocommit=True,db_state_only_transitions=True,db_state_only_checkpoints=False,cors_enabled=True,allowed_origins='https://bench.invalid',upload_idempotency=True,durable_operations=mode,durable_fleet_drained=True,_env_file=None)
                identity=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'id':learner_uuid('bench-a'),'email_confirmed_at':'yes','is_anonymous':False})))
                class Provider(BenchProvider):
                    calls=0
                    async def transcribe(self,*args):
                        self.calls+=1
                        assert repo.engine.pool.checkedout()==0
                        from experiments.assessment_claims.test_stage import T
                        return T
                provider=Provider()
                for invalid in (settings.model_copy(update={'durable_operations':'off'}),settings.model_copy(update={'durable_operations':'claims','durable_fleet_drained':False})):
                    with pytest.raises(ValueError):create_app(invalid,provider,repo,auth_client=identity)
                app=create_app(settings,provider,repo,auth_client=identity)
                async def run():
                    async with app.router.lifespan_context(app):
                        controls=app.state.accent_controls
                        assert controls.read()['id']==1
                        with controls.transaction() as db:
                            assert db.execute(text('SELECT current_user')).scalar_one()==name
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
                            assert (await client.get('/api/health')).status_code==200
                            if mode=='paused':
                                paused=await client.get('/api/plan',headers={'Origin':'https://bench.invalid'})
                                assert paused.status_code==503
                                assert paused.headers['access-control-allow-origin']=='https://bench.invalid'
                            else:
                                path=f'/api/exercises/{exercise}/attempts'
                                headers={'Authorization':'Bearer local','X-Audli-Upload-Id':'0123456789abcdef'}
                                response=await client.post(path,headers=headers,files={'audio':('synthetic.wav',wav(),'audio/wav')})
                                assert response.status_code==200,response.text
                                replay=await client.post(path,headers=headers,files={'audio':('synthetic.wav',wav(),'audio/wav')})
                                assert replay.status_code==200 and replay.json()['id']==response.json()['id']
                                assert provider.calls==1
                                with repo.engine.connect() as db:
                                    identifier=str(db.execute(text("SELECT id FROM poc_stage_operations WHERE kind='upload'")).scalar_one())
                                status=await client.get(f'/api/operations/{identifier}',headers=headers)
                                assert status.status_code==200 and status.json()['status']=='complete'
                                assert (await client.get('/api/plan',headers=headers)).status_code==200
                asyncio.run(run())
                assert repo.engine.pool.checkedout()==0
                if mode=='claims':
                    # Reproduce Docker's app-only runtime: no experiments or tests
                    # import path and no owner-initialized repository injected.
                    import os,sys,subprocess,shutil,socket,time
                    image=tmp_path/'image';image.mkdir()
                    shutil.copytree(__import__('pathlib').Path(__file__).resolve().parents[2]/'app',image/'app',ignore=shutil.ignore_patterns('__pycache__'))
                    with socket.socket() as listener:
                        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
                    env={key:value for key,value in os.environ.items() if key in ('PATH','HOME','LANG','LD_LIBRARY_PATH')}
                    env.update(AUDLI_PROVIDER='demo',AUDLI_PERSISTENCE='postgres',AUDLI_DATABASE_URL=restricted,AUDLI_AUTH_MODE='supabase',AUDLI_SUPABASE_URL='https://bench.invalid',AUDLI_SUPABASE_PUBLISHABLE_KEY='sb_publishable_bench',AUDLI_DATA_DIR=str(tmp_path/'child-data'),AUDLI_UPLOAD_IDEMPOTENCY='true',AUDLI_DURABLE_OPERATIONS='claims',AUDLI_DURABLE_FLEET_DRAINED='true')
                    (image/'restricted_boot.py').write_text('from contextlib import asynccontextmanager\nfrom pathlib import Path\nfrom app.main import app\ndef build():\n    original=app.router.lifespan_context\n    @asynccontextmanager\n    async def lifecycle(app):\n        async with original(app):\n            yield\n        Path("shutdown-complete").write_text("complete")\n    app.router.lifespan_context=lifecycle\n    return app\n')
                    process=subprocess.Popen([sys.executable,'-m','uvicorn','--factory','restricted_boot:build','--host','127.0.0.1','--port',str(port)],cwd=image,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    try:
                        deadline=time.monotonic()+10;healthy=False
                        while time.monotonic()<deadline and process.poll() is None:
                            try:
                                healthy=httpx.get(f'http://127.0.0.1:{port}/api/health',timeout=.5).status_code==200
                                if healthy:break
                            except httpx.TransportError:pass
                            time.sleep(.05)
                        assert healthy,'Packaged restricted-role subprocess did not become healthy'
                    finally:
                        process.terminate()
                        try:process.wait(timeout=10)
                        except subprocess.TimeoutExpired:process.kill();process.wait();pytest.fail('Shutdown did not complete')
                    assert process.returncode in (0,-15)
                    assert (image/'shutdown-complete').read_text()=='complete'
                    # Managed Auth is synthesized, never copied from production.
                    from app.storage.repository import SQLProgressRepository
                    from .test_remediation import auth_surface
                    from app.claims.release_readiness.account_erasure import erase
                    other=SQLProgressRepository(admin,learner_uuid('bench-b'))
                    scoped=SQLProgressRepository(repo.engine,learner_uuid('bench-a'))
                    auth_surface(SQLProgressRepository(admin,scoped.learner_id),other)
                    with admin.begin() as db:
                        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA auth,storage TO {name}')
                        db.exec_driver_sql(f'GRANT SELECT ON storage.buckets TO {name}')
                        for table in ('users','sessions','refresh_tokens','flow_state','scim_users'):
                            db.exec_driver_sql(f'GRANT SELECT,DELETE ON auth.{table} TO {name}')
                            db.exec_driver_sql(f'ALTER TABLE auth.{table} ENABLE ROW LEVEL SECURITY')
                            db.exec_driver_sql(f'CREATE POLICY reviewed_erase ON auth.{table} TO {name} USING (true) WITH CHECK (true)')
                        db.exec_driver_sql(f'GRANT UPDATE(id) ON auth.users TO {name}')
                    assert erase(scoped,tmp_path/'exercise_audio')=={'deleted':True}
                    assert other.profile()

            finally:repo.close()
        finally:
            with admin.begin() as db:
                db.exec_driver_sql(f'DROP OWNED BY {name}')
                db.exec_driver_sql(f'DROP ROLE {name}')


def test_review_grant_artifact_executes_atomically_only_for_restricted_role():
    from pathlib import Path
    with production_shaped() as (url,admin,owner):
        apply_candidate(owner,include_review=True,client_roles=tuple(v for k,v in ROLES.items() if k!='postgres'))
        name='audli_grants_'+uuid4().hex[:12]
        with admin.begin() as db:db.exec_driver_sql(f'CREATE ROLE {name} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT')
        try:
            sql=(Path(__file__).resolve().parents[2]/'docs/aud94-startup-evidence/REVIEW-ONLY-GRANTS.sql').read_text().replace('reviewed_backend',name)
            from sqlalchemy.exc import DBAPIError
            failed=sql.replace('COMMIT;', "DO $$ BEGIN RAISE EXCEPTION 'local interrupted grants'; END $$; COMMIT;")
            with pytest.raises(DBAPIError):
                with admin.connect() as db:db.exec_driver_sql(failed);db.commit()
            with admin.connect() as db:
                assert not db.execute(text("SELECT has_table_privilege(:r,'schema_migrations','SELECT')"),{'r':name}).scalar_one()
            with admin.connect() as db:
                db.exec_driver_sql(sql)
                db.commit()
            with admin.connect() as db:
                assert db.execute(text("SELECT has_table_privilege(:r,'schema_migrations','SELECT')"),{'r':name}).scalar_one()
                assert not db.execute(text("SELECT has_table_privilege(:r,'schema_migrations','INSERT,UPDATE,DELETE,TRUNCATE')"),{'r':name}).scalar_one()
                assert not db.execute(text("SELECT has_column_privilege(:r,'accent_control','enabled','UPDATE')"),{'r':name}).scalar_one()
        finally:
            with admin.begin() as db:db.exec_driver_sql(f'DROP OWNED BY {name}');db.exec_driver_sql(f'DROP ROLE {name}')
