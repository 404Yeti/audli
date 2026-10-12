import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select, update
from app.storage import schema as storage_schema
from app.accent_practice import offer, intent, resolve, passage_audio, VerifiedPassageVoice, discover, discovery_response
from app.models import AccentPreferences, LearnerProfile, ExerciseContent
from app.lesson import Lesson, now, prompt
from app.config import Settings
from test_persistence import repository_factory
from test_lesson_lifecycle import lesson_client, start, respond, action, exercises

VOICE = VerifiedPassageVoice(voice_id='a'*20, validation='Reviewed listening validation: test fixture only')
CANDIDATE_IDS = {'british':'onwK4e9ZLuTAKqWW03F9', 'australian':'tyepWYJJwJM9TTFIg5U7', 'irish':'DbwWo4rVEd5NrejHYUnm'}

def lesson():
    return Lesson(id='test', started_at=now(), active_since=now(), name='Maya', focus='details')


def test_discovery_is_bounded_separate_and_backward_compatible():
    prefs = AccentPreferences()
    history = []
    asked = []
    for index in range(12):
        selected = discover(prefs, history, {'british': VOICE})
        if selected:
            asked.append(index)
        item = lesson(); item.accent_discovery = selected; item.accent_discovery_resolved = selected
        history.insert(0, item)
    assert asked == [0, 3]
    assert not discover(prefs, [], {})
    assert not discover(AccentPreferences(status='no_preference'), [], {'british': VOICE})
    assert not discover(AccentPreferences(status='preferred', accents=['british']), [], {'british': VOICE})
    assert not Lesson.model_validate({k:v for k,v in lesson().model_dump().items() if not k.startswith('accent_discovery')}).accent_discovery
    assert discovery_response('I visited Australia yesterday')[0] is None


@pytest.mark.parametrize('text,status,accents', [
    ('British and Irish accents please', 'preferred', ['british','irish']),
    ('No thanks', 'no_preference', []),
    ('Not today', 'no_preference', []),
])
def test_existing_learner_discovery_persists_without_selecting(repository_factory,tmp_path,audio_bytes,text,status,accents):
    from conftest import ASGIClient, FakeProvider
    from app.main import create_app
    from app.models import Transcription
    repo=repository_factory(); profile=repo.profile(); profile.onboarding_status='complete'
    repo.save_profile(profile)
    # Real legacy JSON has no accent_preferences field, rather than an explicit
    # unspecified value written by the newer model.
    with repo.transaction() as db:
        data=db.execute(select(storage_schema.profiles.c.data).where(
            storage_schema.profiles.c.user_id==repo.learner_id)).scalar_one()
        data.pop('accent_preferences',None)
        db.execute(update(storage_schema.profiles).where(
            storage_schema.profiles.c.user_id==repo.learner_id).values(data=data))
    before=repo.profile().model_dump()
    provider=FakeProvider()
    async def transcript(*args): return Transcription(text=text,confidence=.99)
    provider.transcribe=transcript
    settings=Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE,'irish':VOICE})
    client=ASGIClient(create_app(settings,provider,repo),provider)
    item=start(client)
    assert 'remember for future' in item['prompt'] and 'Want to practice' not in item['prompt']
    assert repo.lesson().accent_discovery and repo.lesson().accent_offered is None
    assert start(client)['id']==item['id']
    item=respond(client,item,audio_bytes)
    assert ('remember that accent preference' in item['prompt'] if status == 'preferred'
            else 'usual listening practice' in item['prompt'])
    saved=repo.profile().accent_preferences
    assert saved.status==status and saved.accents==accents and saved.response==text
    assert repo.lesson().passage_accent is None and repo.lesson().accent_discovery_resolved
    after=repo.profile().model_dump(); after['accent_preferences']=before['accent_preferences']
    assert after==before
    reopened=repository_factory()
    assert reopened.profile().accent_preferences==saved
    other=repository_factory('00000000-0000-0000-0000-000000000099')
    assert other.profile().accent_preferences.status=='unspecified'
    assert other.lesson(item['id']) is None


def test_global_refusal_persists_but_daily_refusal_does_not(repository_factory,tmp_path,audio_bytes):
    from conftest import ASGIClient, FakeProvider
    from app.main import create_app
    from app.models import Transcription
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['british']);repo.save_profile(profile)
    provider=FakeProvider()
    async def transcript(*args): return Transcription(text='Stop suggesting accents',confidence=.99)
    provider.transcribe=transcript
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE}),provider,repo),provider)
    item=respond(client,start(client),audio_bytes)
    assert 'won’t suggest' in item['prompt']
    assert repo.profile().accent_preferences.status=='no_preference'
    assert repo.lesson().passage_accent is None
    assert offer(repo.profile().accent_preferences,[],{'british':VOICE}) is None


def test_discovery_uncertainty_and_stale_checkpoint_cannot_save_preference(repository_factory,tmp_path,audio_bytes):
    from conftest import ASGIClient, FakeProvider
    from app.main import create_app
    from app.models import Transcription
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete';repo.save_profile(profile)
    provider=FakeProvider()
    async def uncertain(*args): return Transcription(text='British accents',confidence=.1)
    provider.transcribe=uncertain
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'british':VOICE}),provider,repo),provider)
    item=start(client)
    pending=client.post(f"/api/lessons/{item['id']}/attempts",data={'revision':item['revision']},files={'audio':('a.wav',audio_bytes,'audio/wav')}).json()
    assert client.post(f"/api/lessons/{item['id']}/answer",json={'revision':pending['revision']}).status_code==422
    assert repo.profile().accent_preferences.status=='unspecified'
    assert not repo.lesson().accent_discovery_resolved
    stale=repo.lesson(); current=repo.lesson();current.status='paused';current.active_since=None
    repo.save_lesson(current,current.revision)
    with pytest.raises(ValueError):
        repo.save_lesson(stale,stale.revision,accent_preferences=AccentPreferences(status='preferred',accents=['british']))
    assert repo.profile().accent_preferences.status=='unspecified'

def test_policy_preferences_cadence_and_rotation():
    prefs = AccentPreferences(status='preferred', accents=['australian', 'irish'])
    history = []
    offers = []
    for index in range(10):
        selected = offer(prefs, history, {'australian':VOICE, 'irish':VOICE})
        if selected: offers.append((index, selected))
        item = lesson(); item.accent_offered = selected; item.status = 'paused'
        history.insert(0, item)
    assert offers == [(0,'australian'),(3,'irish'),(6,'australian'),(9,'irish')]
    assert offer(prefs, [], {}) is None
    assert offer(AccentPreferences(status='no_preference', accents=['irish']), [], {'irish':VOICE}) is None

@pytest.mark.parametrize('text,offered,result', [
    ("Sure, let's do Australian", 'australian', ('select','australian')),
    ('No thanks', 'australian', ('decline',None)),
    ('Not today', 'australian', ('decline',None)),
    ('British instead please', 'australian', ('select','british')),
    ('Can we practice an Irish accent?',None,('select','irish')),
    ('I visited Australia yesterday',None,(None,None)),
    ('I visited Australia yesterday','australian',(None,None)),
    ('Maybe not', 'australian',(None,None)),
    ('Can we practice a Jamaican accent?',None,('unavailable',None)),
])
def test_spoken_intents(text, offered, result):
    assert intent(text, offered) == result

def test_decline_unknown_and_checkpoint():
    item = lesson(); item.accent_offered = 'australian'
    assert 'usual' in resolve(item,'No thanks',{'australian':VOICE})
    restored = Lesson.model_validate_json(item.model_dump_json())
    assert 'usual passage voice' in resolve(restored,'Sure',{'australian':VOICE})
    assert restored.passage_accent is None and restored.accent_resolved
    assert 'Perfect' in resolve(restored,'Practice Australian please',{'australian':VOICE})
    assert restored.passage_accent == 'australian'
    assert 'isn’t available' in resolve(restored,'British instead',{'australian':VOICE})
    assert restored.passage_accent is None

def test_config_requires_validation_and_credentials():
    with pytest.raises(ValueError): VerifiedPassageVoice(voice_id='a'*20, validation='')
    with pytest.raises(ValueError): Settings(_env_file=None,passage_voices={'australian':VOICE})

@pytest.mark.parametrize('failure',[False,True])
def test_passage_routing_and_fallback(monkeypatch, failure, repository_factory):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            assert url.endswith('/'+'a'*20)
            assert kwargs['json']['voice_settings']['speed'] == .75
            if failure: raise httpx.ConnectError('unavailable')
            return httpx.Response(200,content=b'accent',request=httpx.Request('POST',url))
    monkeypatch.setattr('app.accent_practice.httpx.AsyncClient',lambda **kwargs:Client())
    async def sanitize(data,kind): return data
    monkeypatch.setattr('app.audio.sanitize_generated_audio',sanitize)
    calls=[]
    class Provider:
        async def speech(self,content): calls.append('passage'); return b'default'
        async def speak(self,*args): raise AssertionError('Tutor routing must not change')
    settings=Settings(_env_file=None,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'australian':VOICE})
    from accent_control_helpers import scope
    from app.storage.accent_controls import SharedAccentControls
    repo=repository_factory();lesson=scope(repo,settings,'australian')
    controls=SharedAccentControls(repo.engine,settings)
    audio, used=asyncio.run(passage_audio(settings,Provider(),SimpleNamespace(script='story',speech_rate=.75),'australian',controls,owner=repo.learner_id,lesson=lesson))
    controls.close()
    assert used.accent == ('default' if failure else 'australian')
    assert used.fallback == failure
    assert audio == (b'default' if failure else b'accent')
    assert calls == (['passage'] if failure else [])

def test_existing_saved_preferences_and_exercise_metadata(lesson_client,audio_bytes):
    client=lesson_client; repo=client.app.state.repository
    profile=repo.profile(); profile.accent_preferences=AccentPreferences(status='preferred',accents=['australian'])
    repo.save_profile(profile)
    before=repo.profile().model_dump()
    item=start(client)
    assert 'Want to practice' not in item['prompt'] # no verified configured voices
    item=respond(client,item,audio_bytes)
    item=action(client,item,'heard'); item=action(client,item,'heard'); item=action(client,item,'exercise')
    used=item['exercise']['passage_voice']
    assert used['accent']=='default'
    assert client.get('/api/lessons/current').json()['exercise']['passage_voice']==used
    assert action(client,item,'exercise')['exercise']['passage_voice']==used
    assert client.get(f"/api/exercises/{item['exercise']['id']}/transcript").status_code==403
    assert repo.profile().model_dump()==before

def test_spoken_selection_interruption_uncertainty_and_fallback(repository_factory,tmp_path,audio_bytes):
    from conftest import ASGIClient, FakeProvider
    from app.main import create_app
    from app.models import Transcription
    repo=repository_factory()
    profile=repo.profile(); profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['australian'])
    repo.save_profile(profile)
    provider=FakeProvider()
    async def speak(*args): return audio_bytes
    provider.speak=speak
    settings=Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'australian':VOICE})
    client=ASGIClient(create_app(settings,provider,repo),provider)
    item=start(client)
    assert 'Australian' in item['prompt']
    stored=repo.lesson(); stored.status='paused'; stored.active_since=None
    repo.save_lesson(stored,stored.revision)
    assert start(client)['id']==item['id']
    item=action(client,client.get('/api/lessons/current').json(),'resume')
    assert item['prompt']==prompt(repo.lesson())
    async def uncertain(*args): return Transcription(text='Sure',confidence=.1)
    provider.transcribe=uncertain
    pending=client.post(f"/api/lessons/{item['id']}/attempts",data={'revision':item['revision']},files={'audio':('a.wav',audio_bytes,'audio/wav')}).json()
    assert client.post(f"/api/lessons/{item['id']}/answer",json={'revision':pending['revision']}).status_code==422
    assert repo.lesson().passage_accent is None and not repo.lesson().accent_resolved
    async def accept(*args): return Transcription(text='Sure, Australian please',confidence=.99)
    provider.transcribe=accept
    item=respond(client,pending,audio_bytes)
    assert repo.lesson().passage_accent=='australian'
    item=action(client,item,'heard')
    assert item['phase']=='TRANSITION'  # Clear welcome acceptance needs no second consent.
    item=action(client,item,'heard')
    # Missing route after selection must safely fall back without network access.
    settings.passage_voices={}
    item=action(client,item,'exercise')
    assert item['exercise']['passage_voice']['fallback']
    assert 'usual passage voice' in item['introduction_prompt']
    assert client.get('/api/lessons/current').json()['exercise']==item['exercise']

def test_declining_offer_does_not_change_topics_or_scores(repository_factory,tmp_path,audio_bytes):
    from conftest import ASGIClient, FakeProvider
    from app.main import create_app
    from app.models import Transcription
    repo=repository_factory(); profile=repo.profile(); profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['australian'])
    repo.save_profile(profile); before=repo.profile().model_dump()
    provider=FakeProvider()
    async def decline(*args): return Transcription(text='No thanks, not today',confidence=.99)
    provider.transcribe=decline
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={'australian':VOICE}),provider,repo),provider)
    item=respond(client,start(client),audio_bytes)
    assert 'usual passage voice' in item['prompt']
    assert repo.lesson().accent_resolved and repo.lesson().passage_accent is None
    item=action(client,item,'heard')
    assert item['phase']=='TRANSITION' and 'accent' not in item['prompt']
    assert repo.profile().model_dump()==before

@pytest.mark.parametrize('accent,failed,interrupted',[
    ('australian',False,False),('british',False,False),('irish',False,False),
    ('australian',True,False),('irish',False,True)])
def test_selected_passage_persists_audio_replay_retry_and_cancellation(repository_factory,tmp_path,audio_bytes,monkeypatch,accent,failed,interrupted):
    from conftest import ASGIClient, FakeProvider
    from app.main import create_app
    from app.models import Transcription
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=[accent]);repo.save_profile(profile)
    before=repo.profile().model_dump()
    provider=FakeProvider()
    async def transcript(*args): return Transcription(text=f'Let’s practice {accent}',confidence=.99)
    provider.transcribe=transcript
    calls=[]
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            calls.append(url)
            if interrupted:
                checkpoint=repo.lesson();checkpoint.status='paused';checkpoint.active_since=None
                repo.save_lesson(checkpoint,checkpoint.revision)
            return httpx.Response(503 if failed else 200,content=audio_bytes,request=httpx.Request('POST',url))
    real_client = httpx.AsyncClient
    monkeypatch.setattr('app.accent_practice.httpx.AsyncClient',
        lambda **kwargs: real_client(**kwargs) if 'transport' in kwargs else Client())
    candidate=VerifiedPassageVoice(voice_id=CANDIDATE_IDS[accent],validation='Routing fixture only: not human voice approval')
    settings=Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices={accent:candidate})
    client=ASGIClient(create_app(settings,provider,repo),provider)
    item=respond(client,start(client),audio_bytes)
    item=action(client,item,'heard')
    assert item['phase']=='TRANSITION'
    item=action(client,item,'heard')
    response=client.post(f"/api/lessons/{item['id']}/exercise",json={'revision':item['revision']})
    if interrupted:
        assert response.status_code==409 and repo.current_exercise() is None
        assert repo.lesson().status=='paused' and repo.lesson().passage_accent==accent
    else:
        assert response.status_code==200
        assert repo.lesson().accent_consent.state=='consumed'
        from app.accent_practice import authorized_accent
        assert authorized_accent(repo.lesson()) is None
        item=response.json();exercise=item['exercise'];used=exercise['passage_voice']
        assert used['accent']==('default' if failed else accent)
        assert used['fallback']==failed
        if not failed: assert used['voice_id']==CANDIDATE_IDS[accent]
        original=client.get(exercise['audio_url']).content
        settings.passage_voices={}
        assert action(client,item,'exercise')['exercise']==exercise
        assert client.get(exercise['audio_url']).content==original
        assert client.get(exercise['audio_url']).content==original
        assert client.get('/api/lessons/current').json()['exercise']==exercise
        assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code==403
        assert json.loads(repo.exercise(exercise['id'])['content'])['passage_voice']==used
    assert calls==['https://api.elevenlabs.io/v1/text-to-speech/'+CANDIDATE_IDS[accent]]
    assert settings.elevenlabs_voice_id=='yKYzqEa22xh5PdidhN70'
    assert repo.profile().model_dump()==before


@pytest.mark.parametrize('text,expected', [
    ('British instead please','british'),
    ('Can we practice an Irish accent?','irish'),
    ('Can we practice a Jamaican accent?',None),
])
def test_spoken_alternate_and_learner_initiation_do_not_rewrite_preferences(repository_factory,tmp_path,audio_bytes,text,expected):
    from conftest import ASGIClient, FakeProvider
    from app.main import create_app
    from app.models import Transcription
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['australian']);repo.save_profile(profile)
    provider=FakeProvider()
    async def transcript(*args): return Transcription(text=text,confidence=.99)
    provider.transcribe=transcript
    voices={accent:VerifiedPassageVoice(voice_id=value,validation='Routing fixture only: not human voice approval') for accent,value in CANDIDATE_IDS.items()}
    client=ASGIClient(create_app(Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='test',accent_practice_enabled=True,passage_tts_daily_characters=100000,passage_voices=voices),provider,repo),provider)
    item=start(client)
    if 'Can we' in text:
        checkpoint=repo.lesson();checkpoint.accent_offered=None;checkpoint.accent_resolved=True
        repo.save_lesson(checkpoint,checkpoint.revision);item=client.get('/api/lessons/current').json()
    before=repo.profile().model_dump()
    item=respond(client,item,audio_bytes)
    assert repo.lesson().passage_accent==expected
    assert repo.profile().model_dump()==before
    if expected is None: assert 'isn’t available' in item['prompt']


@pytest.fixture(autouse=True)
def activated_test_feature(monkeypatch):
    from accent_control_helpers import enable_test_controls
    enable_test_controls(monkeypatch)
