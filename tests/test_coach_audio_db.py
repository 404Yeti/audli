"""AUD-21: one owned preparation read, atomic cache saves and offloaded audio work."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event,select

from app.config import Settings
from app.main import create_app
from app.models import Conversation
from app.storage import schema as s
from app import turntrace
from conftest import ASGIClient,FakeProvider
from test_persistence import repository_factory


def seed(repo,exercise,audio_bytes):
    key=str(uuid4())
    repo.save_exercise(key,exercise,repo.profile().difficulty,key+'.wav',audio=audio_bytes)
    repo.save_conversation(Conversation(exercise_id=key,state='AWAITING_SUMMARY'))
    return key


def test_coach_snapshot_is_one_owned_read(repository_factory,exercise,audio_bytes):
    repo=repository_factory();other=repository_factory(str(uuid4()))
    key=seed(repo,exercise,audio_bytes)
    expected=(repo.exercise(key),repo.conversation(key),repo.profile().tutor_voice)
    statements=[]
    def observe(*args):statements.append(args[2])
    event.listen(repo.engine,'before_cursor_execute',observe)
    try:value=repo.coach_audio_snapshot(key)
    finally:event.remove(repo.engine,'before_cursor_execute',observe)
    assert len(statements)==1 and statements[0].lstrip().upper().startswith('SELECT')
    assert (value['exercise'],value['conversation'],value['tutor_voice'])==expected
    assert value['completed'] is None and value['result'] is None and value['lesson'] is None
    assert other.coach_audio_snapshot(key) is None


def test_cache_mapping_failure_rolls_back_audio_asset(repository_factory,exercise,audio_bytes):
    repo=repository_factory();key=seed(repo,exercise,audio_bytes);name=str(uuid4())+'.wav'
    real=repo.upsert
    def fail_mapping(db,table,*args,**kwargs):
        if table is s.coach_audio:raise RuntimeError('synthetic mapping failure')
        return real(db,table,*args,**kwargs)
    repo.upsert=fail_mapping
    with pytest.raises(RuntimeError,match='synthetic mapping failure'):
        repo.save_coach_audio(key,'summary',name,audio=audio_bytes)
    assert repo.audio_blob(name) is None and repo.coach_audio(key,'summary') is None


def test_cache_save_cannot_overwrite_other_owner_audio(repository_factory,exercise,audio_bytes):
    repo=repository_factory();other=repository_factory(str(uuid4()))
    own=seed(repo,exercise,audio_bytes);foreign=seed(other,exercise,audio_bytes)
    name=foreign+'.wav'
    with pytest.raises(ValueError,match='Audio belongs'):
        repo.save_coach_audio(own,'summary',name,audio=b'bad overwrite')
    assert other.audio_blob(name)==audio_bytes and repo.coach_audio(own,'summary') is None


def test_snapshot_is_offloaded_and_cached_replay_has_no_new_tts(repository_factory,tmp_path,exercise,audio_bytes,monkeypatch):
    repo=repository_factory();key=seed(repo,exercise,audio_bytes);provider=FakeProvider();calls=[];on_loop=[]
    original=type(repo).coach_audio_snapshot
    def observe(self,*args):on_loop.append(turntrace.on_loop());return original(self,*args)
    monkeypatch.setattr(type(repo),'coach_audio_snapshot',observe)
    async def speak(*args,**kwargs):
        assert repo.engine.pool.checkedout()==0
        calls.append(True);return audio_bytes
    provider.speak=speak
    app=create_app(Settings(provider='demo',db_offload=True,data_dir=tmp_path,_env_file=None),provider,repo)
    client=ASGIClient(app,provider);path=f'/api/exercises/{key}/coach-audio'
    denied=client.post(path,json={'cue_id':'feedback'},headers={'Accept':'audio/*'})
    assert denied.status_code==403 and not calls
    first=client.post(path,json={'cue_id':'summary'},headers={'Accept':'audio/*'})
    second=client.post(path,json={'cue_id':'summary'},headers={'Accept':'audio/*'})
    assert first.status_code==second.status_code==200 and first.content==second.content
    assert len(calls)==1 and on_loop==[False,False,False]


def test_cancel_during_tts_leaves_no_cache(repository_factory,tmp_path,exercise,audio_bytes):
    repo=repository_factory();key=seed(repo,exercise,audio_bytes)
    async def run():
        provider=FakeProvider();entered=asyncio.Event();release=asyncio.Event()
        async def speak(*args,**kwargs):
            assert repo.engine.pool.checkedout()==0
            entered.set();await release.wait();return audio_bytes
        provider.speak=speak
        app=create_app(Settings(provider='demo',db_offload=True,data_dir=tmp_path,_env_file=None),provider,repo)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            request=asyncio.create_task(client.post(f'/api/exercises/{key}/coach-audio',json={'cue_id':'summary'},headers={'Accept':'audio/*'}))
            await asyncio.wait_for(entered.wait(),3)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):await request
    asyncio.run(run())
    with repo.read() as db:assert db.execute(select(s.coach_audio).where(s.coach_audio.c.exercise_id==key)).first() is None
    assert repo.engine.pool.checkedout()==0
