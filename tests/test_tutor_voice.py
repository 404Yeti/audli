"""Account settings and voice routing; all provider traffic uses MockTransport."""
import asyncio
import json
import httpx
import pytest
from pydantic import ValidationError
from app.config import Settings
from app.main import create_app
from app.models import LearnerProfile
from app.services.tutor_tts import TutorTTSProvider, TutorSpeech, tutor_cache_signature
from app.tutor_voice import VOICE_IDS, tutor_settings
from app.services.tutor_preview import existing_preview
from conftest import ASGIClient, FakeProvider
from test_auth import accounts, AccountClient, auth_response, settings_for, USER_A
from test_persistence import repository_factory
from test_tutor_tts import mp3


def test_default_and_old_snapshots_use_original():
    assert LearnerProfile().tutor_voice == 'original'
    assert LearnerProfile.model_validate({'name': 'Returning learner', 'completed_attempts': 12}).tutor_voice == 'original'
    with pytest.raises(ValidationError):
        LearnerProfile(tutor_voice='arbitrary-provider-id')


def test_preference_account_persistence_and_onboarding_preservation(accounts, repository_factory, tmp_path):
    a, b, _ = accounts
    before = a.get('/api/profile').json()['profile']
    assert a.get('/api/profile/tutor-voice').json()['voice'] == 'original'
    assert a.put('/api/profile/tutor-voice', json={'voice': 'male'}).json()['voice'] == 'male'
    expected = before | {'tutor_voice': 'male'}
    assert a.get('/api/profile').json()['profile'] == expected
    assert b.get('/api/profile/tutor-voice').json()['voice'] == 'original'
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(settings_for(tmp_path / 'restarted'), a.provider, repository_factory(None), auth)
    try:
        reauthenticated = AccountClient(app, a.provider, 'valid-a')
        assert reauthenticated.get('/api/profile/tutor-voice').json()['voice'] == 'male'
        state = a.post('/api/onboarding/start', json={'revision': 0}).json()
        assert a.post('/api/onboarding/heard', json={'revision': state['revision']}).json()['profile']['tutor_voice'] == 'male'
        assert a.put('/api/profile/tutor-voice', json={'voice': 'original'}).json()['voice'] == 'original'
        assert reauthenticated.get('/api/profile/tutor-voice').json()['voice'] == 'original'
    finally:
        app.state.repository.close(); asyncio.run(auth.aclose())


@pytest.mark.parametrize('payload', [{'voice':'bad'}, {'voice':VOICE_IDS['male']}, {'voice':'male','learner_id':USER_A}, {}, {'voice':None}])
def test_invalid_preference_cannot_mutate_profile(accounts, payload):
    a, _, _ = accounts
    before = a.get('/api/profile').json()
    assert a.put('/api/profile/tutor-voice', json=payload).status_code == 422
    assert a.get('/api/profile').json() == before


@pytest.mark.parametrize('method,path', [('GET','/api/profile/tutor-voice'),('PUT','/api/profile/tutor-voice'),('GET','/api/profile/tutor-voice/preview/male')])
def test_voice_routes_require_verified_auth(accounts, method, path):
    a, _, _ = accounts
    response = ASGIClient(a.app,a.provider).request(method,path,**({'json':{'voice':'male'}} if method=='PUT' else {}))
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'


def test_voice_settings_and_inflight_snapshot_never_mutate_passages(mp3):
    calls = []
    config = Settings(_env_file=None,tutor_tts_provider='elevenlabs',elevenlabs_api_key='fixture')
    original_signature = tutor_cache_signature(config)
    async def run():
        router = TutorTTSProvider(FakeProvider(),config,httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r:(calls.append(str(r.url)) or httpx.Response(200,content=mp3)))))
        first = TutorSpeech(config,router,'original')
        second = TutorSpeech(config,router,'male')
        try:
            assert first.signature == original_signature and first.signature != second.signature
            assert first.settings.voice == second.settings.voice == config.voice
            assert first.settings.available_passage_voices == second.settings.available_passage_voices
            await second.speak('Male tutor')
            await first.speak('An already prepared original cue stays original')
            assert (await second.provider.speech(None)).startswith(b'RIFF')
            assert router.settings.elevenlabs_voice_id == VOICE_IDS['original']
        finally:
            await router.close()
    asyncio.run(run())
    assert f"/{VOICE_IDS['male']}/stream" in calls[0]
    assert f"/{VOICE_IDS['original']}/stream" in calls[1]


def test_owned_all_tutor_paths_and_voice_cache(tmp_path, repository_factory, mp3, audio_bytes):
    calls = []
    config = settings_for(tmp_path).model_copy(update={'provider':'openai','tutor_tts_provider':'elevenlabs','elevenlabs_api_key':Settings(_env_file=None,elevenlabs_api_key='fixture').elevenlabs_api_key})
    base = FakeProvider()
    router = TutorTTSProvider(base,config,httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r:(calls.append(r) or httpx.Response(200,content=mp3)))))
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app = create_app(config,router,repository_factory(None),auth)
    a,b = AccountClient(app,router,'valid-a'),AccountClient(app,router,'valid-b')
    def assert_last(voice): assert f'/{VOICE_IDS[voice]}/stream' in str(calls[-1].url)
    def audio(path, body=None):
        response=a.post(path,**({'json':body} if body is not None else {}))
        assert response.status_code==200,response.text
        assert_last('male')
        return response
    try:
        assert a.put('/api/profile/tutor-voice',json={'voice':'male'}).status_code==200
        state=a.post('/api/onboarding/start',json={'revision':0}).json()
        audio('/api/onboarding/audio',{'revision':state['revision']})
        audio('/api/recognition/retry-audio')
        audio('/api/recognition/acknowledgment-audio')
        count=len(calls); audio('/api/recognition/acknowledgment-audio'); assert len(calls)==count
        a.put('/api/profile/tutor-voice',json={'voice':'original'})
        assert a.post('/api/recognition/acknowledgment-audio').status_code==200;assert_last('original')
        a.put('/api/profile/tutor-voice',json={'voice':'male'})
        assert a.post('/api/recognition/acknowledgment-audio').status_code==200;assert len(calls)==count+1
        assert b.post('/api/recognition/acknowledgment-audio').status_code==200;assert_last('original')
        # Finish onboarding in persisted fixture state to exercise lesson and coach paths.
        repo=app.state.repository.for_learner(USER_A)
        profile=repo.profile();profile.onboarding_status='complete';repo.save_profile(profile)
        lesson=a.post('/api/lessons/start').json()
        audio(f"/api/lessons/{lesson['id']}/audio",{'revision':lesson['revision']})
        from test_conversation import begin, answer, scripted
        scripted(a,[{'inference':'insufficient_evidence'},{}])
        stored=repo.lesson();stored.phase='EXERCISE'
        stored=repo.save_lesson(stored,stored.revision)
        generated=a.post(f"/api/lessons/{stored.id}/exercise",json={'revision':stored.revision})
        assert generated.status_code==200,generated.text
        lesson=generated.json();exercise=lesson['exercise']
        audio(f"/api/lessons/{stored.id}/introduction-audio",{'revision':lesson['revision']})
        assert a.post(f"/api/exercises/{exercise['id']}/conversation/listened").status_code==200
        assert exercise['passage_voice']['accent']=='default'
        audio(f"/api/exercises/{exercise['id']}/coach-audio",{'cue_id':'summary'})
        _,_,response=answer(a,exercise,audio_bytes)
        question=response.json()['active_followup']
        audio(f"/api/exercises/{exercise['id']}/coach-audio",{'cue_id':question['id']})
        _,_,response=answer(a,exercise,audio_bytes,question)
        assert response.json()['result'] is not None
        audio(f"/api/exercises/{exercise['id']}/coach-audio",{'cue_id':'feedback'})
        # Cache routing may change while an old URL exists; it cannot serve the other voice.
        a.put('/api/profile/tutor-voice',json={'voice':'original'})
        assert a.get(f"/api/exercises/{exercise['id']}/coach-audio/feedback").status_code==404
        assert b.get(f"/api/exercises/{exercise['id']}/coach-audio/feedback").status_code==404
    finally:
        app.state.repository.close();asyncio.run(auth.aclose());asyncio.run(router.close())


def test_male_unavailable_retains_existing_coral_fallback(mp3):
    attempts=[];fallback=[]
    config=Settings(_env_file=None,tutor_tts_provider='elevenlabs',elevenlabs_api_key='fixture')
    async def run():
        base=FakeProvider()
        async def speak(text,rate=.9): fallback.append(text);return mp3
        base.speak=speak
        router=TutorTTSProvider(base,config,httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r:(attempts.append(str(r.url)) or httpx.Response(404)))))
        try: assert (await TutorSpeech(config,router,'male').speak('Unavailable voice'))[:2] == mp3[:2]
        finally: await router.close()
    asyncio.run(run())
    assert len(attempts)==len(fallback)==1
    assert VOICE_IDS['male'] in attempts[0]


def test_preview_download_never_synthesizes_or_leaks_key(mp3):
    calls=[]
    url='https://storage.googleapis.com/eleven-public-prod/previews/sample.mp3'
    def transport(request):
        calls.append(request)
        assert request.method=='GET'
        if request.url.host=='api.elevenlabs.io':
            return httpx.Response(200,json={'preview_url':url})
        assert 'xi-api-key' not in request.headers
        return httpx.Response(200,content=mp3)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            config=Settings(_env_file=None,elevenlabs_api_key='fixture')
            data=await existing_preview(config,VOICE_IDS['male'],client)
            assert data
    asyncio.run(run());assert len(calls)==2


@pytest.mark.parametrize('url',[None,'http://storage.googleapis.com/eleven-public-prod/a.mp3','https://evil.example/a.mp3','https://storage.googleapis.com/other/a.mp3','https://user:pass@storage.googleapis.com/eleven-public-prod/a.mp3'])
def test_preview_rejects_untrusted_or_missing_urls(url):
    calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:(calls.append(r) or httpx.Response(200,json={'preview_url':url})))) as client:
            with pytest.raises(ValueError):
                await existing_preview(Settings(_env_file=None,elevenlabs_api_key='fixture'),VOICE_IDS['male'],client)
    asyncio.run(run());assert len(calls)==1


def test_concurrent_preference_survives_completed_assessment(repository_factory,tmp_path,audio_bytes,monkeypatch):
    from test_conversation import begin, answer, scripted
    repo=repository_factory();provider=FakeProvider()
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,provider='demo'),provider,repo),provider)
    scripted(client,[{}]);exercise=begin(client)
    complete=repo.complete
    def concurrent(*args,**kwargs):
        repo.save_tutor_voice('male')
        return complete(*args,**kwargs)
    monkeypatch.setattr(repo,'complete',concurrent)
    _,_,response=answer(client,exercise,audio_bytes)
    assert response.status_code==200,response.text
    assert repo.profile().tutor_voice=='male'
    assert repo.profile().completed_attempts==1


def test_preview_cache_and_unavailability_never_synthesize(repository_factory,tmp_path,mp3,monkeypatch):
    calls=[]
    async def preview(settings,voice_id):calls.append(voice_id);return mp3
    monkeypatch.setattr('app.tutor_voice_api.existing_preview',preview)
    config=settings_for(tmp_path).model_copy(update={'tutor_tts_provider':'elevenlabs','elevenlabs_api_key':Settings(_env_file=None,elevenlabs_api_key='fixture').elevenlabs_api_key})
    provider=FakeProvider()
    async def forbidden(*args,**kwargs):raise AssertionError('Preview must never synthesize speech')
    provider.speak=forbidden
    auth=httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app=create_app(config,provider,repository_factory(None),auth)
    a,b=AccountClient(app,provider,'valid-a'),AccountClient(app,provider,'valid-b')
    try:
        assert a.get('/api/profile/tutor-voice/preview/male').content==mp3
        assert a.get('/api/profile/tutor-voice/preview/male').content==mp3
        assert calls==[VOICE_IDS['male']]
        assert b.get('/api/profile/tutor-voice/preview/male').status_code==200
        assert a.get('/api/profile/tutor-voice/preview/original').status_code==200
        assert calls==[VOICE_IDS['male'],VOICE_IDS['male'],VOICE_IDS['original']]
        assert a.get('/api/profile/tutor-voice/preview/arbitrary-id').status_code==422
        assert a.get('/api/profile/tutor-voice').json()['voice']=='original'
        async def unavailable(*args):raise httpx.ConnectError('private provider payload')
        monkeypatch.setattr('app.tutor_voice_api.existing_preview',unavailable)
        response=b.get('/api/profile/tutor-voice/preview/original')
        assert response.status_code==503 and 'private provider payload' not in response.text
        config.tutor_tts_provider='openai'
        assert a.get('/api/profile/tutor-voice/preview/male').status_code==503
    finally:
        app.state.repository.close();asyncio.run(auth.aclose())


@pytest.mark.parametrize('failure',['too_large','invalid_audio','unavailable','redirect'])
def test_preview_rejects_bad_provider_audio_without_synthesis(failure):
    calls=[]
    def transport(request):
        calls.append(request)
        assert request.method=='GET'
        if request.url.host=='api.elevenlabs.io':
            return httpx.Response(200,json={'preview_url':'https://storage.googleapis.com/eleven-public-prod/previews/sample.mp3'})
        if failure=='unavailable':return httpx.Response(404)
        if failure=='redirect':return httpx.Response(302,headers={'location':'https://evil.example/a.mp3'})
        return httpx.Response(200,content=b'x'*(2*1024*1024+1) if failure=='too_large' else b'not an mp3')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            with pytest.raises((ValueError,httpx.HTTPStatusError)):
                await existing_preview(Settings(_env_file=None,elevenlabs_api_key='fixture'),VOICE_IDS['male'],client)
    asyncio.run(run());assert len(calls)==2


def test_recorded_preview_is_capped_at_twelve_seconds(audio_bytes):
    import io
    import wave
    from imageio_ffmpeg import get_ffmpeg_exe
    from app.audio import sanitize_generated_audio
    with wave.open(io.BytesIO(audio_bytes),'rb') as source:
        params=source.getparams();frames=source.readframes(source.getnframes())
    output=io.BytesIO()
    with wave.open(output,'wb') as target:
        target.setparams(params);target.writeframes(frames*5)
    async def run():
        recorded=await sanitize_generated_audio(output.getvalue(),'mp3')
        def transport(request):
            if request.url.host=='api.elevenlabs.io':return httpx.Response(200,json={'preview_url':'https://storage.googleapis.com/eleven-public-prod/previews/recorded.mp3'})
            return httpx.Response(200,content=recorded)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            short=await existing_preview(Settings(_env_file=None,elevenlabs_api_key='fixture'),VOICE_IDS['male'],client)
        decoder=await asyncio.create_subprocess_exec(get_ffmpeg_exe(),'-v','error','-i','pipe:0','-ac','1','-ar','8000','-f','s16le','pipe:1',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        pcm,_=await decoder.communicate(short)
        assert decoder.returncode==0 and 11.9<=len(pcm)/16000<=12.1
    asyncio.run(run())


def test_tutor_voice_survives_explicit_onboarding_restart(accounts):
    a,_,_=accounts
    assert a.put('/api/profile/tutor-voice',json={'voice':'male'}).status_code==200
    response=a.post('/api/onboarding/restart',json={'revision':0,'confirmation':'RESTART ONBOARDING'})
    assert response.status_code==200,response.text
    assert a.get('/api/profile/tutor-voice').json()['voice']=='male'
