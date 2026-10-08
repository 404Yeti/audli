"""Terminal-copy regressions: evaluator prose must never become a spoken question."""
import json
from types import SimpleNamespace
import pytest
from app.feedback import final_feedback, validate_final_feedback
from test_conversation import assessment, begin, answer, QUESTIONS


@pytest.mark.parametrize('copy', [
    "Could you tell me what manage means?", "Try to find more specific details.",
    "Try to identify all the apps.", "What does refurbished mean？",
    "Remember to tell me more about the location.", "You understood 85 percent.",
    ' '.join(['word'] * 51), 'Nice. Good. Great. Another sentence.',
])
def test_terminal_copy_rejects_questions_requests_scores_and_long_reports(copy):
    with pytest.raises(ValueError):
        validate_final_feedback(copy)


def test_word_limit_is_exact_and_configurable():
    validate_final_feedback(' '.join(['word'] * 50))
    with pytest.raises(ValueError):
        validate_final_feedback(' '.join(['word'] * 31), 30)


def scripted_report(client, outcomes):
    calls = []
    async def assess(exercise, turns):
        state = outcomes[min(len(calls), len(outcomes)-1)]
        calls.append(turns)
        result = assessment(exercise, turns, state)
        result.feedback = ("Great job understanding the main idea! Could you tell me what manage means? "
                           "Try to find more specific details about all the apps. Keep up the good work!")
        return result
    client.provider.assess = assess
    return calls


@pytest.mark.parametrize('dimension', ['inference', 'vocabulary', 'details'])
def test_unknown_routes_interactively_not_through_terminal_report(client, audio_bytes, dimension):
    scripted_report(client, [{dimension: 'insufficient_evidence'}, {}])
    exercise = begin(client)
    _, _, initial = answer(client, exercise, audio_bytes)
    data = initial.json()
    assert data['state'] == 'AWAITING_FOLLOWUP'
    assert data['active_followup']['dimension'] == dimension
    assert data['prompt'].count('?') == 1
    assert data['result'] is None
    assert client.get(f"/api/exercises/{exercise['id']}/transcript").status_code == 403
    _, _, final = answer(client, exercise, audio_bytes, data['active_followup'])
    data = final.json()
    validate_final_feedback(data['prompt'])
    assert data['prompt'] == data['result']['evaluation']['feedback']
    assert 'Could you' not in data['prompt'] and 'Try to find' not in data['prompt']
    assert data['result']['adaptation']['previous_score'] == 1


@pytest.mark.parametrize('statuses', [{}, {'details': 'misunderstood'},
                                     {dim: 'misunderstood' for dim in QUESTIONS}])
def test_strong_or_confirmed_wrong_feedback_is_short_and_terminal(client, audio_bytes, statuses):
    scripted_report(client, [statuses])
    exercise = begin(client)
    _, _, response = answer(client, exercise, audio_bytes)
    data = response.json()
    assert response.status_code == 200
    assert data['followups_asked'] == 0
    validate_final_feedback(data['prompt'])
    if statuses:
        assert data['result']['evaluation']['misunderstood']
        assert 'One correction:' in data['prompt']
        assert not data['result']['evaluation']['insufficient_evidence']
    else:
        assert 'important details' in data['prompt']
    spoken = []
    async def speak(text, speech_rate=.9):
        spoken.append(text)
        return audio_bytes
    client.provider.speak = speak
    assert client.post(f"/api/exercises/{exercise['id']}/coach-audio", json={'cue_id': 'feedback'}).status_code == 200
    assert spoken == [data['prompt']]


def test_budget_exhaustion_preserves_unknown_without_terminal_third_question(client, audio_bytes):
    calls = scripted_report(client, [{dim: 'insufficient_evidence' for dim in QUESTIONS}])
    exercise = begin(client)
    before = client.get('/api/profile').json()['profile']['difficulty']
    _, _, response = answer(client, exercise, audio_bytes)
    for _ in range(2):
        assert response.json()['state'] == 'AWAITING_FOLLOWUP'
        _, _, response = answer(client, exercise, audio_bytes, response.json()['active_followup'])
    data = response.json()
    assert len(calls) == 3 and data['followups_asked'] == 2
    assert data['active_followup'] is None
    assert data['result']['evaluation']['insufficient_evidence']
    assert not data['result']['evaluation']['misunderstood']
    assert data['result']['adaptation']['new_difficulty'] == before
    validate_final_feedback(data['prompt'])
    assert 'challenging' not in data['prompt']  # Unknown is not confirmed poor comprehension.


@pytest.mark.parametrize('label', ['What does manage mean?', 'Try to find the correct app.',
                                 ' '.join(['meaning'] * 70), 'First. Second. Third.'])
def test_unsafe_or_oversized_correction_uses_complete_safe_message(label):
    evaluation = SimpleNamespace(dimensions={'main_idea': 'demonstrated'},
        insufficient_evidence=[], misunderstood=[label], understood=['Main idea'])
    event = SimpleNamespace(changed_variable=None, decision='same')
    text = final_feedback(evaluation, event, 30)
    validate_final_feedback(text, 30)
    assert label not in text
    assert 'misunderstanding' in text


def test_old_feedback_audio_cache_is_not_reused(client, audio_bytes):
    scripted_report(client, [{}])
    exercise = begin(client)
    _, _, completed = answer(client, exercise, audio_bytes)
    repo = client.app.state.repository
    before = client.get('/api/profile').json()
    historical = completed.json()['result']['evaluation']
    historical['feedback'] = 'Could you tell me what manage means? Try to find more details.'
    with repo.connect() as db:
        db.execute('UPDATE evaluation_results SET data=? WHERE attempt_id=?',
                   (json.dumps(historical), repo.completed(exercise['id'])))
    resumed = client.get(f"/api/exercises/{exercise['id']}/conversation").json()
    assert resumed['prompt'] == resumed['result']['evaluation']['feedback'] == completed.json()['prompt']
    assert client.get('/api/profile').json() == before
    old = 'old-feedback.wav'
    (repo.path.parent / 'exercise_audio' / old).write_bytes(audio_bytes)
    repo.save_coach_audio(exercise['id'], 'feedback', old)
    calls = []
    async def speak(text, speech_rate=.9):
        calls.append(text)
        return audio_bytes
    client.provider.speak = speak
    prefix = f"/api/exercises/{exercise['id']}"
    assert client.get(prefix+'/coach-audio/feedback').status_code == 404
    assert client.post(prefix+'/coach-audio', json={'cue_id': 'feedback'}).status_code == 200
    assert calls == [completed.json()['prompt']]
    assert client.get(prefix+'/coach-audio/feedback').status_code == 200


def test_unasked_unknown_detail_can_use_remaining_followup_budget(client, audio_bytes):
    async def assess(exercise, turns):
        result = assessment(exercise, turns)
        for unit in result.units:
            if unit.dimension == 'details' and unit.index in (0, 1):
                unit.status = 'insufficient_evidence'
                unit.evidence = ''
                unit.question = 'What happened after the update?'
        return result
    client.provider.assess = assess
    exercise = begin(client)
    _, _, first = answer(client, exercise, audio_bytes)
    assert first.json()['active_followup']['index'] == 0
    _, _, second = answer(client, exercise, audio_bytes, first.json()['active_followup'])
    assert second.json()['state'] == 'AWAITING_FOLLOWUP'
    assert second.json()['active_followup']['index'] == 1
    _, _, final = answer(client, exercise, audio_bytes, second.json()['active_followup'])
    assert final.json()['followups_asked'] == 2
    assert final.json()['result']['adaptation']['decision'] == 'same'
    validate_final_feedback(final.json()['prompt'])

@pytest.mark.parametrize('label,tip',[
    ('The delivery was postponed until Friday','changed plan'),
    ('The discount reduced the final cost','original amount'),
    ('The clay was shaped before it was fired','order of events'),
])
def test_specific_coaching_changes_and_deduplicates_advice(label,tip):
    evaluation=SimpleNamespace(dimensions={'main_idea':'demonstrated','details':'misunderstood'},
        insufficient_evidence=[],misunderstood=[label],understood=['Main idea'])
    event=SimpleNamespace(focus='details')
    first=final_feedback(evaluation,event)
    assert label in first and tip in first and 'dates, times' not in first
    from app.lesson import evidence_tip
    repeated=final_feedback(evaluation,event,previous_advice=[evidence_tip(label,'details')])
    assert label in repeated and tip not in repeated
    validate_final_feedback(first);validate_final_feedback(repeated)


def test_partial_evidence_is_not_described_as_a_confirmed_misunderstanding(exercise):
    from app.conversation import final_evaluation
    from app.models import ConversationTurn
    evidence=assessment(exercise,[ConversationTurn(attempt_id='a',text='A spoken answer')])
    evidence.units[1].status='partially_demonstrated'
    evaluation=final_evaluation(exercise,evidence)
    text=final_feedback(evaluation,SimpleNamespace(focus='details'),content=exercise)
    assert 'One part to build on' in text and 'One correction' not in text
    assert 'misunderstanding' not in text
    validate_final_feedback(text)
