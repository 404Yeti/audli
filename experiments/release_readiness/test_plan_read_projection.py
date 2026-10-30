"""Fresh plan reads retain claim admission and observe authoritative changes."""
import asyncio
import httpx
import pytest
from sqlalchemy import event
from experiments.assessment_claims.test_stage import fixture, http_app
from scripts.bench_app import BenchProvider
from app.claims.assessment_claims.integration import install
from app.claims.assessment_claims.bridge import BridgeStore, writer


def test_fresh_projection_has_no_writes_and_dirty_profile_uses_fenced_fallback(fixture,tmp_path,monkeypatch):
    import experiments.assessment_claims.integration as old
    monkeypatch.setattr(old,'install',install)
    st,*_=fixture
    async def run():
        app,auth=await http_app(st,tmp_path,monkeypatch,BenchProvider(),full=True)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local'}) as client:
                first=await client.get('/api/plan');assert first.status_code==200
                statements=[]
                def observe(*args):statements.append(args[2].strip().upper())
                event.listen(st.repo.engine,'before_cursor_execute',observe)
                try:
                    second=await client.get('/api/plan');assert second.json()==first.json()
                    assert not any(sql.startswith(('INSERT','UPDATE','DELETE')) for sql in statements)
                finally:event.remove(st.repo.engine,'before_cursor_execute',observe)
                store=BridgeStore(st.repo)
                _,_,context=store.claim('profile-change','a'*64)
                token=writer.set(context)
                try:
                    profile=st.repo.profile();profile.goal='Understand scientific interviews'
                    st.repo.save_profile(profile)
                    store.finish(context,{'status':200,'body':{}})
                finally:writer.reset(token)
                changed=await client.get('/api/plan');assert changed.status_code==200
                assert changed.json()['revision']>first.json()['revision']
                assert (await client.get('/api/plan')).json()==changed.json()
                _,_,_=store.claim('active-provider','b'*64)
                assert (await client.get('/api/plan')).status_code==409
        finally:await auth.aclose()
    asyncio.run(run())


def test_readonly_projection_rejects_mutation_arguments(fixture):
    st,*_=fixture
    with pytest.raises(ValueError):st.repo.roadmap(readonly=True,projection=True,revision=1)
