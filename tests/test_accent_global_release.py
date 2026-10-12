"""Global switch, formal evidence, provider limits, and authenticated isolation."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from app.config import Settings
from app.accent_practice import passage_audio, authorized_accent
from app.models import AccentPreferences, Transcription
from app.main import create_app
from test_accent_practice import VOICE
from test_persistence import repository_factory
from test_lesson_lifecycle import start, action
from test_passage_voice_validation import reviewed
from test_auth import USER_A, USER_B, auth_response
from conftest import ASGIClient, FakeProvider


def configured(**overrides):
    return Settings(_env_file=None,elevenlabs_api_key='fixture',passage_voices={'british':VOICE},
        **({'accent_practice_enabled':True,'passage_tts_daily_characters':1000}|overrides))


def test_flag_defaults_disabled_and_has_no_account_allowlist():
    settings=Settings(_env_file=None)
    assert not settings.accent_practice_enabled and settings.available_passage_voices=={}
    assert not any('allowlist' in name or 'eligible_account' in name for name in Settings.model_fields)
    with pytest.raises(ValueError,match='budget'):
        configured(passage_tts_daily_characters=0)


def test_disabled_feature_keeps_ordinary_lesson_and_preferences(repository_factory,tmp_path):
    repo=repository_factory();profile=repo.profile();profile.onboarding_status='complete'
    profile.accent_preferences=AccentPreferences(status='preferred',accents=['british']);repo.save_profile(profile)
    provider=FakeProvider();settings=configured(accent_practice_enabled=False,data_dir=tmp_path)
    client=ASGIClient(create_app(settings,provider,repo),provider)
    item=start(client);assert 'practice' not in item['prompt'] and not repo.lesson().accent_discovery
    # Even an old accepted grant cannot synthesize an accent after the switch is off.
    from app.accent_practice import resolve
    stored=repo.lesson();resolve(stored,'Practice British',settings.passage_voices);stored.phase='EXERCISE'
    repo.save_lesson(stored,stored.revision)
    item=action(client,client.get('/api/lessons/current').json(),'exercise')
    assert item['exercise']['passage_voice']['accent']=='default'
    assert repo.profile().accent_preferences==profile.accent_preferences


def test_production_enablement_rechecks_actual_human_csv_and_hashes(tmp_path):
    values,save=reviewed(tmp_path,False)
    kwargs=dict(environment='production',persistence='postgres',auth_mode='supabase',
        database_url='postgresql://fixture:fixture@localhost/audli?sslmode=require',
        supabase_url='https://project.supabase.co',supabase_publishable_key='sb_publishable_test',
        passage_review_dir=tmp_path,passage_voices={'australian':{'voice_id':'1'*20,'validation':'Human review fixture only'}})
    with pytest.raises(ValueError,match='human approval'):
        Settings(_env_file=None,elevenlabs_api_key='fixture',accent_practice_enabled=True,passage_tts_daily_characters=1000,**kwargs)
    for row in values:row['rater']='Synthetic test reviewer'
    save()
    settings=Settings(_env_file=None,elevenlabs_api_key='fixture',accent_practice_enabled=True,passage_tts_daily_characters=1000,**kwargs)
    assert settings.available_passage_voices['australian'].voice_id=='1'*20
    (tmp_path/'P01.mp3').write_bytes(b'changed')
    with pytest.raises(ValueError,match='human approval'):
        Settings(_env_file=None,elevenlabs_api_key='fixture',accent_practice_enabled=True,passage_tts_daily_characters=1000,**kwargs)
    # Disabled startup does not need approved files or live provider access.
    kwargs['passage_review_dir']=tmp_path/'absent'
    assert not Settings(_env_file=None,elevenlabs_api_key='fixture',**kwargs).accent_practice_enabled


@pytest.mark.parametrize('failure',[402,429,503,'timeout','flag','budget','rate','characters'])
def test_provider_failures_and_limits_preserve_default_voice(monkeypatch,failure,repository_factory):
    settings=configured(passage_tts_timeout_seconds=.01)
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append(kwargs)
            if failure=='timeout':await asyncio.sleep(1)
            return httpx.Response(failure if isinstance(failure,int) else 503,request=httpx.Request('POST',url))
    monkeypatch.setattr('app.accent_practice.httpx.AsyncClient',Client)
    class Provider:
        async def speech(self,content):return b'ordinary'
    if failure=='flag':settings.accent_practice_enabled=False
    if failure=='budget':settings.passage_tts_daily_characters=1
    if failure=='characters':settings.passage_tts_max_characters=1
    from accent_control_helpers import scope
    from app.storage.accent_controls import SharedAccentControls
    repo=repository_factory();lesson=scope(repo,settings)
    limits=SharedAccentControls(repo.engine,settings)
    content=SimpleNamespace(script='original fixture story',speech_rate=.65 if failure=='rate' else .75)
    audio,voice=asyncio.run(passage_audio(settings,Provider(),content,'british',limits,owner=repo.learner_id,lesson=lesson))
    assert audio==b'ordinary' and voice.accent=='default' and voice.fallback
    limits.close()
    assert bool(calls)==(failure not in ('flag','budget','rate','characters'))


def test_concurrent_authenticated_lessons_keep_consent_and_preference_isolated(repository_factory,tmp_path):
    database=repository_factory(None)
    for owner,prefs in [(USER_A,AccentPreferences(status='preferred',accents=['british'])),
                        (USER_B,AccentPreferences())]:
        repo=database.for_learner(owner);profile=repo.profile();profile.onboarding_status='complete'
        profile.accent_preferences=prefs;repo.save_profile(profile)
    provider=FakeProvider()
    async def run():
        auth_client=httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
        settings=configured(data_dir=tmp_path,auth_mode='supabase',supabase_url='https://project.supabase.co',supabase_publishable_key='sb_publishable_test')
        app=create_app(settings,provider,database,auth_client)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            headers=[{'Authorization':'Bearer valid-a'},{'Authorization':'Bearer valid-b'}]
            responses=await asyncio.gather(*[client.post('/api/lessons/start',headers=h) for h in headers])
            assert all(r.status_code==200 for r in responses)
            lessons=[r.json() for r in responses]
            assert 'Want to practice' in lessons[0]['prompt'] and 'remember' in lessons[1]['prompt']
            revisions=[]
            for owner,text in [(USER_A,'Yes'),(USER_B,'No thanks')]:
                repo=database.for_learner(owner);lesson=repo.lesson();lesson.pending=Transcription(text=text,confidence=.99)
                revisions.append(repo.save_lesson(lesson,lesson.revision).revision)
            answers=await asyncio.gather(*[client.post(f"/api/lessons/{lesson['id']}/answer",headers=h,json={'revision':rev})
                for lesson,h,rev in zip(lessons,headers,revisions)])
            assert all(r.status_code==200 for r in answers)
            a,b=database.for_learner(USER_A),database.for_learner(USER_B)
            assert authorized_accent(a.lesson())=='british' and authorized_accent(b.lesson()) is None
            assert b.profile().accent_preferences.status=='no_preference'
            assert a.profile().accent_preferences.status=='preferred'
            assert (await client.get(f"/api/lessons/{lessons[0]['id']}",headers=headers[1])).status_code==404
            assert (await client.post('/api/lessons/start')).status_code==401
        await auth_client.aclose()
    asyncio.run(run())

@pytest.mark.parametrize('field,value',[('model','eleven_multilingual_v2'),('rate',.8),('voice_id','9'*20)])
def test_review_gate_rejects_unreviewed_settings_or_mixed_voices(tmp_path,field,value):
    import json
    from app.passage_validation import approved_configuration
    reviewed(tmp_path)
    path=tmp_path/'results.json';ledger=json.loads(path.read_text());ledger['rows'][0][field]=value
    path.write_text(json.dumps(ledger))
    assert 'australian' not in approved_configuration(tmp_path)


@pytest.fixture(autouse=True)
def activated_test_feature(monkeypatch):
    from accent_control_helpers import enable_test_controls
    enable_test_controls(monkeypatch)
