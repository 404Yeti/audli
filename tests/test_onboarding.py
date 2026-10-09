"""Real API/storage transitions; provider extraction is explicitly synthetic."""
import asyncio
import pytest
import httpx
from app.models import LearnerProfile
from app.main import create_app
from app.onboarding import ProfileExtraction
from conftest import ASGIClient
from test_auth import accounts, AccountClient, settings_for, auth_response, USER_A
from test_persistence import repository_factory


def extraction(stage, text):
    return ProfileExtraction(name='Maya' if stage == 'identity' else None,
        target_language='en' if stage == 'identity' else None,
        goal='Understand technical work' if stage == 'needs' else None,
        target_situations=['design discussions', 'customer calls'] if stage == 'needs' else None,
        interests=['science', 'gardening'] if stage == 'interests' else None)


def start(client):
    state = client.get('/api/onboarding').json()
    return client.post('/api/onboarding/start', json={'revision': state['revision']}).json()


def spoken(client, state, audio_bytes, text='My spoken preferences'):
    response = client.post('/api/onboarding/attempts', data={'revision': state['revision']},
        files={'audio': ('answer.wav', audio_bytes, 'audio/wav')})
    assert response.status_code == 200, response.text
    state = response.json()
    response = client.post('/api/onboarding/answer', json={'revision': state['revision'], 'text': text, 'confirmed': True})
    assert response.status_code == 200, response.text
    return response.json()


def configure(client):
    calls = []
    async def extract(stage, text):
        calls.append(stage)
        return extraction(stage, text)
    client.provider.extract_profile = extract
    return calls


def test_authenticated_full_onboarding_restart_and_first_generation(accounts, repository_factory, tmp_path, audio_bytes):
    a, b, _ = accounts
    calls = configure(a)
    original = a.get('/api/profile').json()
    assert original['destination'] == 'onboarding'
    assert original['profile']['onboarding_status'] == 'not_started'
    initial = LearnerProfile.model_validate(original['profile'])
    state = start(a)
    assert state['profile']['onboarding_status'] == 'in_progress'
    state = spoken(a, state, audio_bytes)
    assert state['stage'] == 'needs'
    # Entire application and repository reconstructed, stable authenticated identity.
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    resumed = AccountClient(create_app(settings_for(tmp_path / 'restart'), a.provider,
        repository=repository_factory(None), auth_client=auth), a.provider, 'valid-a')
    assert resumed.get('/api/onboarding').json() == state
    assert b.get('/api/onboarding').json()['profile']['name'] == 'Listener'
    assert b.post('/api/onboarding/answer', json={'revision': state['revision'], 'text':'Steal', 'confirmed':True}).status_code == 409
    state = spoken(resumed, state, audio_bytes)
    state = spoken(resumed, state, audio_bytes)
    assert calls == ['identity', 'needs', 'interests']
    assert state['profile']['onboarding_status'] == 'profile_saved'
    assert resumed.put('/api/profile', json={'name':'Stale setup', 'goal':'Wrong context'}).status_code == 409
    assert state['profile']['initial_listening_profile'] is None
    assert state['profile']['listening_profile'] == initial.listening_profile.model_dump()
    assert state['profile']['difficulty'] == initial.difficulty.model_dump()
    done = resumed.post('/api/onboarding/complete', json={'revision': state['revision']})
    assert done.status_code == 200
    assert resumed.post('/api/onboarding/complete', json={'revision': state['revision']}).json() == done.json()
    assert resumed.get('/api/profile').json()['destination'] == 'session_ready'
    generated = []
    old = a.provider.generate
    async def generate(profile):
        generated.append(profile)
        return await old(profile)
    a.provider.generate = generate
    assert resumed.post('/api/exercises').status_code == 200
    assert generated[0].name == 'Maya'
    assert generated[0].target_situations == ['design discussions', 'customer calls']
    assert generated[0].interests == ['science', 'gardening']
    assert generated[0].goal == 'Understand technical work'
    assert repository_factory(USER_A).profile().onboarding_status == 'complete'
    asyncio.run(auth.aclose())


def test_pending_recognition_resume_and_failures(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    state = start(a)
    revision = state['revision']
    assert a.post('/api/onboarding/complete', json={'revision':revision}).status_code == 409
    assert a.post('/api/onboarding/answer', json={'revision':revision,'text':'invented','confirmed':True}).status_code == 409
    async def fail(*args):
        raise ValueError('private provider payload')
    normal = a.provider.transcribe
    a.provider.transcribe = fail
    assert a.post('/api/onboarding/attempts', data={'revision':revision}, files={'audio':('x.wav',audio_bytes,'audio/wav')}).status_code == 503
    assert a.get('/api/onboarding').json() == state
    a.provider.transcribe = normal
    response = a.post('/api/onboarding/attempts', data={'revision':revision}, files={'audio':('x.wav',audio_bytes,'audio/wav')})
    state = response.json()
    assert state['pending']['text']
    assert a.get('/api/onboarding').json() == state
    a.provider.extract_profile = fail
    body = {'revision':state['revision'],'text':'Maya English','confirmed':True}
    assert a.post('/api/onboarding/answer', json=body).status_code == 503
    assert a.get('/api/onboarding').json() == state
    async def malformed(*args):
        return {'name':'invented', 'unexpected':'value'}
    a.provider.extract_profile = malformed
    assert a.post('/api/onboarding/answer', json=body).status_code == 422
    assert a.get('/api/onboarding').json() == state
    configure(a)
    assert a.post('/api/onboarding/answer', json=body).status_code == 200
    assert a.post('/api/onboarding/answer', json=body).status_code == 409
    a.provider.speak = fail
    current = a.get('/api/onboarding').json()
    assert a.post('/api/onboarding/audio', json={'revision':current['revision']}).status_code == 503
    assert a.get('/api/onboarding').json() == current


@pytest.mark.parametrize('path', ['/api/onboarding', '/api/onboarding/attempts', '/api/onboarding/complete', '/api/onboarding/audio'])
def test_onboarding_requires_auth(accounts, path):
    a, _, _ = accounts
    client = ASGIClient(a.app, a.provider)
    response = client.get(path) if path == '/api/onboarding' else client.post(path, json={'revision':0})
    assert response.status_code == 401


def test_checkpoint_compare_and_save_preserves_learning_and_isolates(repository_factory):
    repo = repository_factory()
    original = repo.profile()
    pending = original.model_copy(deep=True)
    pending.onboarding_status = 'in_progress'
    original.completed_attempts = 3
    original.difficulty.speech_rate = .85
    repo.save_profile(original)
    saved = repo.save_onboarding(pending, 0)
    assert saved.completed_attempts == 3 and saved.difficulty.speech_rate == .85
    with pytest.raises(ValueError, match='changed'):
        repo.save_onboarding(pending, 0)
    assert repository_factory().profile() == saved


@pytest.mark.parametrize('language', ['Spanish', 'fr'])
def test_unsupported_language_does_not_advance(accounts, audio_bytes, language):
    a, _, _ = accounts
    async def extract(*args):
        return extraction('identity', '').model_copy(update={'target_language':language})
    a.provider.extract_profile = extract
    state = start(a)
    state = a.post('/api/onboarding/attempts', data={'revision':state['revision']}, files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    assert a.post('/api/onboarding/answer', json={'revision':state['revision'],'text':'Maya Spanish','confirmed':True}).status_code == 422
    assert a.get('/api/onboarding').json() == state

@pytest.mark.parametrize('stage', ['identity', 'needs', 'interests'])
def test_missing_structured_preferences_request_fresh_answer(accounts, audio_bytes, stage):
    a, _, _ = accounts
    configure(a)
    state = start(a)
    while state['stage'] != stage:
        state = spoken(a, state, audio_bytes)
    state = a.post('/api/onboarding/attempts', data={'revision':state['revision']},
        files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    async def missing(*args):
        return ProfileExtraction(name=None, target_language=None, goal=None, interests=None, target_situations=None)
    a.provider.extract_profile = missing
    response = a.post('/api/onboarding/answer', json={'revision':state['revision'],'text':'Unclear preferences','confirmed':True})
    assert response.status_code == 200
    saved = response.json()
    assert saved['stage'] == stage and saved['pending'] is None
    assert saved['revision'] == state['revision'] + 1
    assert saved['profile']['onboarding']['clarification_count'] == 1
    assert a.get('/api/onboarding').json() == saved


@pytest.mark.parametrize('stage,missing', [('identity','name'), ('identity','target_language'),
    ('needs','goal'), ('needs','target_situations'), ('interests','interests')])
def test_partial_preferences_merge_only_fresh_answers(accounts, audio_bytes, stage, missing):
    a, b, _ = accounts
    configure(a)
    state = start(a)
    while state['stage'] != stage:
        state = spoken(a, state, audio_bytes)
    full = extraction(stage, '')
    async def partial(*args):
        return full.model_copy(update={missing: None})
    a.provider.extract_profile = partial
    state = spoken(a, state, audio_bytes)
    assert state['stage'] == stage and state['pending'] is None
    assert state['profile']['onboarding']['clarification'] == missing
    from app.onboarding import CLARIFICATIONS
    assert state['prompt'] == CLARIFICATIONS[missing]
    prompts = []
    async def speak(text, speech_rate=.9):
        prompts.append(text)
        return audio_bytes
    a.provider.speak = speak
    assert a.post('/api/onboarding/audio', json={'revision':state['revision']}).status_code == 200
    assert prompts == [state['prompt']]
    assert a.get('/api/onboarding').json() == state
    assert b.get('/api/onboarding').json()['profile']['onboarding']['clarification'] is None
    assert a.post('/api/onboarding/answer', json={'revision':state['revision'], 'text':'Old answer', 'confirmed':True}).status_code == 409
    # A response containing only the missing field completes the saved stage.
    async def focused(*args):
        return ProfileExtraction(**{key: (getattr(full, key) if key == missing else None)
            for key in ProfileExtraction.model_fields})
    a.provider.extract_profile = focused
    state = spoken(a, state, audio_bytes, 'Fresh clarification')
    assert state['stage'] != stage
    assert state['profile']['onboarding']['clarification'] is None
    if stage == 'identity':
        assert state['profile']['name'] == 'Maya'


def test_clarification_budget_survives_restart_and_explicit_retry(accounts, repository_factory, tmp_path, audio_bytes):
    a, _, _ = accounts
    async def partial(*args):
        return extraction('identity', '').model_copy(update={'target_language':None})
    a.provider.extract_profile = partial
    state = start(a)
    for _ in range(3):
        state = spoken(a, state, audio_bytes)
    assert state['clarification_paused'] and state['profile']['name'] == 'Maya'
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    resumed = AccountClient(create_app(settings_for(tmp_path / 'partial-restart'), a.provider,
        repository=repository_factory(None), auth_client=auth), a.provider, 'valid-a')
    assert resumed.get('/api/onboarding').json() == state
    assert resumed.post('/api/onboarding/audio', json={'revision':state['revision']}).status_code == 409
    assert resumed.post('/api/onboarding/attempts', data={'revision':state['revision']},
        files={'audio':('x.wav',audio_bytes,'audio/wav')}).status_code == 409
    state = resumed.post('/api/onboarding/retry-clarification', json={'revision':state['revision']}).json()
    assert not state['clarification_paused'] and state['profile']['name'] == 'Maya'
    assert state['profile']['onboarding']['clarification_count'] == 0
    configure(resumed)
    assert spoken(resumed, state, audio_bytes)['stage'] == 'needs'
    asyncio.run(auth.aclose())


def test_uncertain_hands_free_answer_cannot_save_partial_information(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    from app.models import Transcription
    async def uncertain(*args):
        return Transcription(text='Maya', confidence=.2, uncertainty=['unclear'])
    a.provider.transcribe = uncertain
    state = start(a)
    state = a.post('/api/onboarding/attempts', data={'revision':state['revision']},
        files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    assert a.post('/api/onboarding/answer', json={'revision':state['revision'], 'text':'Maya',
        'confirmed':True,'hands_free':True}).status_code == 422
    assert a.get('/api/onboarding').json() == state


def test_no_interest_preference_remains_valid_after_clarification(accounts, audio_bytes):
    a, _, _ = accounts
    configure(a)
    state = start(a)
    for _ in range(2):
        state = spoken(a, state, audio_bytes)
    async def empty(*args):
        return extraction('interests', '').model_copy(update={'interests':None})
    a.provider.extract_profile = empty
    state = spoken(a, state, audio_bytes)
    async def no_preference(*args):
        return extraction('interests', '').model_copy(update={'interests':[]})
    a.provider.extract_profile = no_preference
    state = spoken(a, state, audio_bytes, 'No preference')
    assert state['stage'] == 'review' and state['profile']['interests'] == []


def test_provider_failure_after_partial_answer_retries_recognition_without_losing_name(accounts, audio_bytes):
    a, _, _ = accounts
    async def name_only(*args):
        return extraction('identity', '').model_copy(update={'target_language':None})
    a.provider.extract_profile = name_only
    state = spoken(a, start(a), audio_bytes, 'Maya')
    state = a.post('/api/onboarding/attempts', data={'revision':state['revision']},
        files={'audio':('x.wav',audio_bytes,'audio/wav')}).json()
    async def fail(*args):
        raise ValueError('Synthetic provider failure')
    a.provider.extract_profile = fail
    body = {'revision':state['revision'],'text':'English','confirmed':True}
    assert a.post('/api/onboarding/answer', json=body).status_code == 503
    assert a.get('/api/onboarding').json() == state
    async def language_only(*args):
        return ProfileExtraction(name=None,target_language='en',goal=None,target_situations=None,interests=None)
    a.provider.extract_profile = language_only
    saved = a.post('/api/onboarding/answer', json=body).json()
    assert saved['stage'] == 'needs' and saved['profile']['name'] == 'Maya'


def test_explicit_revision_and_completion_do_not_reset_old_learning(accounts, repository_factory, audio_bytes):
    a, _, _ = accounts
    configure(a)
    # Existing AUD-14 learner with saved setup can complete onboarding without state loss.
    assert a.put('/api/profile', json={'name':'Old name','goal':'Old goal'}).status_code == 200
    state = start(a)
    assert state['profile']['onboarding_status'] == 'in_progress'
    assert state['stage'] == 'identity'
    repo = repository_factory(USER_A)
    profile = repo.profile()
    profile.completed_attempts = 2
    profile.difficulty.speech_rate = .85
    repo.save_profile(profile)
    for _ in range(3):
        state = spoken(a, state, audio_bytes)
    response = a.post('/api/onboarding/revise', json={'revision':state['revision']})
    assert response.status_code == 200
    state = response.json()
    assert state['stage'] == 'identity' and state['profile']['completed_attempts'] == 2
    for _ in range(3):
        state = spoken(a, state, audio_bytes)
    done = a.post('/api/onboarding/complete', json={'revision':state['revision']}).json()
    assert done['profile']['completed_attempts'] == 2
    assert done['profile']['difficulty']['speech_rate'] == .85
    assert a.post('/api/onboarding/revise', json={'revision':done['revision']}).status_code == 409
    assert a.post('/api/onboarding/start', json={'revision':0}).json() == done


def test_onboarding_origin_size_and_schema_guards(accounts, audio_bytes):
    a, _, _ = accounts
    state = start(a)
    assert a.post('/api/onboarding/complete', headers={'Origin':'https://evil.example'}, json={'revision':0}).status_code == 403
    assert a.post('/api/onboarding/answer', content=b'x' * 40001).status_code == 413
    assert a.post('/api/onboarding/start', json={'revision':state['revision'], 'learner_id':USER_A}).status_code == 422
    invalid = a.post('/api/onboarding/attempts', data={'revision':state['revision']}, files={'audio':('x.wav',b'fake audio','audio/wav')})
    assert invalid.status_code == 422
    assert a.get('/api/onboarding').json() == state


def test_prompt_audio_uses_existing_speech_and_recordings_are_not_retained(accounts, audio_bytes, tmp_path):
    a, _, _ = accounts
    state = start(a)
    before = a.get('/api/onboarding').json()
    response = a.post('/api/onboarding/audio', json={'revision':state['revision']})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'audio/wav'
    assert response.headers['cache-control'] == 'no-store'
    assert a.get('/api/onboarding').json() == before
    assert not list(tmp_path.rglob('*.wav'))
    response = a.post('/api/onboarding/attempts', data={'revision':state['revision']}, files={'audio':('x.wav',audio_bytes,'audio/wav')})
    assert response.status_code == 200
    assert not list(tmp_path.rglob('*.wav'))


def test_prompt_cache_retry_restart_owner_and_text_invalidation(accounts, repository_factory, audio_bytes, monkeypatch, tmp_path):
    from app.onboarding import PROMPTS
    a, b, _ = accounts
    state = start(a)
    other = start(b)
    calls = []
    async def speak(text, speech_rate=.9):
        calls.append(text)
        return audio_bytes
    a.provider.speak = speak
    body = {'revision': state['revision']}
    first = a.post('/api/onboarding/audio', json=body)
    assert first.status_code == 200
    assert a.post('/api/onboarding/audio', json=body).content == first.content
    assert len(calls) == 1
    assert a.get('/api/onboarding').json() == state
    # The authenticated second owner must generate its own cached asset.
    b.provider.speak = speak
    assert b.post('/api/onboarding/audio', json={'revision':other['revision']}).status_code == 200
    assert len(calls) == 2
    monkeypatch.setitem(PROMPTS, 'identity', 'An updated complete onboarding prompt.')
    assert a.post('/api/onboarding/audio', json=body).status_code == 200
    assert len(calls) == 3
    # Persisted bytes can be read by a reconstructed repository without local files.
    repo = repository_factory(USER_A)
    from sqlalchemy import select
    from app.storage import schema
    with repo.engine.connect() as db:
        names = db.execute(select(schema.audio_assets.c.audio_name).where(schema.audio_assets.c.user_id == USER_A)).scalars().all()
    assert len(names) == 2
    assert all(repo.audio_blob(name) for name in names)
    assert a.post('/api/onboarding/audio', json={'revision':state['revision']-1}).status_code == 409
    assert len(calls) == 3
    auth = httpx.AsyncClient(transport=httpx.MockTransport(auth_response))
    settings = settings_for(tmp_path / 'new-voice')
    settings.voice = 'alloy'
    resumed = AccountClient(create_app(settings, a.provider, repository=repository_factory(None),
        auth_client=auth), a.provider, 'valid-a')
    assert resumed.post('/api/onboarding/audio', json=body).status_code == 200
    assert len(calls) == 4
    assert resumed.post('/api/onboarding/audio', json=body).status_code == 200
    assert len(calls) == 4
    asyncio.run(auth.aclose())


def test_prompt_cache_failure_does_not_poison_retry(accounts, audio_bytes):
    a, _, _ = accounts
    state = start(a)
    async def fail(*args):
        raise ValueError('synthetic failure')
    a.provider.speak = fail
    body = {'revision':state['revision']}
    assert a.post('/api/onboarding/audio', json=body).status_code == 503
    async def succeed(*args):
        return audio_bytes
    a.provider.speak = succeed
    assert a.post('/api/onboarding/audio', json=body).status_code == 200
    a.provider.speak = fail
    assert a.post('/api/onboarding/audio', json=body).status_code == 200
    assert a.get('/api/onboarding').json() == state


def test_prompt_cache_concurrent_retry_synthesizes_once(accounts, audio_bytes):
    a, _, _ = accounts
    state = start(a)
    calls = []
    async def speak(*args):
        calls.append(True)
        await asyncio.sleep(.02)
        return audio_bytes
    a.provider.speak = speak
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a.app), base_url='http://testserver',
                headers={'Authorization':'Bearer valid-a'}) as client:
            results = await asyncio.gather(*[client.post('/api/onboarding/audio', json={'revision':state['revision']}) for _ in range(2)])
            assert all(result.status_code == 200 for result in results)
            assert results[0].content == results[1].content
    asyncio.run(scenario())
    assert len(calls) == 1
    assert a.get('/api/onboarding').json() == state
