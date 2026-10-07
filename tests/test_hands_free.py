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
