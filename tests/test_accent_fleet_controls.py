"""Real SQLite/Postgres transactions; HTTP provider fixtures never spend credits."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from app.config import Settings
from app.main import create_app
from app.storage import schema as s
from app.storage.accent_controls import SharedAccentControls, LEASE_SECONDS
from app.storage.repository import utcnow
from app.accent_practice import passage_audio
from test_persistence import repository_factory
from test_accent_practice import VOICE
from accent_control_helpers import scope
from conftest import FakeProvider
from test_auth import USER_A, USER_B, auth_response
from app.models import AccentPreferences, Transcription


def settings(**kwargs):
    return Settings(_env_file=None,elevenlabs_api_key='fixture',passage_voices={'british':VOICE},
        **({'accent_practice_enabled':True,'passage_tts_daily_characters':100,'passage_tts_max_concurrent':2}|kwargs))


@pytest.fixture
def fleet(repository_factory):
    root=repository_factory(None)
    config=settings()
    a=SharedAccentControls(root.engine,config)
    b=SharedAccentControls(root.engine,config)
    # Two independently pooled application instances; migration remains disabled by default.
    assert a.read()['enabled']==0 and a.read()['characters']==0
    a.configure(enabled=True,daily_limit=100,concurrent_limit=2)
    try:
        yield root,config,a,b
    finally:
        a.close();b.close()


def learner(root,config):
    repo=root.for_learner(str(uuid4()))
    return repo,scope(repo,config)


def admit(control,repo,lesson,count=10):
    return control.reserve(repo.learner_id,lesson.id,lesson.exercises_completed,'british',count)


def test_atomic_fleet_concurrency_and_budget(fleet):
    root,config,a,b=fleet
    scopes=[learner(root,config) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        grants=list(pool.map(lambda item:admit((a,b)[item[0]%2],*item[1]),enumerate(scopes)))
    accepted=[grant for grant in grants if grant]
    assert len(accepted)==2 and a.read()['characters']==20
    for grant in accepted:a.finish(grant[0],'success')
    a.configure(enabled=True,daily_limit=25,concurrent_limit=2)
    repo,lesson=learner(root,config)
    assert not admit(b,repo,lesson,6)
    assert admit(b,repo,lesson,5) and a.read()['characters']==25


def test_idempotency_restart_recovery_and_no_refunds(fleet):
    root,config,a,b=fleet;repo,lesson=learner(root,config)
    grant=admit(a,repo,lesson)
    assert grant and not admit(b,repo,lesson)
    # A recreated controller sees the same charges and scope tombstone.
    restart=SharedAccentControls(root.engine,config)
    try:
        assert restart.read()['characters']==10 and not admit(restart,repo,lesson)
        with root.engine.begin() as db:
            db.execute(update(s.accent_reservations).where(s.accent_reservations.c.token==grant[0])
                       .values(lease_until=utcnow()-timedelta(seconds=1)))
        other,next_lesson=learner(root,config)
        assert admit(restart,other,next_lesson)
        with root.engine.connect() as db:
            row=db.execute(select(s.accent_reservations).where(s.accent_reservations.c.token==grant[0])).mappings().one()
        assert row['state']=='abandoned' and restart.read()['characters']==20
        assert not admit(restart,repo,lesson)  # Even after expiry, never repeat a paid scope.
        restart.configure(enabled=False)
        restart.configure(enabled=True)
        assert restart.read()['characters']==20
    finally:restart.close()


@pytest.mark.parametrize('failure',['failed','cancelled'])
def test_uncertain_provider_work_holds_slot_until_lease(fleet,failure):
    root,config,a,b=fleet;a.configure(enabled=True,concurrent_limit=1)
    repo,lesson=learner(root,config);grant=admit(a,repo,lesson)
    a.finish(grant[0],failure)
    other,next_lesson=learner(root,config)
    assert not admit(b,other,next_lesson)
    with root.engine.begin() as db:
        db.execute(update(s.accent_reservations).where(s.accent_reservations.c.token==grant[0])
            .values(lease_until=utcnow()-timedelta(seconds=1)))
    assert admit(b,other,next_lesson)
    assert a.read()['characters']==20
    assert LEASE_SECONDS>config.passage_tts_timeout_seconds


def test_database_day_rollover_does_not_restore_used_scope(fleet):
    root,config,a,b=fleet;repo,lesson=learner(root,config);grant=admit(a,repo,lesson)
    a.finish(grant[0],'success')
    with root.engine.begin() as db:
        db.execute(update(s.accent_control).values(budget_day='2000-01-01',characters=100))
    other,next_lesson=learner(root,config)
    assert admit(b,other,next_lesson) and a.read()['characters']==10
    assert not admit(a,repo,lesson)


def test_scope_cannot_authorize_another_account_or_missing_consent(fleet):
    root,config,a,b=fleet;repo,lesson=learner(root,config)
    other=root.for_learner(str(uuid4()))
    assert not admit(b,other,lesson)
    lesson.accent_consent.state='declined';repo.save_lesson(lesson,lesson.revision)
    assert not admit(a,repo,lesson) and a.read()['characters']==0


def test_no_shared_state_no_paid_call_and_bounded_timeout(fleet,monkeypatch):
    root,config,a,b=fleet;repo,lesson=learner(root,config)
    def unavailable(*args):raise OSError('secret must never appear')
    monkeypatch.setattr(a,'read',unavailable);monkeypatch.setattr(a,'reserve',unavailable)
    class Provider:
        async def speech(self,content):return b'ordinary'
    async def run():
        assert await a.voices()=={}
        audio,voice=await passage_audio(config,Provider(),SimpleNamespace(script='story',speech_rate=.75),
            'british',a,owner=repo.learner_id,lesson=lesson)
        assert audio==b'ordinary' and voice.fallback
        monkeypatch.setattr('app.storage.accent_controls.CONTROL_TIMEOUT_SECONDS',.02)
        monkeypatch.setattr(a,'read',lambda:time.sleep(.2))
        at=time.perf_counter();assert await a.voices()=={}
        assert time.perf_counter()-at<.1
    asyncio.run(run())


@pytest.mark.parametrize('failure',[402,429,503,'timeout','cancel'])
def test_provider_failure_never_retries_or_refunds(fleet,monkeypatch,failure):
    root,config,a,b=fleet;repo,lesson=learner(root,config);calls=[]
    config.passage_tts_timeout_seconds=.01
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append(url)
            if failure in ('timeout','cancel'):await asyncio.sleep(1)
            return httpx.Response(failure,request=httpx.Request('POST',url))
    monkeypatch.setattr('app.accent_practice.httpx.AsyncClient',Client)
    class Provider:
        async def speech(self,content):return b'ordinary'
    async def run():
        kwargs=dict(owner=repo.learner_id,lesson=lesson)
        content=SimpleNamespace(script='story',speech_rate=.75)
        if failure=='cancel':
            config.passage_tts_timeout_seconds=1
            task=asyncio.create_task(passage_audio(config,Provider(),content,'british',a,**kwargs))
            while not calls:await asyncio.sleep(.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            audio,voice=await passage_audio(config,Provider(),content,'british',a,**kwargs)
            assert audio==b'ordinary' and voice.fallback
        audio,voice=await passage_audio(config,Provider(),content,'british',b,**kwargs)
        assert audio==b'ordinary' and voice.fallback and len(calls)==1
    asyncio.run(run())
    assert a.read()['characters']==5


def test_disable_two_authenticated_instances_and_measure(fleet,tmp_path):
    root,config,a,b=fleet
    for owner in (USER_A,USER_B):
        repo=root.for_learner(owner);profile=repo.profile();profile.onboarding_status='complete'
        profile.accent_preferences=AccentPreferences(status='preferred',accents=['british']);repo.save_profile(profile)
    config=settings(data_dir=tmp_path,auth_mode='supabase',supabase_url='https://project.supabase.co',supabase_publishable_key='sb_publishable_test')
    async def run():
        auth=[httpx.AsyncClient(transport=httpx.MockTransport(auth_response)) for _ in range(2)]
        apps=[create_app(config,FakeProvider(),root,client) for client in auth]
        clients=[httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') for app in apps]
        try:
            assert await apps[0].state.accent_controls.voices()
            a.configure(enabled=False);at=time.perf_counter()
            observations=await asyncio.gather(*[app.state.accent_controls.voices() for app in apps])
            elapsed=(time.perf_counter()-at)*1000
            assert observations==[{},{}] and elapsed<2000
            print(f'DISABLE_MEASURE backend={root.engine.dialect.name} instances=2 elapsed_ms={elapsed:.3f}')
            responses=await asyncio.gather(*[client.post('/api/lessons/start',headers={'Authorization':f'Bearer valid-{suffix}'})
                for client,suffix in zip(clients,['a','b'])])
            assert all(r.status_code==200 for r in responses)
            assert all('Want to practice' not in r.json()['prompt'] for r in responses)
            assert (await clients[0].post('/api/lessons/start')).status_code==401
            alien=responses[1].json()['id']
            assert (await clients[0].get(f'/api/lessons/{alien}',headers={'Authorization':'Bearer valid-a'})).status_code==404
            for owner in (USER_A,USER_B):
                lesson=root.for_learner(owner).lesson();lesson.phase='EXERCISE'
                root.for_learner(owner).save_lesson(lesson,lesson.revision)
            results=await asyncio.gather(*[client.post(f'/api/lessons/{response.json()["id"]}/exercise',
                json={'revision':root.for_learner(owner).lesson().revision},headers={'Authorization':f'Bearer valid-{suffix}'})
                for client,response,owner,suffix in zip(clients,responses,[USER_A,USER_B],['a','b'])])
            assert all(r.status_code==200 for r in results)
            assert all(r.json()['exercise']['passage_voice']['accent']=='default' for r in results)
        finally:
            for app in apps:app.state.accent_controls.close()
            for client in clients+auth:await client.aclose()
    asyncio.run(run())


def test_disable_in_flight_and_reenable_does_not_reuse_revision(fleet,monkeypatch):
    root,config,a,b=fleet;repo,lesson=learner(root,config);calls=[]
    class Provider:
        async def speech(self,content):return b'ordinary'
    async def sanitize(data,kind):return data
    monkeypatch.setattr('app.audio.sanitize_generated_audio',sanitize)
    async def run():
        started=asyncio.Event();finish=asyncio.Event()
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def post(self,url,**kwargs):
                calls.append(url);started.set();await finish.wait()
                return httpx.Response(200,content=b'accent',request=httpx.Request('POST',url))
        monkeypatch.setattr('app.accent_practice.httpx.AsyncClient',Client)
        task=asyncio.create_task(passage_audio(config,Provider(),SimpleNamespace(script='story',speech_rate=.75),
            'british',a,owner=repo.learner_id,lesson=lesson))
        await started.wait();b.configure(enabled=False)
        other,next_lesson=learner(root,config)
        audio,voice=await passage_audio(config,Provider(),SimpleNamespace(script='story',speech_rate=.75),
            'british',b,owner=other.learner_id,lesson=next_lesson)
        assert audio==b'ordinary' and voice.fallback and len(calls)==1
        b.configure(enabled=True);finish.set();audio,voice=await task
        assert audio==b'ordinary' and voice.fallback and a.read()['characters']==5
    asyncio.run(run())


def test_hard_worker_exit_leaves_durable_charge_and_lease(fleet):
    import subprocess
    import sys
    root,config,a,b=fleet;repo,lesson=learner(root,config)
    payload=json.dumps(dict(url=root.engine.url.render_as_string(hide_password=False),
        owner=repo.learner_id,lesson=lesson.id,number=lesson.exercises_completed))
    code='''
import json, os, sys
from sqlalchemy import create_engine
from app.config import Settings
from app.storage.accent_controls import SharedAccentControls
p=json.loads(sys.argv[1])
s=Settings(_env_file=None,elevenlabs_api_key='fixture',accent_practice_enabled=True,
 passage_tts_daily_characters=100,passage_voices={'british':{'voice_id':'a'*20,'validation':'Synthetic test fixture only'}})
c=SharedAccentControls(create_engine(p['url']),s)
assert c.reserve(p['owner'],p['lesson'],p['number'],'british',10)
os._exit(0)
'''
    result=subprocess.run([sys.executable,'-c',code,payload],capture_output=True,timeout=20)
    assert result.returncode==0
    assert b.read()['characters']==10 and not admit(b,repo,lesson)
    with root.engine.connect() as db:
        assert db.execute(select(func_count()).select_from(s.accent_reservations)
            .where(s.accent_reservations.c.lease_until>utcnow())).scalar_one()==1


def func_count():
    from sqlalchemy import func
    return func.count()


def test_postgres_locked_control_fails_closed_before_provider(fleet):
    root,config,a,b=fleet
    if root.engine.dialect.name!='postgresql':
        pytest.skip('Postgres row-lock timeout test')
    repo,lesson=learner(root,config)
    with root.engine.begin() as locked:
        locked.execute(select(s.accent_control).with_for_update()).one()
        at=time.perf_counter()
        result=asyncio.run(a.call(a.reserve,repo.learner_id,lesson.id,0,'british',10))
        assert result is None and time.perf_counter()-at<2
    assert b.read()['characters']==0


def test_expired_synthesis_lease_cannot_dispatch_or_return_voice(fleet):
    root,config,a,b=fleet;repo,lesson=learner(root,config);grant=admit(a,repo,lesson)
    with root.engine.begin() as db:
        db.execute(update(s.accent_reservations).where(s.accent_reservations.c.token==grant[0])
                   .values(lease_until=utcnow()-timedelta(seconds=1)))
    assert not asyncio.run(b.valid(grant))


def test_explicit_002_to_003_upgrade_preserves_learner_state(repository_factory):
    root=repository_factory();config=settings();lesson=scope(root,config)
    before=root.profile().model_dump();snapshot=root.lesson().model_dump()
    with root.engine.begin() as db:
        s.roadmaps.drop(db);s.accent_reservations.drop(db);s.accent_control.drop(db)
        db.execute(s.versions.delete().where(s.versions.c.version>=3))
    if root.engine.dialect.name=='postgresql':
        from scripts.migrate_postgres import migrate
        from app.storage.adapters import PostgresRepository
        url=root.engine.url.render_as_string(hide_password=False)
        with pytest.raises(RuntimeError,match='Expected migrations'):
            PostgresRepository(url,root.learner_id)
        migration=Settings(_env_file=None,persistence='postgres',database_url=url,learner_id=root.learner_id)
        assert migrate(migration) and not migrate(migration)
    else:
        from app.storage.adapters import initialize_sqlite
        initialize_sqlite(root)
    assert root.profile().model_dump()==before and root.lesson().model_dump()==snapshot
    control=SharedAccentControls(root.engine,config)
    try:
        assert control.read()['enabled']==0 and control.read()['daily_limit']==0
    finally:control.close()


def test_delayed_event_loop_never_accepts_stale_positive_flag(fleet,monkeypatch):
    root,config,a,b=fleet
    monkeypatch.setattr('app.storage.accent_controls.CONTROL_TIMEOUT_SECONDS',.02)
    def delayed_read():
        time.sleep(.01)
        return {'enabled':1}
    monkeypatch.setattr(a,'read',delayed_read)
    async def run():
        task=asyncio.create_task(a.voices())
        await asyncio.sleep(.002)
        time.sleep(.05)  # Deliberately delay both timeout callback and ready result.
        assert await task=={}
    asyncio.run(run())


def test_authenticated_instances_share_synthesis_limit_and_replay(fleet,tmp_path,monkeypatch,audio_bytes):
    root,config,a,b=fleet
    config=settings(data_dir=tmp_path,auth_mode='supabase',supabase_url='https://project.supabase.co',
        supabase_publishable_key='sb_publishable_test',passage_tts_daily_characters=100000,passage_tts_max_concurrent=1)
    a.configure(enabled=True,daily_limit=100000,concurrent_limit=1)
    lessons=[]
    for owner in (USER_A,USER_B):
        repo=root.for_learner(owner);profile=repo.profile();profile.onboarding_status='complete';repo.save_profile(profile)
        lessons.append(scope(repo,config))
    real_client=httpx.AsyncClient
    async def run():
        auth=[real_client(transport=httpx.MockTransport(auth_response)) for _ in range(2)]
        apps=[create_app(config,FakeProvider(),root,client) for client in auth]
        clients=[real_client(transport=httpx.ASGITransport(app=app),base_url='http://testserver') for app in apps]
        started=asyncio.Event();finish=asyncio.Event();calls=[]
        class PassageClient:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def post(self,url,**kwargs):
                calls.append(kwargs['json']);started.set();await finish.wait()
                return httpx.Response(200,content=audio_bytes,request=httpx.Request('POST',url))
        monkeypatch.setattr('app.accent_practice.httpx.AsyncClient',PassageClient)
        try:
            first=asyncio.create_task(clients[0].post(f'/api/lessons/{lessons[0].id}/exercise',
                headers={'Authorization':'Bearer valid-a'},json={'revision':lessons[0].revision}))
            await asyncio.wait_for(started.wait(),5)
            second=await clients[1].post(f'/api/lessons/{lessons[1].id}/exercise',
                headers={'Authorization':'Bearer valid-b'},json={'revision':lessons[1].revision})
            assert second.status_code==200 and second.json()['exercise']['passage_voice']['accent']=='default'
            assert second.json()['exercise']['passage_voice']['fallback']
            finish.set();response=await first
            assert response.status_code==200 and response.json()['exercise']['passage_voice']['accent']=='british'
            assert len(calls)==1 and a.read()['characters']==len(calls[0]['text'])
            assert root.for_learner(USER_A).lesson().accent_consent.state=='consumed'
            a.configure(enabled=False)
            replay=await clients[0].post(f'/api/lessons/{lessons[0].id}/exercise',
                headers={'Authorization':'Bearer valid-a'},json={'revision':response.json()['revision']})
            assert replay.status_code==200 and replay.json()['exercise']==response.json()['exercise']
            audio_url=replay.json()['exercise']['audio_url']
            stored=root.for_learner(USER_A).exercise(replay.json()['exercise']['id'])
            assert (await clients[0].get(audio_url,headers={'Authorization':'Bearer valid-a'})).content==root.for_learner(USER_A).audio_blob(stored['audio_name'])
            assert (await clients[1].get(audio_url,headers={'Authorization':'Bearer valid-b'})).status_code==404
            assert len(calls)==1
        finally:
            finish.set()
            for app in apps:app.state.accent_controls.close()
            for client in clients+auth:await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('change',['declined','expired','paused'])
def test_consent_is_rechecked_between_admission_and_dispatch(fleet,change):
    root,config,a,b=fleet;repo,lesson=learner(root,config);grant=admit(a,repo,lesson)
    latest=repo.lesson()
    if change=='declined':latest.accent_consent.state='declined'
    elif change=='expired':latest.accent_consent.accepted_at=utcnow()-timedelta(seconds=601)
    else:latest.status='paused';latest.active_since=None
    repo.save_lesson(latest,latest.revision)
    assert not asyncio.run(a.valid(grant,owner=repo.learner_id,lesson=lesson,accent='british'))
    assert b.read()['characters']==10


def test_pooler_ignored_startup_options_are_overridden_per_transaction(fleet):
    root, config, a, b = fleet
    if root.engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL session-pooler timeout contract')
    for control in (a, b):
        with control.engine.begin() as db:
            db.exec_driver_sql("SELECT set_config('statement_timeout','2min',false), "
                "set_config('lock_timeout','0',false), "
                "set_config('idle_in_transaction_session_timeout','0',false)")
        with control.transaction() as db:
            assert db.exec_driver_sql('SHOW statement_timeout').scalar_one() == '750ms'
            assert db.exec_driver_sql('SHOW lock_timeout').scalar_one() == '250ms'
            assert db.exec_driver_sql('SHOW idle_in_transaction_session_timeout').scalar_one() == '2s'
        assert control.read()['enabled'] == 1
