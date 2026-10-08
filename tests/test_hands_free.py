"""Automatic recognition must not promote uncertain ASR into learning evidence."""
import pytest
from app.models import Transcription
from app.recognition import reliable_recognition
from test_conversation import begin, scripted
from test_auth import accounts
from test_persistence import repository_factory
from test_onboarding import configure, start


@pytest.mark.parametrize('confidence,uncertainty,text,expected', [
    (.65, [], 'Meaning survives poor grammar', True),
    (.649, [], 'Some recognized speech', False),
    (None, [], 'Some recognized speech', False),
    (.99, ['Low certainty'], 'Some recognized speech', False),
    (.99, [], ' ', False),
])
def test_reliable_recognition_gate(confidence, uncertainty, text, expected):
    assert reliable_recognition(Transcription(text=text, confidence=confidence, uncertainty=uncertainty), .65) is expected


@pytest.mark.parametrize('uncertain,edited', [(True, False), (False, True), (False, False)])
def test_automatic_assessment_preserves_checkpoint_and_evidence_gate(client, audio_bytes, uncertain, edited):
    calls = scripted(client, [{}])
    exercise = begin(client)
    client.provider.uncertain = uncertain
    attempt = client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio':('response.wav',audio_bytes,'audio/wav')}).json()
    before = client.get('/api/profile').json()
    body = {'text': 'Invented answer' if edited else attempt['transcription']['text'], 'confirmed':True, 'hands_free':True}
    response = client.post(f"/api/attempts/{attempt['id']}/assess", json=body)
    if uncertain or edited:
        assert response.status_code == 422 and response.json()['uncertain']
        assert not calls and client.get('/api/profile').json() == before
        assert client.get('/api/history').json() == []
        state = client.get(f"/api/exercises/{exercise['id']}/conversation").json()
        assert state['pending_attempt']['transcription'] == attempt['transcription']
        assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 403
    else:
        assert response.status_code == 200 and len(calls) == 1
        assert client.post(f"/api/attempts/{attempt['id']}/assess", json=body).json() == response.json()
        assert len(calls) == 1


def test_automatic_onboarding_uses_only_saved_reliable_recognition(accounts, audio_bytes):
    a, b, _ = accounts
    calls = configure(a)
    state = start(a)
    a.provider.uncertain = True
    state = a.post('/api/onboarding/attempts', data={'revision':state['revision']}, files={'audio':('a.wav',audio_bytes,'audio/wav')}).json()
    body = {'revision':state['revision'], 'text':state['pending']['text'], 'confirmed':True,'hands_free':True}
    assert a.post('/api/onboarding/answer',json=body).status_code == 422
    assert a.get('/api/onboarding').json() == state and calls == []
    assert b.post('/api/onboarding/answer',json=body).status_code == 409
    a.provider.uncertain = False
    state = a.post('/api/onboarding/attempts', data={'revision':state['revision']}, files={'audio':('a.wav',audio_bytes,'audio/wav')}).json()
    body.update(revision=state['revision'], text='Injected replacement')
    assert a.post('/api/onboarding/answer',json=body).status_code == 422
    body['text'] = state['pending']['text']
    assert a.post('/api/onboarding/answer',json=body).json()['stage'] == 'needs'
    assert calls == ['identity']


def test_retry_speech_is_authenticated_and_does_not_change_progress(accounts):
    a, b, _ = accounts
    before = a.get('/api/profile').json()
    assert a.post('/api/recognition/retry-audio', headers={'Authorization': ''}).status_code == 401
    response = a.post('/api/recognition/retry-audio')
    assert response.status_code == 200 and response.headers['content-type'].startswith('audio/')
    assert a.get('/api/profile').json() == before
    assert b.get('/api/onboarding').json()['profile']['onboarding_status'] == 'not_started'


def test_inline_coaching_audio_keeps_ownership_gates_and_existing_json_contract(accounts, audio_bytes):
    a, b, _ = accounts
    exercise = begin(a)
    path = f"/api/exercises/{exercise['id']}/coach-audio"
    calls = []
    async def speak(text, speech_rate=.9):
        calls.append(text)
        return audio_bytes
    a.provider.speak = speak
    assert a.post(path, json={'cue_id':'feedback'}, headers={'Accept':'audio/*'}).status_code == 403
    assert b.post(path, json={'cue_id':'summary'}, headers={'Accept':'audio/*'}).status_code == 404
    assert a.post(path, json={'cue_id':'summary'}, headers={'Accept':'audio/*','Authorization':''}).status_code == 401
    before = a.get('/api/profile').json()
    inline = a.post(path, json={'cue_id':'summary'}, headers={'Accept':'audio/*'})
    assert inline.status_code == 200 and inline.headers['content-type'].startswith('audio/')
    assert inline.headers['cache-control'] == 'no-store'
    legacy = a.post(path, json={'cue_id':'summary'})
    assert set(legacy.json()) == {'audio_url'}
    assert inline.content == a.get(legacy.json()['audio_url']).content
    assert len(calls) == 1 and a.get('/api/profile').json() == before


def test_inline_feedback_audio_failure_preserves_final_evidence_and_retry(client, audio_bytes):
    from test_conversation import answer
    scripted(client, [{}])
    exercise = begin(client)
    _, _, completed = answer(client, exercise, audio_bytes)
    before = client.get('/api/profile').json()
    path = f"/api/exercises/{exercise['id']}/coach-audio"
    async def fail(text, speech_rate=.9): raise ValueError('Temporary speech failure')
    client.provider.speak = fail
    assert client.post(path, json={'cue_id':'feedback'}, headers={'Accept':'audio/*'}).status_code == 503
    assert client.get('/api/profile').json() == before and len(client.get('/api/history').json()) == 1
    assert client.get(f"/api/exercises/{exercise['id']}/conversation").json()['result'] == completed.json()['result']
    async def speak(text, speech_rate=.9): return audio_bytes
    client.provider.speak = speak
    assert client.post(path, json={'cue_id':'feedback'}, headers={'Accept':'audio/*'}).status_code == 200
