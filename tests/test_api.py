import pytest


def make_attempt(client,audio_bytes):
    exercise=client.post('/api/exercises').json()
    response=client.post(f"/api/exercises/{exercise['id']}/attempts",files={'audio':('answer.wav',audio_bytes,'audio/wav')})
    assert response.status_code==200,response.text
    return exercise,response.json()


def test_full_loop_and_privacy(client,audio_bytes):
    exercise=client.post('/api/exercises').json()
    assert set(exercise)=={'id','difficulty','audio_url','completed_attempt_id'}
    assert 'migration' not in str(exercise)
    assert client.get(exercise['audio_url']).status_code==200
    assert client.get(exercise['audio_url']).headers['cache-control']=='no-store'
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code==403
    assert client.post('/api/exercises').json()['id']==exercise['id']
    exercise,attempt=make_attempt(client,audio_bytes)
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code==403
    result=client.post(f"/api/attempts/{attempt['id']}/evaluate",json={'text':attempt['transcription']['text'],'confirmed':True})
    assert result.status_code==200,result.text
    assert result.json()['evaluation']['overall']==1
    assert result.json()['adaptation']['changed_variable']=='speech_rate'
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").json()['script']
    repeated=client.post(f"/api/attempts/{attempt['id']}/evaluate",json={'text':'Same answer again','confirmed':True})
    assert repeated.json()==result.json()
    assert client.provider.evaluations==1
    assert len(client.get('/api/history').json())==1
    assert client.get('/api/profile').json()['profile']['completed_attempts']==1
    next_exercise=client.post('/api/exercises').json()
    assert next_exercise['id']!=exercise['id']
    assert next_exercise['difficulty']['speech_rate']==.8
    assert client.get(f"/api/exercises/{next_exercise['id']}/transcript").status_code==403


def test_confirmation_and_uncertainty(client,audio_bytes):
    client.provider.uncertain=True
    exercise,attempt=make_attempt(client,audio_bytes)
    assert attempt['transcription']['confidence']==.2
    assert attempt['transcription']['uncertainty']
    response=client.post(f"/api/attempts/{attempt['id']}/evaluate",json={'text':'I heard about an update','confirmed':False})
    assert response.status_code==422
    client.provider.concern=True
    response=client.post(f"/api/attempts/{attempt['id']}/evaluate",json={'text':'I heard about an update','confirmed':True})
    assert response.status_code==422
    assert client.get('/api/profile').json()['profile']['completed_attempts']==0
    assert client.get('/api/history').json()==[]
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code==403
    client.provider.concern=False
    assert client.post(f"/api/attempts/{attempt['id']}/evaluate",json={'text':'I heard about an update','confirmed':True}).status_code==200

@pytest.mark.parametrize('data,mime,status',[(b'not audio'*100,'audio/wav',422),(b'hello','text/plain',415)])
def test_bad_uploads(client,data,mime,status):
    exercise=client.post('/api/exercises').json()
    response=client.post(f"/api/exercises/{exercise['id']}/attempts",files={'audio':('answer',data,mime)})
    assert response.status_code==status


def test_provider_failure_is_safe_and_retryable(client):
    client.provider.fail=True
    response=client.post('/api/exercises')
    assert response.status_code==503
    assert 'SECRET' not in response.text
    assert client.get('/api/exercises/current').json() is None
    client.provider.fail=False
    assert client.post('/api/exercises').status_code==200


def test_unconfirmed_cannot_unlock_and_no_typed_only_attempt(client):
    assert client.post('/api/attempts/nonexistent/evaluate',json={'text':'I know the answer','confirmed':True}).status_code==404
    assert client.get('/api/history').json()==[]


def test_oversized_and_cross_site_requests(client):
    assert client.post('/api/exercises',headers={'origin':'https://malicious.example'}).status_code==403
    assert client.post('/api/exercises',headers={'content-length':'50000'}).status_code==413


def test_state_survives_repository_restart(client,audio_bytes):
    from app.repository import ProgressRepository
    exercise,attempt=make_attempt(client,audio_bytes)
    client.post(f"/api/attempts/{attempt['id']}/evaluate",json={'text':attempt['transcription']['text'],'confirmed':True})
    reopened=ProgressRepository(client.app.state.repository.path)
    assert reopened.profile().completed_attempts==1
    assert reopened.completed(exercise['id'])==attempt['id']
    assert reopened.result(attempt['id'])['adaptation']['teacher_decision'] is None

@pytest.mark.parametrize('failure', ['missing_unit', 'duplicate_unit', 'unquoted_evidence'])
def test_invalid_evaluator_output_preserves_attempt_and_allows_idempotent_retry(client, audio_bytes, caplog, failure):
    import logging
    exercise, attempt = make_attempt(client, audio_bytes)
    original = client.provider.evaluate
    async def invalid(content, transcript):
        judgments = await original(content, transcript)
        if failure == 'missing_unit':
            judgments.units.pop()
        elif failure == 'duplicate_unit':
            judgments.units.append(judgments.units[0])
        else:
            judgments.units[0].evidence = 'Invented private learner quotation.'
        return judgments
    client.provider.evaluate = invalid
    before = client.get('/api/profile').json()
    path = f"/api/attempts/{attempt['id']}/evaluate"
    body = {'text': attempt['transcription']['text'], 'confirmed': True}
    with caplog.at_level(logging.ERROR, logger='audli'):
        failed = client.post(path, json=body)
    assert failed.status_code == 503
    assert 'ComprehensionEvaluator.score_judgments' in caplog.text
    assert 'builtins.ValueError' in caplog.text
    assert 'Invented private learner quotation.' not in caplog.text
    repo = client.app.state.repository
    assert repo.attempt(attempt['id'])['status'] == 'transcribed'
    assert repo.result(attempt['id']) is None
    assert client.get('/api/profile').json() == before
    assert client.get('/api/history').json() == []
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 403
    client.provider.evaluate = original
    retried = client.post(path, json=body)
    assert retried.status_code == 200
    repeated = client.post(path, json=body)
    assert repeated.status_code == 200 and repeated.json() == retried.json()
    assert len(client.get('/api/history').json()) == 1
    assert repo.profile().completed_attempts == 1
