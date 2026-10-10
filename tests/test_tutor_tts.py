"""Provider boundary and owned API integration without paid provider calls."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.audio import sanitize_generated_audio, signature_matches
from app.config import Settings
from app.main import create_app
from app.services.tutor_tts import TutorTTSProvider, tutor_cache_signature, tutor_cue_cache_id, tts_measurements
from conftest import FakeProvider
from test_auth import AccountClient, auth_response
from test_persistence import repository_factory


def settings(**values):
    return Settings(_env_file=None, provider='openai', tutor_tts_provider='elevenlabs',
        elevenlabs_api_key='fixture-eleven-secret', **values)


@pytest.fixture
def mp3(audio_bytes):
    return asyncio.run(sanitize_generated_audio(audio_bytes, 'mp3'))


def test_configuration_is_opt_in_and_secrets_are_redacted():
    assert Settings(_env_file=None).tutor_tts_provider == 'openai'
    with pytest.raises(ValidationError, match='ELEVENLABS_API_KEY'):
        Settings(_env_file=None, tutor_tts_provider='elevenlabs', elevenlabs_api_key=None)
    with pytest.raises(ValidationError):
        settings(elevenlabs_stability=2)
    assert 'fixture-eleven-secret' not in repr(settings())
    from app.diagnostics import redact
    assert redact('fixture-eleven-secret', settings()) == '[REDACTED]'


def test_cache_signature_tracks_audio_settings_but_not_keys():
    first = settings()
    assert tutor_cache_signature(first) == tutor_cache_signature(first.model_copy(update={'elevenlabs_api_key': None}))
    for key,value in [('tutor_tts_provider','openai'),('elevenlabs_style',.2),
        ('elevenlabs_voice_id','a'*20),('elevenlabs_model','eleven_multilingual_v2'),
        ('speech_model','tts-1'),('elevenlabs_stability',.3),('elevenlabs_similarity_boost',.2),
        ('elevenlabs_speaker_boost',False)]:
        assert tutor_cache_signature(first) != tutor_cache_signature(first.model_copy(update={key:value}))
    assert len(tutor_cue_cache_id(first,'x'*80,'message'))<=80
    assert tutor_cue_cache_id(first,'summary','old')!=tutor_cue_cache_id(first,'summary','new')
    assert tutor_cue_cache_id(first,'summary','text')!=tutor_cue_cache_id(first,'feedback','text')


def test_startup_configuration_logs_only_safe_metadata(caplog):
    from app.services.tutor_tts import emit_configuration
    caplog.set_level('INFO', logger='uvicorn.error')
    config = settings(openai_api_key='fixture-openai-secret')
    emit_configuration(config)
    assert 'fixture-openai-secret' not in caplog.text
    assert 'fixture-eleven-secret' not in caplog.text
    assert '"elevenlabs_key_configured": true' in caplog.text
    assert '"coral_fallback_selected": true' in caplog.text
    caplog.clear()
    emit_configuration(Settings(_env_file=None, provider='openai'))
    assert '"elevenlabs_key_configured": false' in caplog.text
    assert '"coral_selected": true' in caplog.text


def test_application_startup_emits_configuration_without_synthesis(tmp_path, caplog):
    caplog.set_level('INFO', logger='uvicorn.error')
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), FakeProvider())
    async def run():
        async with app.router.lifespan_context(app):
            assert '"event": "configuration"' in caplog.text
    asyncio.run(run())
    assert '"outcome"' not in caplog.text


def test_selected_voice_mp3_and_passages_stay_delegated(mp3, caplog):
    calls, fallback, metrics = [], [], []
    def transport(request):
        calls.append(request)
        return httpx.Response(200,content=mp3,headers={'character-cost':'5'})
    async def run():
        base=FakeProvider()
        async def speak(text,rate=.9): fallback.append(text); return mp3
        base.speak=speak
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        router=TutorTTSProvider(base,settings(),client)
        token=tts_measurements.set(metrics)
        try:
            with caplog.at_level('INFO',logger='uvicorn.error'):
                audio=await router.speak('private tutor text')
            assert signature_matches(audio,'mp3')
            assert (await router.speech(None)).startswith(b'RIFF')
        finally:
            tts_measurements.reset(token);await router.close()
    asyncio.run(run())
    assert len(calls)==1 and not fallback
    assert '/yKYzqEa22xh5PdidhN70/stream' in str(calls[0].url)
    assert metrics[0]['reported_cost_units']==5
    assert metrics[0]['first_audio_byte_ms']>=0
    assert 'Tutor TTS' in caplog.text
    assert 'private tutor text' not in caplog.text and 'fixture-eleven-secret' not in caplog.text


@pytest.mark.parametrize('failure',['http','invalid','undecodable','timeout','network'])
def test_one_attempt_then_one_fallback_and_safe_errors(mp3,failure,caplog):
    attempts,fallback,metrics=[],[],[]
    async def transport(request):
        attempts.append(request)
        if failure=='timeout': await asyncio.sleep(10)
        if failure=='network': raise httpx.ConnectError('fixture-eleven-secret private transcript')
        if failure=='http': return httpx.Response(429,json={'detail':'private transcript fixture-eleven-secret'})
        return httpx.Response(200,content=b'ID3broken' if failure=='undecodable' else b'not audio')
    async def run():
        base=FakeProvider()
        async def speak(text,rate=.9): fallback.append(text);return mp3
        base.speak=speak
        router=TutorTTSProvider(base,settings(tutor_tts_timeout_seconds=.05),httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        token=tts_measurements.set(metrics)
        try:
            with caplog.at_level('INFO',logger='uvicorn.error'):
                assert signature_matches(await router.speak('private transcript'),'mp3')
        finally: tts_measurements.reset(token);await router.close()
    asyncio.run(run())
    assert len(attempts)==len(fallback)==1
    assert any(m.get('event')=='fallback' for m in metrics)
    assert 'fixture-eleven-secret' not in caplog.text and 'private transcript' not in caplog.text


def test_cancellation_closes_stream_without_fallback(mp3):
    started=asyncio.Event()
    closed=[];fallback=[]
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.sleep(10)
            yield mp3
        async def aclose(self):closed.append(True)
    async def run():
        base=FakeProvider()
        async def speak(text,rate=.9):fallback.append(text);return mp3
        base.speak=speak
        router=TutorTTSProvider(base,settings(),httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Stream()))))
        task=asyncio.create_task(router.speak('cancel this cue'))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await router.close()
    asyncio.run(run())
    assert closed and not fallback


@pytest.mark.parametrize('slow_fallback', [False, True])
def test_both_failures_are_bounded_and_redacted(slow_fallback):
    async def run():
        base=FakeProvider()
        async def speak(text,rate=.9):
            if slow_fallback:await asyncio.sleep(10)
            raise ValueError('private transcript secret')
        base.speak=speak
        router=TutorTTSProvider(base,settings(tutor_tts_fallback_timeout_seconds=.05),httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(401))))
        try:
            with pytest.raises(ValueError,match='temporarily unavailable') as exc:
                await router.speak('test')
            assert exc.value.__suppress_context__
        finally:await router.close()
    asyncio.run(run())


@pytest.mark.parametrize('selected', ['openai', 'elevenlabs'])
def test_real_openai_tutor_uses_coral_and_disables_sdk_retries(mp3, selected):
    from app.services.openai_provider import OpenAIProvider
    calls=[]
    def transport(request):
        import json
        calls.append(json.loads(request.content))
        return httpx.Response(200,content=mp3,headers={'content-type':'audio/mpeg'})
    async def run():
        from openai import AsyncOpenAI
        config=settings(openai_api_key='fixture-openai',voice='alloy' if selected=='elevenlabs' else 'coral')
        config.tutor_tts_provider=selected
        base=OpenAIProvider(config)
        await base.client.close()
        base.client=AsyncOpenAI(api_key='fixture-openai',http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),max_retries=2)
        original=base.client.with_options
        def options(**kw):
            assert kw['max_retries']==0
            return original(**kw)
        base.client.with_options=options
        router=TutorTTSProvider(base,config,httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(503))))
        try:assert signature_matches(await router.speak('Coral fallback'),'mp3')
        finally:await router.close()
    asyncio.run(run())
    assert len(calls)==1 and calls[0]['voice']=='coral'


def test_owned_api_cache_config_invalidation_and_transcript_gate(tmp_path,repository_factory,mp3):
    from test_auth import settings_for
    calls=[]
    config=settings_for(tmp_path).model_copy(update={'provider':'openai','tutor_tts_provider':'elevenlabs',
        'elevenlabs_api_key':settings().elevenlabs_api_key})
    router=TutorTTSProvider(FakeProvider(),config,httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r:(calls.append(r) or httpx.Response(200,content=mp3)))))
    auth=httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    app=create_app(config,router,repository=repository_factory(None),auth_client=auth)
    a,b=AccountClient(app,router,'valid-a'),AccountClient(app,router,'valid-b')
    try:
        assert AccountClient(app,router,'bad').post('/api/recognition/acknowledgment-audio').status_code==401
        assert not calls
        first=a.post('/api/recognition/acknowledgment-audio')
        assert first.status_code==200 and first.headers['content-type']=='audio/mpeg'
        assert a.post('/api/recognition/acknowledgment-audio').content==first.content
        assert len(calls)==1
        assert b.post('/api/recognition/acknowledgment-audio').status_code==200
        assert len(calls)==2
        async def concurrent():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                responses=await asyncio.gather(*[client.post('/api/recognition/acknowledgment-audio',
                    headers={'Authorization':'Bearer valid-a'},json={'cue':'followup'}) for _ in range(2)])
                assert all(r.status_code==200 for r in responses)
                assert responses[0].content==responses[1].content
        asyncio.run(concurrent())
        assert len(calls)==3
        config.elevenlabs_stability=.3
        assert a.post('/api/recognition/acknowledgment-audio').status_code==200
        assert len(calls)==4
        exercise=a.post('/api/exercises').json()['id']
        assert b.get(f'/api/exercises/{exercise}/audio').status_code==404
        assert a.get(f'/api/exercises/{exercise}').json().get('script') is None
        assert a.get(f'/api/exercises/{exercise}/coach-audio/feedback').status_code==403
    finally:
        app.state.repository.close();asyncio.run(auth.aclose());asyncio.run(router.close())
