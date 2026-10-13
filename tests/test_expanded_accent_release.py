"""AUD-38 release authorization, voice identity, preferences and consent."""
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.accent_registry import SELECTED_VOICES, EXISTING_VOICES, LABELS, routing_allowed
from app.accent_intent import interpret
from app.accent_practice import resolve, authorized_accent, passage_audio, VerifiedPassageVoice
from app.config import Settings
from app.lesson import Lesson, now
from app.models import LearnerProfile
from app.onboarding import apply_accent_answer
from app.passage_validation import approved_configuration
from test_auth import accounts, USER_A
from test_persistence import repository_factory
from accent_control_helpers import scope
from app.storage.accent_controls import SharedAccentControls

EVIDENCE = Path(__file__).resolve().parents[1] / 'docs/aud38-release-evidence'


@pytest.mark.parametrize('accent', SELECTED_VOICES)
def test_new_accent_requests_preferences_and_one_passage_consent(accent):
    name = LABELS[accent]
    voice = SELECTED_VOICES[accent]
    assert voice.status == 'enabled' and routing_allowed(accent, voice.voice_id)
    assert not routing_allowed(accent, 'x'*20)
    parsed = interpret(f'Can we practice {name}?')
    assert parsed.kind == 'request' and parsed.accent == accent
    assert interpret(f'Could we practise {name}?').accent == accent
    profile = LearnerProfile()
    profile.onboarding.stage = 'accents'
    updated = apply_accent_answer(profile, name)
    assert updated.accent_preferences.accents == [accent]
    item = Lesson(id=str(uuid4()), name='Maya', focus='details', started_at=now(), active_since=now())
    assert authorized_accent(item) is None
    resolve(item, f'Can we practice {name}?', {accent: object()})
    assert authorized_accent(item) == accent
    resolve(item, 'Cancel', {accent: object()})
    assert authorized_accent(item) is None
    assert interpret(f'Maybe try {name}').kind == 'ambiguous'


@pytest.mark.parametrize('accent', SELECTED_VOICES)
def test_authenticated_existing_learner_can_save_new_accent_without_consent(accounts, accent):
    a,b,_ = accounts
    a.get('/api/profile'); b.get('/api/profile')
    repo = a.app.state.repository.for_learner(USER_A)
    profile = repo.profile(); profile.onboarding_status='complete'; profile.completed_attempts=7
    repo.save_profile(profile)
    before = repo.profile().model_dump(); other = b.get('/api/profile').json()
    response = a.put('/api/profile/accent-preferences', json={'accents':[accent]})
    assert response.status_code == 200 and response.json()['accents'] == [accent]
    after = repo.profile().model_dump(); after['accent_preferences'] = before['accent_preferences']
    assert after == before and b.get('/api/profile').json() == other
    assert repo.lesson() is None


def test_shipped_owner_approval_matches_all_real_sample_hashes():
    approved = approved_configuration(EVIDENCE)
    assert set(approved) == set(SELECTED_VOICES)
    assert all(approved[a]['voice_id'] == v.voice_id for a,v in SELECTED_VOICES.items())
    assert Settings(_env_file=None).elevenlabs_voice_id == 'yKYzqEa22xh5PdidhN70'
    assert {v.accent:v.voice_id for v in EXISTING_VOICES} == dict(british='onwK4e9ZLuTAKqWW03F9',
        australian='tyepWYJJwJM9TTFIg5U7', irish='DbwWo4rVEd5NrejHYUnm')


@pytest.mark.parametrize('corruption', ['audio','approval','results'])
def test_approval_gate_rejects_tampered_expansion(tmp_path,corruption):
    shutil.copytree(EVIDENCE,tmp_path/'review'); path=tmp_path/'review'
    if corruption=='audio': (path/'AUD38-indian.mp3').write_bytes(b'changed')
    elif corruption=='approval': (path/'product-owner-approval.json').unlink()
    else:
        ledger=json.loads((path/'results.json').read_text());ledger['rows'][0]['voice_id']='x'*20
        (path/'results.json').write_text(json.dumps(ledger))
    assert 'indian' not in approved_configuration(path)


@pytest.mark.parametrize('accent', SELECTED_VOICES)
@pytest.mark.parametrize('quota', [False, True])
def test_exact_passage_routing_at_point8_and_quota_fallback(repository_factory,monkeypatch,accent,quota):
    repo=repository_factory(); record=SELECTED_VOICES[accent]
    settings=Settings(_env_file=None,elevenlabs_api_key='fixture',accent_practice_enabled=True,
        passage_tts_daily_characters=10000,passage_voices={accent:VerifiedPassageVoice(voice_id=record.voice_id,
        validation='Synthetic provider fixture; production approval tested separately')})
    controls=SharedAccentControls(repo.engine,settings)
    controls.configure(enabled=True,daily_limit=10000,concurrent_limit=2)
    lesson=scope(repo,settings,accent)
    resolve(lesson, f'Practice {LABELS[accent]}', settings.passage_voices)
    repo.save_lesson(lesson,lesson.revision)
    lesson=repo.lesson();calls=[]
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            calls.append((url,kwargs['json']))
            return httpx.Response(401 if quota else 200,content=b'fixture passage audio',request=httpx.Request('POST',url))
    class Provider:
        async def speech(self,content):return b'usual voice'
    async def sanitize(*args):return b'fixture passage audio'
    monkeypatch.setattr('app.accent_practice.httpx.AsyncClient',lambda **kwargs:Client())
    monkeypatch.setattr('app.audio.sanitize_generated_audio',sanitize)
    audio,voice=asyncio.run(passage_audio(settings,Provider(),SimpleNamespace(script='Original fixture English',speech_rate=.8),
        accent,controls,owner=repo.learner_id,lesson=lesson))
    assert len(calls)==1 and calls[0][0].endswith('/'+record.voice_id)
    assert calls[0][1]['voice_settings']['speed']==.8
    assert voice.accent==('default' if quota else accent) and voice.fallback==quota
    assert audio==(b'usual voice' if quota else b'fixture passage audio')


def test_expansion_flag_requires_complete_evidence_and_preserves_old_mapping():
    original = {'australian': VerifiedPassageVoice(voice_id='tyepWYJJwJM9TTFIg5U7', validation='Existing human approval record')}
    enabled = Settings(_env_file=None, elevenlabs_api_key='fixture', expanded_accent_library_enabled=True,
        expanded_passage_review_dir=EVIDENCE, passage_voices=original, accent_practice_enabled=True, passage_tts_daily_characters=10000)
    assert set(enabled.available_passage_voices) == {'australian', *SELECTED_VOICES}
    assert enabled.available_passage_voices['australian'].voice_id == 'tyepWYJJwJM9TTFIg5U7'
    disabled = Settings(_env_file=None, elevenlabs_api_key='fixture', passage_voices={'australian': original['australian']},
        accent_practice_enabled=True, passage_tts_daily_characters=10000)
    assert set(disabled.available_passage_voices) == {'australian'}
    with pytest.raises(ValueError, match='owner-approved'):
        Settings(_env_file=None, elevenlabs_api_key='fixture', expanded_accent_library_enabled=True,
            expanded_passage_review_dir=Path('/nonexistent-fixture-review'))


def test_nonbillable_health_reports_verified_release_configuration(tmp_path,monkeypatch):
    from app.main import create_app
    from conftest import ASGIClient, FakeProvider
    monkeypatch.setenv('RENDER_GIT_COMMIT','fixture-release-revision')
    settings=Settings(_env_file=None,data_dir=tmp_path,elevenlabs_api_key='protected-fixture-key',
        accent_practice_enabled=True,passage_tts_daily_characters=5000,passage_tts_max_concurrent=1,
        expanded_accent_library_enabled=True,expanded_passage_review_dir=EVIDENCE)
    provider=FakeProvider(); response=ASGIClient(create_app(settings,provider),provider).get('/api/health')
    assert response.status_code==200 and 'protected-fixture-key' not in response.text
    value=response.json()
    assert value['revision']=='fixture-release-revision'
    assert value['passage_voice_ids']=={a:v.voice_id for a,v in SELECTED_VOICES.items()}
    assert value['tutor_voice_id']=='yKYzqEa22xh5PdidhN70'
    assert value['passage_tts_daily_characters']==5000 and value['passage_tts_max_concurrent']==1
