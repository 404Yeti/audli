"""Account-scoped typed names; synthetic speech providers, no paid calls."""
import asyncio
import httpx
import pytest
from test_onboarding import start, spoken, configure
from test_auth import accounts, USER_A
from test_persistence import repository_factory


def submit(client, state, name):
    return client.post('/api/onboarding/name', json={'question_revision': state['name_question_revision'], 'name': name})


@pytest.mark.parametrize('name,normalized', [('  Nguyễn   Thị Mỹ  ', 'Nguyễn Thị Mỹ'), ('Jose\u0301', 'José'), ('李 小龍', '李 小龍'), ('Марія', 'Марія'), ("O’Connor", "O’Connor")])
def test_typed_name_persists_resumes_and_advances_once(accounts, repository_factory, name, normalized):
    a, b, _ = accounts
    state = start(a)
    assert state['name_entry_available']
    before = repository_factory(USER_A).profile()
    response = submit(a, state, name)
    assert response.status_code == 200
    saved = response.json()
    assert saved['stage'] == 'identity' and not saved['name_entry_available']
    assert saved['profile']['name'] == normalized and saved['pending'] is None
    assert submit(a, state, name).json() == saved
    assert a.get('/api/onboarding').json() == saved
    assert repository_factory(USER_A).profile().name == normalized
    assert saved['profile']['difficulty'] == before.difficulty.model_dump()
    assert b.get('/api/onboarding').json()['profile']['name'] == 'Listener'


@pytest.mark.parametrize('name', ['', '   ', 'a'*81, '<script>alert(1)</script>', '1234', 'A\x00B', 'A\u202eB', 'A\nB', 'A/B'])
def test_invalid_name_does_not_change_checkpoint(accounts, name):
    a, _, _ = accounts
    state = start(a)
    assert submit(a, state, name).status_code == 422
    assert a.get('/api/onboarding').json() == state


def test_voice_first_then_typed_wins_and_late_voice_rejected(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    original = start(a)
    voice = spoken(a, original, audio_bytes)
    assert voice['profile']['name'] == 'Maya'
    saved = submit(a, original, 'Nguyễn Mỹ').json()
    assert saved['profile']['name'] == 'Nguyễn Mỹ' and saved['stage'] == 'needs'
    assert a.post('/api/onboarding/attempts', data={'revision':voice['revision']}, files={'audio':('x.wav',audio_bytes,'audio/wav')}).status_code == 409
    state = spoken(a, saved, audio_bytes)
    assert state['profile']['name'] == 'Nguyễn Mỹ'
    assert submit(a, original, 'Other').status_code == 409


def test_simultaneous_transcription_and_typed_submission(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    state = start(a)
    old = a.provider.transcribe
    async def delayed(*args):
        await asyncio.sleep(.05)
        return await old(*args)
    a.provider.transcribe = delayed
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a.app), base_url='http://testserver', headers={'Authorization':'Bearer valid-a'}) as client:
            voice, typed = await asyncio.gather(client.post('/api/onboarding/attempts', data={'revision':state['revision']}, files={'audio':('x.wav',audio_bytes,'audio/wav')}), client.post('/api/onboarding/name', json={'question_revision':state['name_question_revision'], 'name':'Đặng Hà'}))
            assert voice.status_code in (200,409)
            assert typed.status_code == 200
    asyncio.run(run())
    saved = a.get('/api/onboarding').json()
    assert saved['stage'] == 'identity' and saved['profile']['name'] == 'Đặng Hà' and saved['pending'] is None


def test_name_route_auth_and_question_scope(accounts, audio_bytes):
    from conftest import ASGIClient
    a, _, _ = accounts
    assert ASGIClient(a.app,a.provider).post('/api/onboarding/name',json={'question_revision':0,'name':'Maya'}).status_code == 401
    state = a.get('/api/onboarding').json()
    assert submit(a,state,'Maya').status_code == 409
    state = start(a)
    assert a.post('/api/onboarding/name',json={'question_revision':state['name_question_revision'],'name':'Maya','learner_id':USER_A}).status_code == 422
    assert a.post('/api/onboarding/name',json={'question_revision':999,'name':'Maya'}).status_code == 409
    configure(a)
    original = state
    state = submit(a,state,'Maya').json()
    state = spoken(a,state,audio_bytes)
    state = spoken(a,state,audio_bytes)
    assert submit(a,original,'Other').status_code == 409


@pytest.mark.parametrize('name', ['Existing learner', 'Listener'])
def test_existing_saved_name_is_not_asked_again(accounts, name):
    a, _, _ = accounts
    assert a.put('/api/profile', json={'name':name,'goal':'Meetings'}).status_code == 200
    state = start(a)
    assert not state['name_entry_available'] and 'what should I call you' not in state['prompt'].lower()
    assert state['profile']['name'] == name


def test_typed_name_retains_language_question_and_late_extraction_cannot_overwrite(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    state = start(a)
    typed = submit(a,state,'Nguyễn Mỹ').json()
    assert typed['profile']['onboarding']['clarification'] == 'target_language'
    assert typed['revision'] == state['revision'] + 1
    # A later voice extraction includes a guessed name as well as English.
    completed = spoken(a,typed,audio_bytes)
    assert completed['stage'] == 'needs' and completed['profile']['name'] == 'Nguyễn Mỹ'


def test_typed_name_wins_inflight_identity_extraction(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    original = start(a)
    pending = a.post('/api/onboarding/attempts',data={'revision':original['revision']},files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    normal = a.provider.extract_profile
    async def delayed(*args):
        await asyncio.sleep(.05)
        return await normal(*args)
    a.provider.extract_profile = delayed
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a.app),base_url='http://testserver',headers={'Authorization':'Bearer valid-a'}) as client:
            voice, typed = await asyncio.gather(client.post('/api/onboarding/answer',json={'revision':pending['revision'],'text':pending['pending']['text'],'confirmed':True}),client.post('/api/onboarding/name',json={'question_revision':original['name_question_revision'],'name':'Đặng Hà'}))
            assert voice.status_code in (200,409) and typed.status_code == 200
    asyncio.run(run())
    saved = a.get('/api/onboarding').json()
    assert saved['stage'] == 'needs' and saved['profile']['name'] == 'Đặng Hà'


def test_resumed_pending_name_and_saved_typing_survive_app_restart(accounts, repository_factory, tmp_path):
    from app.main import create_app
    from test_auth import AccountClient, settings_for, auth_response
    a, _, _ = accounts
    state = start(a)
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    resumed = AccountClient(create_app(settings_for(tmp_path/'name-resume'),a.provider,repository=repository_factory(None),auth_client=auth),a.provider,'valid-a')
    assert resumed.get('/api/onboarding').json() == state
    saved = submit(resumed,state,'Trần Hà').json()
    assert a.get('/api/onboarding').json() == saved
    assert submit(a,state,'Trần Hà').json() == saved
    asyncio.run(auth.aclose())


def test_name_window_closes_before_unrelated_voice_answer(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    original = start(a)
    state = spoken(a,original,audio_bytes)
    pending = a.post('/api/onboarding/attempts',data={'revision':state['revision']},files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    assert submit(a,original,'Late name').status_code == 409
    assert a.get('/api/onboarding').json() == pending
