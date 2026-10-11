"""Spoken preference → optional offer → one-passage consent, using real routes."""
import httpx
import pytest
from types import SimpleNamespace
from app.accent_practice import authorized_accent
from app.config import Settings
from app.main import create_app
from app.models import AccentPreferences, Transcription
from conftest import ASGIClient, FakeProvider
from test_persistence import repository_factory
from test_lesson_lifecycle import start, respond, action
from test_accent_practice import CANDIDATE_IDS


@pytest.fixture(autouse=True)
def activate(monkeypatch):
    from accent_control_helpers import enable_test_controls
    enable_test_controls(monkeypatch)


def setup(repo, tmp_path, *, discovery=True, enabled=True):
    profile=repo.profile();profile.onboarding_status='complete'
    if not discovery:
        profile.accent_preferences=AccentPreferences(status='no_preference')
    repo.save_profile(profile)
    provider=FakeProvider()
    settings=Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='fixture',
        accent_practice_enabled=enabled,passage_tts_daily_characters=5000,
        passage_voices={accent:{'voice_id':voice,'validation':'Synthetic approved mapping fixture only'}
                       for accent,voice in CANDIDATE_IDS.items()})
    return ASGIClient(create_app(settings,provider,repo),provider),provider,settings


def reply(client,provider,item,audio_bytes,text):
    async def transcribe(*args):return Transcription(text=text,confidence=.99)
    provider.transcribe=transcribe
    return respond(client,item,audio_bytes)


@pytest.mark.parametrize('accent',['british','australian','irish'])
@pytest.mark.parametrize('answer',['Yes','No thanks','Maybe','I enjoy science'])
def test_preference_offer_routes_only_explicit_acceptance(monkeypatch,repository_factory,tmp_path,audio_bytes,accent,answer):
    repo=repository_factory();client,provider,settings=setup(repo,tmp_path)
    before=repo.profile().model_dump()
    item=start(client)
    item=reply(client,provider,item,audio_bytes,f'{accent.title()} accents are difficult for me.')
    assert repo.profile().accent_preferences.accents==[accent]
    assert authorized_accent(repo.lesson()) is None
    assert repo.lesson().accent_consent.state=='none'
    assert repo.lesson().accent_consent.accent==accent  # 2448c21 confirmation target; not a grant.
    assert repo.lesson().accent_consent.accepted_at is None
    assert repo.lesson().accent_consent.exercise_number is None
    item=action(client,item,'heard')
    assert item['phase']=='REVIEW' and 'Would you like to try' in item['prompt']
    assert accent.title() in item['prompt']
    item=reply(client,provider,item,audio_bytes,answer)
    assert authorized_accent(repo.lesson())==(accent if answer=='Yes' else None)
    item=action(client,item,'heard');assert item['phase']=='TRANSITION'
    item=action(client,item,'heard')
    calls=[]
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append(url)
            assert url.endswith('/'+CANDIDATE_IDS[accent])
            return httpx.Response(200,content=audio_bytes,request=httpx.Request('POST',url))
    monkeypatch.setattr('app.accent_practice.httpx',SimpleNamespace(
        AsyncClient=lambda **kwargs:Client(),HTTPError=httpx.HTTPError))
    item=action(client,item,'exercise')
    routing=item['exercise']['passage_voice']
    assert routing['provider']==('elevenlabs' if answer=='Yes' else 'openai')
    assert routing['accent']==(accent if answer=='Yes' else 'default')
    assert len(calls)==(1 if answer=='Yes' else 0)
    if answer=='Yes':
        assert routing['voice_id']==CANDIDATE_IDS[accent]
        assert repo.lesson().accent_consent.state=='consumed'
    assert action(client,item,'exercise')['exercise']['passage_voice']==routing
    assert client.get(item['exercise']['audio_url']).status_code==200
    assert client.get(f"/api/exercises/{item['exercise']['id']}/transcript").status_code==403
    after=repo.profile().model_dump();after['accent_preferences']=before['accent_preferences']
    assert after==before
    assert settings.elevenlabs_voice_id=='yKYzqEa22xh5PdidhN70'


@pytest.mark.parametrize('phase',['WELCOME','REVIEW'])
def test_spontaneous_preference_offers_and_preserves_topic_review(repository_factory,tmp_path,audio_bytes,phase):
    repo=repository_factory();client,provider,_=setup(repo,tmp_path,discovery=False)
    item=start(client)
    if phase=='REVIEW':
        stored=repo.lesson();stored.phase='REVIEW';stored.previous='Last time we listened to science.'
        repo.save_lesson(stored,stored.revision);item=client.get('/api/lessons/current').json()
    item=reply(client,provider,item,audio_bytes,'Australian accents are difficult for me.')
    if phase=='WELCOME':item=action(client,item,'heard')
    assert item['phase']=='REVIEW' and 'Australian' in item['prompt']
    assert authorized_accent(repo.lesson()) is None
    item=reply(client,provider,item,audio_bytes,'No thanks')
    item=action(client,item,'heard')
    if phase=='REVIEW':
        assert item['phase']=='REVIEW' and 'enjoy listening' in item['prompt']
        item=reply(client,provider,item,audio_bytes,'Australian accents are difficult for me.')
        assert not repo.lesson().accent_confirmation
        assert authorized_accent(repo.lesson()) is None
    else:assert item['phase']=='TRANSITION'


@pytest.mark.parametrize('preference',['Scottish accents are difficult for me.','No preference'])
def test_unsupported_or_refused_discovery_does_not_offer(repository_factory,tmp_path,audio_bytes,preference):
    repo=repository_factory();client,provider,_=setup(repo,tmp_path)
    item=reply(client,provider,start(client),audio_bytes,preference)
    assert not repo.lesson().accent_confirmation and repo.lesson().accent_offered is None
    assert authorized_accent(repo.lesson()) is None
    assert action(client,item,'heard')['phase']=='TRANSITION'


def test_disabled_feature_saves_preference_without_offer(repository_factory,tmp_path,audio_bytes):
    repo=repository_factory();client,provider,_=setup(repo,tmp_path,enabled=False)
    item=reply(client,provider,start(client),audio_bytes,'Australian accents are difficult for me.')
    assert repo.profile().accent_preferences.accents==['australian']
    assert not repo.lesson().accent_confirmation
    assert action(client,item,'heard')['phase']=='TRANSITION'


@pytest.mark.parametrize('answer,accent',[('Irish instead please','irish'),
    ('Try a Scottish accent instead',None)])
def test_offer_accepts_supported_switch_without_substitution(repository_factory,tmp_path,audio_bytes,answer,accent):
    repo=repository_factory();client,provider,_=setup(repo,tmp_path)
    item=reply(client,provider,start(client),audio_bytes,'Australian')
    item=action(client,item,'heard')
    reply(client,provider,item,audio_bytes,answer)
    assert authorized_accent(repo.lesson())==accent
    assert repo.profile().accent_preferences.accents==['australian']


def test_pending_offer_survives_restart_and_cancels_safely(repository_factory,tmp_path,audio_bytes):
    repo=repository_factory();client,provider,settings=setup(repo,tmp_path)
    item=reply(client,provider,start(client),audio_bytes,'Australian')
    item=action(client,item,'heard')
    reopened=repository_factory()
    restarted=ASGIClient(create_app(settings,provider,reopened),provider)
    assert restarted.get('/api/lessons/current').json()['prompt']==item['prompt']
    assert authorized_accent(reopened.lesson()) is None
    item=action(restarted,item,'end')
    assert not reopened.lesson().accent_confirmation
    assert authorized_accent(reopened.lesson()) is None
