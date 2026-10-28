"""Additional review regressions; all data/providers are synthetic."""
import asyncio
import pytest
from sqlalchemy import text,select
from sqlalchemy.exc import DBAPIError
from app.recognition import reliable_recognition
from .test_stage import fixture,upload,body,assessment,counts,http_app,T,complete


@pytest.mark.parametrize('confidence',[.54,.55,.69,.70,.84,.85])
def test_server_confidence_boundaries_remain_identical(fixture,confidence):
    st,ex,*_=fixture
    _,_,lease=st.claim(ex,'upload',{'upload_id':'0123456789abcdef','bytes_hash':'synthetic'})
    recognized=T.model_copy(update={'confidence':confidence})
    st.finish(lease,recognized)
    kind,_,_=st.claim(ex,'assess',body(st,ex,lease.id))
    assert (kind=='owned')==reliable_recognition(recognized,st.settings.min_transcription_confidence)
    assert counts(st)['evaluation_results']==0


def test_late_failure_rolls_back_evaluation_adaptation_session_and_evidence(fixture):
    st,ex,*_=fixture
    first=upload(st,ex);_,_,lease=st.claim(ex,'assess',body(st,ex,first));st.finish(lease,assessment(st,ex,lease))
    second=upload(st,ex,'1111111111111111');_,_,lease=st.claim(ex,'assess',body(st,ex,second));value=assessment(st,ex,lease)
    with st.repo.transaction() as db:
        db.execute(text("CREATE FUNCTION public.poc_fail_profile() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Injected late failure'; END $$"))
        db.execute(text('CREATE TRIGGER poc_fail_profile AFTER UPDATE ON learner_profiles FOR EACH ROW EXECUTE FUNCTION public.poc_fail_profile()'))
    with pytest.raises(DBAPIError):st.finish(lease,value)
    assert counts(st)=={'attempts':2,'conversation_turns':1,'evaluation_results':0,'adaptation_events':0}
    assert st.repo.profile().completed_attempts==0
    assert st.repo.conversation(ex).pending_attempt_id==second
    with st.repo.read() as db:assert db.execute(text("SELECT status FROM training_sessions WHERE user_id=CAST(:owner AS uuid)"),dict(owner=st.owner)).scalar_one()=='active'
    with st.repo.transaction() as db:db.execute(text('DROP TRIGGER poc_fail_profile ON learner_profiles'))
    st.finish(lease,value)
    assert st.repo.profile().completed_attempts==1 and counts(st)['evaluation_results']==1


def test_two_followup_cap_unknown_evidence_and_single_adaptation(fixture):
    st,ex,*_=fixture
    for n,key in enumerate(['0123456789abcdef','1111111111111111','2222222222222222']):
        a=upload(st,ex,key);_,_,lease=st.claim(ex,'assess',body(st,ex,a));value=assessment(st,ex,lease)
        if n>0:
            target=next(u for u in value.units if u.dimension==('main_idea' if n==1 else 'vocabulary') and u.index==0)
            target.status='insufficient_evidence';target.evidence='';target.question='Synthetic clarification?'
        result=st.finish(lease,value)
        assert result['followups_asked']<=2
    assert result['state']=='GIVING_FEEDBACK' and result['result']['evaluation']['insufficient_evidence']
    assert result['result']['adaptation']['decision']=='same'
    assert st.repo.profile().completed_attempts==1 and counts(st)['adaptation_events']==1
    assert counts(st)['conversation_turns']==3


def test_provider_and_cleanup_failure_never_expose_inputs_or_release_unknown_claim(fixture,tmp_path,monkeypatch,caplog):
    import httpx
    from .store import StageStore
    st,ex,*_=fixture;a=upload(st,ex)
    class Provider:
        calls=0
        async def assess(self,*args):self.calls+=1;raise RuntimeError('SYNTHETIC_SECRET_PROVIDER_INPUT')
    def failed_cleanup(*args):raise RuntimeError('SYNTHETIC_SECRET_CLEANUP')
    monkeypatch.setattr(StageStore,'abandon',failed_cleanup)
    async def run():
        p=Provider();app,auth=await http_app(st,tmp_path,monkeypatch,p)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local',headers={'Authorization':'Bearer local'}) as client:
            request=body(st,ex,a);request.pop('attempt_id')
            response=await client.post(f'/api/attempts/{a}/assess',json=request)
            assert response.status_code==503 and 'SECRET' not in response.text
            assert (await client.post(f'/api/attempts/{a}/assess',json=request)).status_code==202
            assert p.calls==1
        await auth.aclose()
    asyncio.run(run())
    assert 'SECRET' not in caplog.text
    assert st.repo.conversation(ex).pending_attempt_id==a and counts(st)['evaluation_results']==0


def test_guard_inventory_covers_every_current_learner_owned_table():
    from .schema import TABLES
    from app.storage import schema as s
    assert {t.name for t in TABLES}==set(s.metadata.tables)-set(s.NOT_LEARNER_OWNED)
