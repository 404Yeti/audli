"""Voice identity/short-choice regressions. No real provider requests or speech logs."""
import asyncio
import httpx
import pytest
from app.config import Settings
from app.main import create_app
from app.models import Transcription
from app.homework_voice import spoken_decision, reliable_choice, SHORT_ASSESSMENT_WARNING
from app.recognition import reliable_recognition
from app.services.tutor_tts import TutorTTSProvider, failure_reason
from conftest import ASGIClient, FakeProvider
from test_homework import client_for, Provider
from test_planner import complete, ready
from test_tutor_tts import mp3


@pytest.mark.parametrize('text,accept,topic', [
    ('Yeah, sure.',True,None),('Yes, please.',True,None),('That sounds good.',True,None),
    ('No, thanks.',False,None),('Maybe next time.',False,None),
    ('Can you find something about cybersecurity?',True,'cybersecurity'),
])
def test_natural_homework_choices(text,accept,topic):
    assert spoken_decision(text).model_dump()=={'accept':accept,'topic':topic}


@pytest.mark.parametrize('text',['maybe','yes but no','I guess','not sure','Can you find something not about science?'])
def test_ambiguous_or_mixed_choices_do_not_assign(text):
    assert spoken_decision(text) is None


@pytest.mark.parametrize('confidence,warnings,accepted',[
    (.65,[SHORT_ASSESSMENT_WARNING],True),(.64,[SHORT_ASSESSMENT_WARNING],False),
    (None,[SHORT_ASSESSMENT_WARNING],False),(.99,['uncertain'],False),
    (.99,[SHORT_ASSESSMENT_WARNING,'uncertain'],False),
])
def test_short_choice_gate_never_weakens_assessment(confidence,warnings,accepted):
    speech=Transcription(text='Yes please',confidence=confidence,uncertainty=warnings)
    assert reliable_choice(speech,.65) is accepted
    assert reliable_recognition(speech,.65) is False


@pytest.mark.parametrize('text,status',[('Yeah, sure.','assigned'),('Yes, please.','assigned'),('No, thanks.','skipped'),('Maybe next time.','skipped')])
def test_realistic_stt_short_warning_allows_only_reliable_choice(tmp_path,audio_bytes,monkeypatch,text,status,caplog):
    calls=[]
    async def transcribe(self,data,filename):
        calls.append(True)
        return Transcription(text=text,confidence=.99,uncertainty=[SHORT_ASSESSMENT_WARNING] if len(text.split())<3 else [])
    monkeypatch.setattr(FakeProvider,'transcribe',transcribe)
    with client_for(tmp_path) as client:
        repo=client.app.state.repository;lesson=complete(repo);before=repo.profile()
        offered=client.post(f'/api/lessons/{lesson.id}/homework',json={}).json()
        path=f"/api/homework/{offered['id']}/decision/attempts"
        with caplog.at_level('INFO',logger='uvicorn.error'):
            response=client.post(path,files={'audio':('reply.wav',audio_bytes,'audio/wav')})
        assert response.status_code==200,response.text
        assert response.json()['status']==status
        assert client.post(path,files={'audio':('reply.wav',audio_bytes,'audio/wav')}).json()['id']==offered['id']
        assert len(calls)==1
        assert repo.profile()==before and repo.lesson(lesson.id).status=='completed'
        assert text not in caplog.text


def test_fallback_is_disclosed_never_sticks_and_preferred_cache_recovers(tmp_path,mp3):
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(401,json={'detail':{'status':'quota_exceeded','message':'private learner secret'}}) if len(calls)==1 else httpx.Response(200,content=mp3)
    config=Settings(_env_file=None,provider='openai',data_dir=tmp_path,tutor_tts_provider='elevenlabs',elevenlabs_api_key='fixture-secret')
    base=FakeProvider()
    async def fallback(text,rate=.9):return mp3
    base.speak=fallback
    router=TutorTTSProvider(base,config,httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    app=create_app(config,router);client=ASGIClient(app,router)
    try:
        first=client.post('/api/recognition/acknowledgment-audio')
        assert first.status_code==200 and first.headers['x-audli-tutor-fallback']=='true'
        assert first.headers['x-audli-tutor-provider']=='openai'
        second=client.post('/api/recognition/acknowledgment-audio')
        assert second.status_code==200 and second.headers['x-audli-tutor-fallback']=='false'
        assert second.headers['x-audli-tutor-provider']=='elevenlabs'
        assert client.post('/api/recognition/acknowledgment-audio').content==second.content
        assert len(calls)==2
        assert all('/yKYzqEa22xh5PdidhN70/stream' in str(r.url) for r in calls)
    finally:
        app.state.repository.close();asyncio.run(router.close())


@pytest.mark.parametrize('status,reason',[('quota_exceeded','quota_exceeded'),('invalid_api_key','invalid_api_key'),('private learner secret','http_rejected')])
def test_error_diagnostics_allow_only_fixed_codes(status,reason):
    response=httpx.Response(401,json={'detail':{'status':status,'message':'private speech secret'}})
    assert asyncio.run(failure_reason(response))==reason


def test_homework_tutor_invitation_clarification_confirmation_use_original(tmp_path,mp3):
    calls=[]
    config=Settings(_env_file=None,provider='openai',data_dir=tmp_path,tutor_tts_provider='elevenlabs',elevenlabs_api_key='fixture-secret',homework_enabled=True,homework_daily_search_limit=5)
    router=TutorTTSProvider(FakeProvider(),config,httpx.AsyncClient(transport=httpx.MockTransport(lambda request:(calls.append(request) or httpx.Response(200,content=mp3)))))
    app=create_app(config,router);repo=app.state.repository;ready(repo)
    app.state.homework_engine.provider=Provider();client=ASGIClient(app,router)
    try:
        lesson=complete(repo);offered=client.post(f'/api/lessons/{lesson.id}/homework',json={}).json();path=f"/api/homework/{offered['id']}"
        for cue in ['invitation','clarification']:
            response=client.post(path+f'/{cue}-audio');assert response.status_code==200
            assert response.headers['x-audli-tutor-provider']=='elevenlabs'
        assert client.post(path+'/decision/text',json={'text':'Yes please'}).json()['status']=='assigned'
        assert client.post(path+'/confirmation-audio').headers['x-audli-tutor-fallback']=='false'
        assert client.post(path+'/clarification-audio').status_code==409
        assert len(calls)==3 and all('/yKYzqEa22xh5PdidhN70/stream' in str(r.url) for r in calls)
    finally:
        repo.close();asyncio.run(router.close())


def test_feedback_fallback_remains_owned_playable_and_recovers_on_next_request(tmp_path,mp3):
    from test_conversation import begin
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(503) if len(calls)==1 else httpx.Response(200,content=mp3)
    config=Settings(_env_file=None,provider='openai',data_dir=tmp_path,tutor_tts_provider='elevenlabs',elevenlabs_api_key='fixture-secret')
    base=FakeProvider()
    async def fallback(text,rate=.9):return mp3
    base.speak=fallback
    router=TutorTTSProvider(base,config,httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    app=create_app(config,router);client=ASGIClient(app,router)
    try:
        exercise=begin(client);path=f"/api/exercises/{exercise['id']}/coach-audio"
        first=client.post(path,json={'cue_id':'summary'},headers={'Accept':'audio/*'})
        assert first.status_code==200 and first.headers['x-audli-tutor-fallback']=='true'
        saved=client.get(path+'/summary');assert saved.status_code==200 and saved.headers['x-audli-tutor-fallback']=='true'
        recovered=client.post(path,json={'cue_id':'summary'},headers={'Accept':'audio/*'})
        assert recovered.status_code==200 and recovered.headers['x-audli-tutor-fallback']=='false'
        assert client.get(path+'/summary').headers['x-audli-tutor-fallback']=='false'
        assert client.post(path,json={'cue_id':'summary'},headers={'Accept':'audio/*'}).status_code==200
        assert len(calls)==2
        assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code==403
    finally:
        app.state.repository.close();asyncio.run(router.close())
