"""Coaching duplicates share durable ownership and never repeat provider work."""
import asyncio

import httpx
from sqlalchemy import select

from experiments.assessment_claims.test_stage import fixture, http_app
from app.claims.assessment_claims.integration import install
from app.claims.durable_claims.store import slots
from scripts.bench_app import BenchProvider, wav


def test_concurrent_audio_duplicate_is_processing_then_explicit_replay_is_provider_free(fixture,tmp_path,monkeypatch):
    import experiments.assessment_claims.integration as legacy
    monkeypatch.setattr(legacy,'install',install)
    st,exercise,*_=fixture
    async def run():
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        class Provider(BenchProvider):
            async def speak(self,*args,**kwargs):
                assert st.repo.engine.pool.checkedout()==0
                calls.append(True);entered.set();await release.wait();return wav()
        app,auth=await http_app(st,tmp_path,monkeypatch,Provider(),full=True)
        lesson=st.repo.lesson()
        headers={'Authorization':'Bearer local','X-Audli-Operation-Id':'audio-duplicate','Accept':'audio/*'}
        body={'cue_id':'summary','lesson_id':lesson.id,'lesson_revision':lesson.revision}
        path=f'/api/exercises/{exercise}/coach-audio'
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
                first=asyncio.create_task(client.post(path,headers=headers,json=body))
                await asyncio.wait_for(entered.wait(),5)
                duplicate=await client.post(path,headers=headers,json=body)
                assert duplicate.status_code==202 and duplicate.json()['processing']
                competing=await client.post(path,headers={**headers,'X-Audli-Operation-Id':'other-audio-request'},json=body)
                assert competing.status_code==409
                assert len(calls)==1 and st.repo.engine.pool.checkedout()==0
                release.set();saved=await first;assert saved.status_code==200
                # Completed stream receipts deliberately require the explicit replay
                # action; an identical POST must not regenerate speech.
                assert (await client.post(path,headers=headers,json=body)).status_code==409
                replay=await client.post(path,headers={**headers,'X-Audli-Operation-Id':'audio-replay'},json=body)
                assert replay.status_code==200 and replay.content==saved.content and len(calls)==1
                stale=await client.post(path,headers={**headers,'X-Audli-Operation-Id':'stale-audio-checkpoint'},
                    json={**body,'lesson_revision':lesson.revision+1})
                assert stale.status_code==409 and len(calls)==1
                assert st.repo.lesson().revision==lesson.revision
                with st.repo.read() as db:
                    assert db.execute(select(slots.c.active).where(slots.c.owner==st.owner)).scalar_one() is None
        finally:release.set();await auth.aclose()
    asyncio.run(run())
