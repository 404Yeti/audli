"""V0.2 domain/API fixtures. Prescribed AI evidence tests flow, not real model accuracy."""
import json
import pytest
from app.evaluation import expected_units
from app.models import EvidenceAssessment, ExerciseContent
from app.conversation import final_evaluation, validate_evidence

QUESTIONS = {'main_idea': 'What was the speaker mainly talking about?',
             'details': 'What happened after the update?',
             'vocabulary': "What did the speaker mean by 'rolled back'?",
             'inference': 'Why do you think Maya chose to wait?'}


def assessment(exercise, turns, statuses=None):
    statuses = statuses or {}
    return EvidenceAssessment(units=[{'dimension': dim, 'index': i,
        'status': statuses.get(dim, 'demonstrated'),
        'evidence': '' if statuses.get(dim) == 'insufficient_evidence' else turns[-1].text,
        'question': QUESTIONS[dim] if statuses.get(dim) == 'insufficient_evidence' else ''}
        for dim, units in expected_units(exercise).items() for i in range(len(units))],
        feedback='You caught the main idea. Let’s keep listening for the reasons.',
        transcription_concern=False, concern_reason='')


def scripted(client, outcomes):
    calls = []
    async def assess(exercise, turns):
        calls.append(turns)
        outcome = outcomes[min(len(calls)-1, len(outcomes)-1)]
        if isinstance(outcome, Exception): raise outcome
        return assessment(exercise, turns, outcome)
    client.provider.assess = assess
    return calls


def begin(client):
    exercise = client.post('/api/exercises').json()
    state = client.post(f"/api/exercises/{exercise['id']}/conversation/listened")
    assert state.status_code == 200
    assert state.json()['state'] == 'AWAITING_SUMMARY'
    return exercise


def answer(client, exercise, audio_bytes, question=None, text=None):
    uploaded = client.post(f"/api/exercises/{exercise['id']}/attempts",
        files={'audio': ('response.wav', audio_bytes, 'audio/wav')})
    assert uploaded.status_code == 200
    attempt = uploaded.json()
    body = {'text': text or attempt['transcription']['text'], 'confirmed': True,
            'followup_id': question['id'] if question else None}
    result = client.post(f"/api/attempts/{attempt['id']}/assess", json=body)
    return attempt, body, result


@pytest.mark.parametrize('text', ['The team rolled back the update because the database field was missing.',
                                  'Team update fail, database missing field, go back old app.'])
def test_all_demonstrated_and_poor_grammar_need_zero_followups(client, audio_bytes, text):
    calls = scripted(client, [{}])
    exercise = begin(client)
    attempt, body, response = answer(client, exercise, audio_bytes, text=text)
    assert response.status_code == 200
    data = response.json()
    assert data['followups_asked'] == 0 and data['active_followup'] is None
    assert data['result']['evaluation']['overall'] == 1
    assert data['result']['evaluation']['coverage'] == 1
    assert set(data['result']['evaluation']['dimensions'].values()) == {'demonstrated'}
    assert len(calls) == 1
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 200
    repeated = client.post(f"/api/attempts/{attempt['id']}/assess", json=body)
    assert repeated.json() == data and len(calls) == 1
    assert len(client.get('/api/history').json()) == 1


@pytest.mark.parametrize('dimension', ['inference', 'vocabulary'])
def test_unknown_is_not_zero_and_gets_only_targeted_followup(client, audio_bytes, dimension):
    scripted(client, [{dimension: 'insufficient_evidence'}, {}])
    exercise = begin(client)
    before = client.get('/api/profile').json()
    attempt, body, response = answer(client, exercise, audio_bytes)
    assert response.status_code == 200
    data = response.json()
    assert data['state'] == 'AWAITING_FOLLOWUP' and data['result'] is None
    assert data['active_followup']['dimension'] == dimension
    repo = client.app.state.repository
    conversation = repo.conversation(exercise['id'])
    assert {unit.status for unit in conversation.assessment.units if unit.dimension == dimension} == {'insufficient_evidence'}
    content = ExerciseContent.model_validate_json(repo.exercise(exercise['id'])['content'])
    assert getattr(final_evaluation(content, conversation.assessment), dimension) is None
    assert client.get('/api/profile').json() == before
    assert client.get('/api/history').json() == []
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 403
    assert client.post(f"/api/attempts/{attempt['id']}/evaluate", json=body | {'followup_id': None}).status_code in (409, 422)
    _, _, clarified = answer(client, exercise, audio_bytes, data['active_followup'], text='They wanted to avoid another failure by testing first.')
    assert clarified.status_code == 200
    final = clarified.json()['result']
    assert getattr(repo.conversation(exercise['id']).assessment.units[-1], 'status') == 'demonstrated'
    assert final['evaluation'][dimension] == 1 and final['evaluation']['overall'] == 1
    assert final['adaptation']['decision'] == 'harder'
    assert len(repo.history()) == 1


def test_paraphrased_vocabulary_does_not_require_the_target_word(client, audio_bytes):
    scripted(client, [{}])
    exercise = begin(client)
    text = 'They returned to the previous version and delayed the release until Friday.'
    _, _, response = answer(client, exercise, audio_bytes, text=text)
    assert 'rolled back' not in text
    assert response.json()['followups_asked'] == 0
    assert response.json()['result']['evaluation']['vocabulary'] == 1


def test_final_clarification_not_initial_summary_drives_adaptation(client, audio_bytes):
    scripted(client, [{'details': 'insufficient_evidence'}, {'details': 'misunderstood'}])
    exercise = begin(client)
    initial_profile = client.get('/api/profile').json()['profile']
    _, _, initial = answer(client, exercise, audio_bytes)
    assert initial.json()['result'] is None
    assert client.get('/api/profile').json()['profile'] == initial_profile
    _, _, final = answer(client, exercise, audio_bytes, initial.json()['active_followup'],
                         text='They cancelled the release forever and never restored the app.')
    result = final.json()['result']
    assert result['evaluation']['details'] == 0
    assert result['evaluation']['dimensions']['details'] == 'misunderstood'
    assert result['evaluation']['overall'] == .6
    event = result['adaptation']
    assert event['decision'] == 'easier' and event['previous_score'] == .6
    assert len([key for key in event['old_difficulty'] if event['old_difficulty'][key] != event['new_difficulty'][key]]) == 1
    next_exercise = client.post('/api/exercises').json()
    assert next_exercise['difficulty'] == event['new_difficulty']


def test_confirmed_misunderstanding_does_not_get_rescue_questions(client, audio_bytes):
    scripted(client, [{dim: 'misunderstood' for dim in QUESTIONS}])
    exercise = begin(client)
    _, _, response = answer(client, exercise, audio_bytes, text='The update worked perfectly and nothing was changed.')
    data = response.json()
    assert data['followups_asked'] == 0
    assert data['result']['evaluation']['overall'] == 0
    assert data['result']['adaptation']['decision'] == 'easier'


def test_two_question_limit_unknown_scores_stay_unknown_and_difficulty_stays_stable(client, audio_bytes):
    scripted(client, [{dim: 'insufficient_evidence' for dim in QUESTIONS}])
    exercise = begin(client)
    before = client.get('/api/profile').json()['profile']
    _, _, initial = answer(client, exercise, audio_bytes)
    _, _, second = answer(client, exercise, audio_bytes, initial.json()['active_followup'])
    assert second.json()['result'] is None
    _, _, final = answer(client, exercise, audio_bytes, second.json()['active_followup'])
    data = final.json()
    assert data['followups_asked'] == 2 and data['active_followup'] is None
    assert data['result']['evaluation']['overall'] is None
    assert data['result']['evaluation']['inference'] is None
    assert data['result']['adaptation']['previous_score'] is None
    assert data['result']['adaptation']['decision'] == 'same'
    after = client.get('/api/profile').json()['profile']
    assert after['difficulty'] == before['difficulty'] and after['listening_profile'] == before['listening_profile']
    assert after['completed_attempts'] == before['completed_attempts'] + 1
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 200


def test_resume_and_retry_preserve_recognized_response_and_question(client, audio_bytes):
    from app.main import create_app
    from app.config import Settings
    from conftest import ASGIClient
    calls = scripted(client, [ValueError('Temporary evaluator failure'), {'inference': 'insufficient_evidence'}, {}])
    exercise = begin(client)
    attempt, body, failed = answer(client, exercise, audio_bytes, text='I understood the problem and the rollback.')
    assert failed.status_code == 503
    path = f"/api/exercises/{exercise['id']}/conversation"
    resumed = client.get(path).json()
    assert resumed['state'] == 'ASSESSING'
    assert resumed['pending_attempt']['id'] == attempt['id']
    assert resumed['pending_attempt']['confirmed_text'] == body['text']
    reopened = ASGIClient(create_app(Settings(data_dir=client.app.state.repository.path.parent, _env_file=None), client.provider), client.provider)
    assert reopened.get(path).json() == resumed
    retry = reopened.post(f"/api/attempts/{attempt['id']}/assess", json=body)
    assert retry.json()['state'] == 'AWAITING_FOLLOWUP'
    assert len(calls) == 2
    assert reopened.post(f"/api/attempts/{attempt['id']}/assess", json=body).json() == retry.json()
    assert len(calls) == 2
    assert reopened.get(path).json()['active_followup'] == retry.json()['active_followup']


def test_complete_followups_gate_legacy_api_and_feedback_audio(client, audio_bytes):
    scripted(client, [{'inference': 'insufficient_evidence'}, {}])
    exercise = begin(client)
    attempt, body, initial = answer(client, exercise, audio_bytes)
    body.pop('followup_id')
    assert client.post(f"/api/attempts/{attempt['id']}/evaluate", json=body).status_code == 409
    prefix = f"/api/exercises/{exercise['id']}"
    assert client.post(prefix+'/coach-audio', json={'cue_id': 'feedback'}).status_code == 403
    assert client.get(prefix+'/coach-audio/feedback').status_code == 403
    # Learner responses are available; rubric, script and expected labels are not.
    public = json.dumps(initial.json())
    assert 'expected_units' not in public and 'important_details' not in public and 'script' not in public
    _, _, final = answer(client, exercise, audio_bytes, initial.json()['active_followup'])
    assert final.status_code == 200
    assert client.get(prefix+'/transcript').status_code == 200


def test_spoken_cues_cached_served_and_feedback_failure_keeps_assessment(client, audio_bytes):
    scripted(client, [{}])
    calls = []
    async def speak(text, speech_rate=.9):
        calls.append(text)
        return audio_bytes
    client.provider.speak = speak
    exercise = begin(client)
    prefix = f"/api/exercises/{exercise['id']}"
    summary = client.post(prefix+'/coach-audio', json={'cue_id': 'summary'})
    assert summary.status_code == 200 and client.get(summary.json()['audio_url']).status_code == 200
    assert client.post(prefix+'/coach-audio', json={'cue_id': 'summary'}).json() == summary.json()
    assert len(calls) == 1
    _, _, completed = answer(client, exercise, audio_bytes)
    result = completed.json()['result']
    async def fail(text, speech_rate=.9): raise ValueError('Speech provider unavailable')
    client.provider.speak = fail
    assert client.post(prefix+'/coach-audio', json={'cue_id': 'feedback'}).status_code == 503
    assert client.get(prefix+'/conversation').json()['result'] == result
    assert len(client.get('/api/history').json()) == 1
    assert client.get(prefix+'/transcript').status_code == 200
    client.provider.speak = speak
    feedback = client.post(prefix+'/coach-audio', json={'cue_id': 'feedback'})
    assert feedback.status_code == 200
    assert client.get(feedback.json()['audio_url']).status_code == 200
    assert not any(character.isdigit() for character in calls[-1])
    assert client.post(prefix+'/conversation/ready').json()['state'] == 'READY_FOR_NEXT'
    assert client.get(prefix+'/conversation').json()['result'] == result


def test_invalid_v2_quote_and_transcription_uncertainty_do_not_advance(client, audio_bytes):
    async def invalid(exercise, turns):
        result = assessment(exercise, turns)
        result.units[0].evidence = 'Invented quotation absent from all learner turns.'
        return result
    client.provider.assess = invalid
    exercise = begin(client)
    _, _, response = answer(client, exercise, audio_bytes)
    assert response.status_code == 503 and client.get('/api/history').json() == []
    async def uncertain(exercise, turns):
        result = assessment(exercise, turns); result.transcription_concern = True; return result
    client.provider.assess = uncertain
    pending = client.get(f"/api/exercises/{exercise['id']}/conversation").json()['pending_attempt']
    response = client.post(f"/api/attempts/{pending['id']}/assess", json={'text': pending['confirmed_text'], 'confirmed': True})
    assert response.status_code == 422 and client.get('/api/history').json() == []
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 403


def test_incorrect_inference_followup_becomes_confirmed_misunderstanding(client, audio_bytes):
    scripted(client, [{'inference': 'insufficient_evidence'}, {'inference': 'misunderstood'}])
    exercise = begin(client)
    _, _, initial = answer(client, exercise, audio_bytes)
    assert initial.json()['result'] is None
    _, _, final = answer(client, exercise, audio_bytes, initial.json()['active_followup'],
                         text='She waited because she never wanted customers to use the app again.')
    result = final.json()['result']
    assert result['evaluation']['inference'] == 0
    assert result['evaluation']['dimensions']['inference'] == 'misunderstood'
    assert result['evaluation']['insufficient_evidence'] == []
    assert result['adaptation']['previous_score'] == result['evaluation']['overall'] == .9


def test_stale_followup_and_unconfirmed_turn_cannot_advance(client, audio_bytes):
    calls = scripted(client, [{'inference': 'insufficient_evidence'}, {}])
    exercise = begin(client)
    _, _, initial = answer(client, exercise, audio_bytes)
    attempt = client.post(f"/api/exercises/{exercise['id']}/attempts",
        files={'audio': ('response.wav', audio_bytes, 'audio/wav')}).json()
    path = f"/api/attempts/{attempt['id']}/assess"
    body = {'text': 'They wanted safer tests before releasing again.', 'confirmed': False,
            'followup_id': initial.json()['active_followup']['id']}
    assert client.post(path, json=body).status_code == 422
    assert client.post(path, json={**body, 'confirmed': True, 'followup_id': 'stale-id'}).status_code == 409
    assert len(calls) == 1 and client.get('/api/history').json() == []


def test_partial_coverage_retains_unknown_and_does_not_overestimate_progress(client, audio_bytes):
    async def assess(exercise, turns):
        result = assessment(exercise, turns)
        result.units[1].status = 'insufficient_evidence'
        result.units[1].evidence = ''
        result.units[1].question = 'What caused the problem?'
        return result
    client.provider.assess = assess
    exercise = begin(client)
    before = client.get('/api/profile').json()['profile']
    _, _, initial = answer(client, exercise, audio_bytes)
    _, _, final = answer(client, exercise, audio_bytes, initial.json()['active_followup'])
    result = final.json()['result']
    assert result['evaluation']['details'] == 1  # Observed units only, never an imputed zero.
    assert result['evaluation']['dimensions']['details'] == 'partially_demonstrated'
    assert result['evaluation']['coverage'] < 1
    assert result['evaluation']['insufficient_evidence']
    assert result['adaptation']['decision'] == 'same'
    after = client.get('/api/profile').json()['profile']
    assert after['listening_profile']['details'] == before['listening_profile']['details']
    assert after['listening_profile']['overall'] == before['listening_profile']['overall']
