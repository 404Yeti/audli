"""Fresh plan projection, bounded homework reads, and complete offload boundaries."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import event,select
from app import turntrace
from app.config import Settings
from app.main import create_app
from app.models import Transcription
from app.storage import homework as store,schema as s
from app.services.youtube import parse_video
from conftest import ASGIClient,FakeProvider
from test_persistence import repository_factory
from test_homework import video,Provider
from test_planner import complete,ready


def assignment(repo,index):
    offered=store.offer(repo,complete(repo).id)
    _,token=store.reserve(repo,offered.id,True,'science',30)
    return store.finish(repo,offered.id,token,[parse_video(video(str(index).zfill(11)),'science')])


def test_homework_list_batches_content_reads_and_get_is_one_owned_join(repository_factory):
    repo=repository_factory();ready(repo)
    saved=[assignment(repo,i) for i in range(6)]
    statements=[]
    def observe(*args):statements.append(args[2])
    event.listen(repo.engine,'before_cursor_execute',observe)
    try:
        values=store.list_assignments(repo)
        assert len(statements)==2 and {item.id for item in values}=={item.id for item in saved}
        statements.clear();assert store.get(repo,saved[0].id)==saved[0];assert len(statements)==1
    finally:event.remove(repo.engine,'before_cursor_execute',observe)
    other=repository_factory(str(uuid4()))
    assert store.list_assignments(other)==[]
    with pytest.raises(LookupError):store.decision_context(other,saved[0].id)
    store.change_status(repo,saved[0].id,'completed')
    assert store.get(repo,saved[0].id).status=='completed'  # No mutable-state TTL.


def test_used_discovery_candidates_have_bounded_queries(repository_factory):
    repo=repository_factory();ready(repo)
    for i in range(6):assignment(repo,i)
    offered=store.offer(repo,complete(repo).id);_,token=store.reserve(repo,offered.id,True,'science',30)
    statements=[]
    def observe(*args):statements.append(args[2])
    event.listen(repo.engine,'before_cursor_execute',observe)
    try:
        result=store.finish(repo,offered.id,token,[parse_video(video(str(i).zfill(11)),'science') for i in range(7)])
    finally:event.remove(repo.engine,'before_cursor_execute',observe)
    assert result.content.provider_id=='00000000006'
    assert len(statements)<=8  # Six already-owned candidates do not add per-record SELECTs.


def test_discovery_finish_failure_rolls_back_content_and_assignment(repository_factory):
    repo=repository_factory();ready(repo);offered=store.offer(repo,complete(repo).id)
    _,token=store.reserve(repo,offered.id,True,'science',30)
    def fail(db,cursor,statement,*args):
        if statement.upper().startswith('UPDATE HOMEWORK_ASSIGNMENTS'):raise RuntimeError('synthetic write failure')
    event.listen(repo.engine,'before_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):store.finish(repo,offered.id,token,[parse_video(video(),'science')])
    finally:event.remove(repo.engine,'before_cursor_execute',fail)
    assert store.get(repo,offered.id).status=='offered'
    with repo.read() as db:assert db.execute(select(s.listening_content.c.id)).first() is None


@pytest.mark.parametrize('offload',[False,True])
def test_plan_and_homework_database_boundaries_and_provider_release(repository_factory,tmp_path,audio_bytes,offload):
    repo=repository_factory();ready(repo);lesson=complete(repo);provider=FakeProvider();seen=[]
    async def transcribe(*args):
        assert repo.engine.pool.checkedout()==0
        return Transcription(text='Yes, please.',confidence=.99,uncertainty=[])
    provider.transcribe=transcribe
    app=create_app(Settings(provider='demo',data_dir=tmp_path,db_offload=offload,homework_enabled=True,homework_daily_search_limit=10,_env_file=None),provider,repo)
    app.state.homework_engine.provider=Provider()
    client=ASGIClient(app,provider)
    def observe(*args):seen.append(turntrace.on_loop())
    event.listen(repo.engine,'before_cursor_execute',observe)
    try:
        plan=client.get('/api/plan');assert plan.status_code==200
        offered=client.post(f'/api/lessons/{lesson.id}/homework',json={});assert offered.status_code==200
        response=client.post(f"/api/homework/{offered.json()['id']}/decision/attempts",files={'audio':('choice.wav',audio_bytes,'audio/wav')})
        assert response.status_code==200 and response.json()['status']=='assigned'
    finally:event.remove(repo.engine,'before_cursor_execute',observe)
    assert seen and set(seen)=={not offload}
    assert repo.engine.pool.checkedout()==0
