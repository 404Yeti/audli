"""Exercise the real OpenAI SDK with mocked HTTP, not a real provider or browser."""
import asyncio
import json
import math
import httpx
import pytest
from openai import AsyncOpenAI
from app.config import Settings
from app.evaluation import expected_units
from app.models import EvaluationJudgments, ExerciseContent, LearnerProfile
from app.services.openai_provider import OpenAIProvider


def response(payload):
    if 'units' in payload and isinstance(payload['units'], list):
        payload = {**payload, 'units': {f"{unit['dimension']}_{unit['index']}":
            {'status': unit['status'], 'evidence': unit['evidence']} for unit in payload['units']}}
    return httpx.Response(200, json={
        'id': 'resp_test', 'object': 'response', 'created_at': 0, 'status': 'completed',
        'model': 'gpt-4.1-mini', 'output': [{'type': 'message', 'id': 'msg_test',
        'role': 'assistant', 'status': 'completed', 'content': [
            {'type': 'output_text', 'text': json.dumps(payload), 'annotations': []}]}],
        'parallel_tool_calls': False, 'tool_choice': 'auto', 'tools': []})


async def run_provider(handler, operation):
    provider = OpenAIProvider(Settings(OPENAI_API_KEY='test-key-not-real', _env_file=None))
    await provider.client.close()
    provider.client = AsyncOpenAI(api_key='test-key-not-real', max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        return await operation(provider)
    finally:
        await provider.client.close()


def test_generation_and_speech_use_adapted_difficulty(exercise):
    profile = LearnerProfile(name='Maya', goal='Understand incident handovers',
        interests=['astronomy'], target_situations=['technical discussions'])
    profile.difficulty.speech_rate = .8
    adapted = exercise.model_copy(update={'speech_rate': .8})
    requests = []
    def handler(request):
        data = json.loads(request.content)
        requests.append((request.url.path, data))
        if request.url.path.endswith('/responses'):
            supplied = json.loads(data['input'][1]['content'])
            assert supplied['profile']['difficulty'] == profile.difficulty.model_dump()
            assert supplied['profile']['goal'] == profile.goal
            assert supplied['profile']['interests'] == profile.interests
            assert supplied['profile']['target_situations'] == profile.target_situations
            assert 'goal, interests and target listening situations' in data['input'][0]['content']
            assert supplied['approximate_word_count'] == 90
            assert data['store'] is False
            assert data['text']['format']['strict'] is True
            return response(adapted.model_dump())
        assert request.url.path.endswith('/audio/speech')
        assert data['speed'] == .8
        assert data['input'] == adapted.script
        assert data['response_format'] == 'mp3'
        return httpx.Response(200, content=b'mocked-audio', headers={'content-type': 'audio/mpeg'})
    async def operation(provider):
        generated = await provider.generate(profile)
        assert isinstance(generated, ExerciseContent)
        assert generated.speech_rate == .8
        assert await provider.speech(generated) == b'mocked-audio'
    asyncio.run(run_provider(handler, operation))
    assert len(requests) == 2


@pytest.mark.parametrize('change', [
    {'speech_rate': .9}, {'target_duration_seconds': 90}, {'vocabulary_level': 'C1'},
    {'information_density': 2}, {'important_details': ['one', 'two', 'three', 'four']},
    {'script': 'word ' * 500},
])
def test_generation_rejects_difficulty_or_content_mismatch(exercise, change):
    def handler(request):
        return response({**exercise.model_dump(), **change})
    async def operation(provider):
        with pytest.raises(ValueError):
            await provider.generate(LearnerProfile())
    asyncio.run(run_provider(handler, operation))


def test_structured_evaluator_preserves_expected_units_and_learner_text(exercise):
    transcript = 'Team update fail, database field missing, go back old app.'
    judgments = EvaluationJudgments(units=[{'dimension': dimension, 'index': i,
        'status': 'missed', 'evidence': ''} for dimension, units in expected_units(exercise).items() for i in range(len(units))],
        feedback='Listen again for the cause.', transcription_concern=False, concern_reason='')
    def handler(request):
        data = json.loads(request.content)
        supplied = json.loads(data['input'][1]['content'])
        assert supplied['script'] == exercise.script
        assert supplied['expected_units'] == expected_units(exercise)
        assert supplied['learner_transcript'] == transcript
        assert data['text']['format']['strict'] is True
        return response(judgments.model_dump())
    async def operation(provider):
        actual = await provider.evaluate(exercise, transcript)
        assert isinstance(actual, EvaluationJudgments)
        assert actual == judgments
    asyncio.run(run_provider(handler, operation))


@pytest.mark.parametrize('logprobs,uncertain', [
    ([{'token': 'team', 'logprob': -.01, 'bytes': [116]}], False),
    ([{'token': 'team', 'logprob': -2, 'bytes': [116]}], True),
    (None, True),
])
def test_transcription_sdk_uncertainty(logprobs, uncertain):
    def handler(request):
        assert request.url.path.endswith('/audio/transcriptions')
        assert b'logprobs' in request.content
        assert b'response.webm' in request.content
        assert b'ORIGINAL HIDDEN SCRIPT' not in request.content
        return httpx.Response(200, json={'text': 'Team rolled back the update.', 'logprobs': logprobs})
    async def operation(provider):
        result = await provider.transcribe(b'mocked learner audio', 'response.webm')
        assert bool(result.uncertainty) == uncertain
        assert result.text == 'Team rolled back the update.'
        expected = math.exp(logprobs[0]['logprob']) if logprobs else None
        assert result.confidence == expected
    asyncio.run(run_provider(handler, operation))


def test_structured_refusal_is_retryable_failure():
    def handler(request):
        payload = response({}).json()
        payload['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'Cannot comply.'}]
        return httpx.Response(200, json=payload)
    async def operation(provider):
        with pytest.raises(ValueError):
            await provider.generate(LearnerProfile())
    asyncio.run(run_provider(handler, operation))


def test_sdk_backed_api_slice_persists_audit_and_regenerates_with_new_speed(tmp_path, exercise, audio_bytes):
    """Real SDK + real repository/audio decoding; HTTP AI results are synthetic."""
    from app.main import create_app
    from app.repository import ProgressRepository
    transcript = ('The team fixed the update by rolling it back. A database migration removed a needed field. '
        'Customers could not save appointments. Maya delayed the release until Friday and will test first to prevent another failure.')
    generated_difficulties, speech_speeds = [], []
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/audio/transcriptions'):
            return httpx.Response(200, json={'text': transcript, 'logprobs': [{'token': 'team', 'logprob': -.01, 'bytes': [116]}]})
        data = json.loads(request.content)
        if request.url.path.endswith('/audio/speech'):
            speech_speeds.append(data['speed'])
            return httpx.Response(200, content=audio_bytes, headers={'content-type': 'audio/mpeg'})
        supplied = json.loads(data['input'][1]['content'])
        if 'profile' in supplied:
            difficulty = supplied['profile']['difficulty']
            generated_difficulties.append(difficulty)
            return response({**exercise.model_dump(), 'speech_rate': difficulty['speech_rate']})
        assert supplied['learner_transcript'] == transcript
        return response({'units': [{'dimension': dimension, 'index': i, 'status': 'understood', 'evidence': supplied['evidence_options'][0]}
            for dimension, units in supplied['expected_units'].items() for i in range(len(units))],
            'feedback': 'You caught the cause and solution.', 'transcription_concern': False, 'concern_reason': ''})
    async def operation(provider):
        settings = Settings(data_dir=tmp_path, _env_file=None)
        app = create_app(settings, provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            first = await client.post('/api/exercises')
            assert first.status_code == 200, first.text
            first = first.json()
            assert set(first) == {'id', 'difficulty', 'audio_url', 'completed_attempt_id', 'topic'}
            assert (await client.get(first['audio_url'])).status_code == 200
            gate = f"/api/exercises/{first['id']}/transcript"
            assert (await client.get(gate)).status_code == 403
            attempt = await client.post(f"/api/exercises/{first['id']}/attempts", files={'audio': ('response.wav', audio_bytes, 'audio/wav')})
            assert attempt.status_code == 200, attempt.text
            attempt = attempt.json()
            assert (await client.get(gate)).status_code == 403
            result = await client.post(f"/api/attempts/{attempt['id']}/evaluate", json={'text': attempt['transcription']['text'], 'confirmed': True})
            assert result.status_code == 200, result.text
            audit = result.json()['adaptation']
            assert {'previous_score', 'changed_variable', 'old_value', 'new_value', 'reason'} <= audit.keys()
            assert audit['previous_score'] == 1
            assert audit['changed_variable'] == 'speech_rate'
            assert audit['old_value'] == .75 and audit['new_value'] == .8 and audit['reason']
            changed = [key for key, value in audit['old_difficulty'].items() if value != audit['new_difficulty'][key]]
            assert changed == ['speech_rate']
            assert (await client.get(gate)).json()['script'] == exercise.script
            persisted = ProgressRepository(settings.data_dir/'audli.sqlite3')
            assert persisted.result(attempt['id']) == result.json()
            next_exercise = await client.post('/api/exercises')
            assert next_exercise.status_code == 200, next_exercise.text
            next_exercise = next_exercise.json()
            assert next_exercise['difficulty'] == audit['new_difficulty']
            assert (await client.get(f"/api/exercises/{next_exercise['id']}/transcript")).status_code == 403
            stored = json.loads(persisted.exercise(next_exercise['id'])['content'])
            assert stored['speech_rate'] == .8
            assert stored['target_duration_seconds'] == audit['new_difficulty']['duration_seconds']
            assert stored['vocabulary_level'] == audit['new_difficulty']['vocabulary_level']
            assert stored['information_density'] == audit['new_difficulty']['information_density']
    asyncio.run(run_provider(handler, operation))
    assert generated_difficulties[1] == {**generated_difficulties[0], 'speech_rate': .8}
    assert speech_speeds == [.75, .8]
    assert len(calls) == 6  # two generation, two speech, one STT, one evaluation


@pytest.mark.parametrize('density,minimum,maximum', [(1,3,3), (2,4,5), (3,6,8)])
def test_density_constraints_are_sent_to_openai_not_just_prompted(exercise, density, minimum, maximum):
    profile = LearnerProfile()
    profile.difficulty.information_density = density
    details = [f'Grounded detail {i}' for i in range(minimum)]
    def handler(request):
        data = json.loads(request.content)
        schema = data['text']['format']['schema']
        array = schema['properties']['important_details']
        assert array['minItems'] == minimum
        assert array['maxItems'] == maximum
        assert schema['additionalProperties'] is False
        return response({**exercise.model_dump(), 'information_density': density, 'important_details': details})
    async def check(provider):
        generated = await provider.generate(profile)
        assert len(generated.important_details) == minimum
    asyncio.run(run_provider(handler, check))


def test_live_four_details_failure_is_rejected_by_density_one_schema(exercise):
    from pydantic import ValidationError
    from app.services.openai_provider import exercise_schema
    # Exact reproduced failure: globally-valid exercise with four details at density 1.
    payload = {**exercise.model_dump(), 'important_details': [*exercise.important_details, 'One extra detail.']}
    assert ExerciseContent.model_validate(payload)  # Old provider contract admitted it.
    with pytest.raises(ValidationError) as exc:
        exercise_schema(1).model_validate(payload)
    assert exc.value.errors()[0]['loc'] == ('important_details',)
    assert exc.value.errors()[0]['type'] == 'too_long'


def test_live_evidence_failure_rejected_by_provider_schema(exercise):
    from pydantic import ValidationError
    from app.evaluation import score_judgments
    from app.services.openai_provider import evaluation_schema
    transcript = 'Team update fail. Go back old app.'
    units = [{'dimension': dim, 'index': index, 'status': 'missed', 'evidence': ''}
             for dim, expected in expected_units(exercise).items() for index in range(len(expected))]
    units[0].update(status='understood', evidence='The team rolled back the failed deployment.')
    old = EvaluationJudgments(units=units, feedback='You caught the main idea.',
                             transcription_concern=False, concern_reason='')
    # Exact live failure mechanism: valid old structured output with a fabricated quote.
    with pytest.raises(ValueError, match='Evaluation cited evidence absent from learner transcript'):
        score_judgments(exercise, old, transcript)
    external = response(old.model_dump()).json()
    payload = json.loads(external['output'][0]['content'][0]['text'])
    with pytest.raises(ValidationError):
        evaluation_schema(exercise, transcript).model_validate(payload)
    def handler(request):
        return response(old.model_dump())
    async def check(provider):
        with pytest.raises(ValidationError):
            await provider.evaluate(exercise, transcript)
    asyncio.run(run_provider(handler, check))


def test_evaluator_contract_enforces_coverage_and_verbatim_evidence(exercise):
    from app.services.openai_provider import evaluation_schema, evidence_options
    from pydantic import ValidationError
    transcript = 'Team update fail. Go back old app.'
    schema_type = evaluation_schema(exercise, transcript)
    slots = {f'{dim}_{i}': {'status': 'missed', 'evidence': ''}
             for dim, units in expected_units(exercise).items() for i in range(len(units))}
    payload = {'units': slots, 'feedback': 'Listen for the cause.', 'transcription_concern': False, 'concern_reason': ''}
    for status in ('understood', 'partial', 'misunderstood'):
        accepted = {**payload, 'units': {**slots, 'main_idea_0': {'status': status, 'evidence': evidence_options(transcript)[0]}}}
        parsed = schema_type.model_validate(accepted)
        assert parsed.units.main_idea_0.evidence in transcript
    invalid = [
        {key: value for key, value in slots.items() if key != 'inference_0'},
        {**slots, 'inference_1': {'status': 'missed', 'evidence': ''}},
        {**slots, 'main_idea_0': {'status': 'understood', 'evidence': ''}},
        {**slots, 'main_idea_0': {'status': 'missed', 'evidence': 'invented'}},
    ]
    for replacement in invalid:
        with pytest.raises(ValidationError):
            schema_type.model_validate({**payload, 'units': replacement})
    def handler(request):
        data = json.loads(request.content)
        schema = data['text']['format']['schema']
        supplied = json.loads(data['input'][1]['content'])
        assert supplied['evidence_options'] == evidence_options(transcript)
        definitions = schema['$defs']
        assert set(definitions['ExpectedUnitJudgments']['required']) == set(slots)
        assert definitions['ExpectedUnitJudgments']['additionalProperties'] is False
        assert definitions['GroundedJudgment']['properties']['evidence']['enum'] == evidence_options(transcript)
        assert definitions['MissedJudgment']['properties']['evidence']['const'] == ''
        return response(payload)
    async def check(provider):
        judgments = await provider.evaluate(exercise, transcript)
        from app.evaluation import score_judgments
        assert score_judgments(exercise, judgments, transcript).overall == 0
    asyncio.run(run_provider(handler, check))


def test_verbatim_excerpt_options_preserve_long_and_irregular_text():
    from app.services.openai_provider import evidence_options
    transcript = '  Broken  grammar\nStill understand! ' + 'word ' * 450
    excerpts = evidence_options(transcript)
    assert all(0 < len(excerpt) <= 1000 and excerpt in transcript for excerpt in excerpts)


def test_evidence_options_bound_schema_size_and_exclude_unusable_punctuation():
    from app.services.openai_provider import evidence_options
    from app.evaluation import score_judgments
    from app.models import EvaluationJudgments
    from app.services.demo import DemoProvider
    transcript = '\n'.join(f'word {i}.' for i in range(500)) + '\n!!!'
    options = evidence_options(transcript)
    assert len(options) <= 80
    assert all(0 < len(option) <= 1000 and option in transcript for option in options)
    assert evidence_options('Useful words here.\n!!!') == ['Useful words here.']


@pytest.mark.parametrize('first_count,reason', [(50, 'too_short'), (54, 'too_short'), (118, 'too_long')])
def test_script_length_repair_regenerates_once_and_preserves_prose(exercise, caplog, first_count, reason):
    import logging
    requests = []
    valid_script = 'original-valid-word ' * 84
    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        supplied = json.loads(data['input'][1]['content'])
        assert supplied['approximate_word_count'] == 84
        assert supplied['required_word_count_range'] == {'minimum': 55, 'maximum': 117}
        assert supplied['preferred_word_count_range'] == {'minimum': 76, 'maximum': 92}
        assert supplied['suggested_sentence_count'] == 7
        if len(requests) == 1:
            assert 'generation_repair' not in supplied
            script = 'private-generated-word ' * first_count
        else:
            repair = supplied['generation_repair']
            assert repair['reason'] == reason and repair['previous_actual_words'] == first_count
            assert repair['word_adjustment_to_target'] == 84 - first_count
            assert repair['previous_exercise']['script'] == 'private-generated-word ' * first_count
            assert repair['previous_exercise']['important_details'] == exercise.important_details
            assert 'Revise the supplied previous_exercise' in data['input'][0]['content']
            assert ('too short' if reason == 'too_short' else 'too long') in data['input'][0]['content']
            assert '55–117 words' in data['input'][0]['content']
            script = valid_script
        return response({**exercise.model_dump(), 'script': script})
    async def check(provider):
        result = await provider.generate(LearnerProfile())
        assert result.script == valid_script
    with caplog.at_level(logging.INFO, logger='audli'):
        asyncio.run(run_provider(handler, check))
    assert len(requests) == 2
    for metadata in ('generation_attempt=1', 'generation_attempt=2', 'target_words=84',
                     'allowed_minimum=54.60', 'allowed_maximum=117.60',
                     f'actual_words={first_count}', f'retry_reason={reason}', 'retry_scheduled=True'):
        assert metadata in caplog.text
    assert 'private-generated-word' not in caplog.text and 'original-valid-word' not in caplog.text


def test_valid_script_never_triggers_repair(exercise):
    calls = []
    def handler(request):
        calls.append(request)
        return response(exercise.model_dump())
    async def check(provider):
        result = await provider.generate(LearnerProfile())
        assert result.script == exercise.script
    asyncio.run(run_provider(handler, check))
    assert len(calls) == 1


@pytest.mark.parametrize('counts', [(51, 79), (50, 44)])
def test_observed_79_word_target_repairs_draft_or_fails_bounded(exercise, counts):
    calls = []
    profile = LearnerProfile()
    profile.difficulty.speech_rate = .7  # 45 seconds at 105 words/minute => 79 words.
    def handler(request):
        supplied = json.loads(json.loads(request.content)['input'][1]['content'])
        calls.append(supplied)
        assert supplied['approximate_word_count'] == 79
        assert supplied['required_word_count_range'] == {'minimum': 52, 'maximum': 110}
        assert 'phase' not in supplied and 'remaining_seconds' not in supplied
        return response({**exercise.model_dump(), 'speech_rate': .7,
                         'script': 'original-word ' * counts[len(calls)-1]})
    async def check(provider):
        if counts[1] == 79:
            assert len((await provider.generate(profile)).script.split()) == 79
        else:
            with pytest.raises(ValueError, match='actual_words=44'):
                await provider.generate(profile)
    asyncio.run(run_provider(handler, check))
    assert len(calls) == 2
    assert calls[1]['generation_repair']['word_adjustment_to_target'] == 79 - counts[0]


@pytest.mark.parametrize('counts', [(50, 50), (118, 118), (50, 118)])
def test_script_length_repair_exhaustion_preserves_existing_error(exercise, counts):
    calls = []
    def handler(request):
        calls.append(request)
        return response({**exercise.model_dump(), 'script': 'original-word ' * counts[len(calls)-1]})
    async def check(provider):
        with pytest.raises(ValueError, match=f'Generated script length outside target tolerance: target_words=84, '
                           f'allowed_words=54.60..117.60, actual_words={counts[1]}'):
            await provider.generate(LearnerProfile())
    asyncio.run(run_provider(handler, check))
    assert len(calls) == 2


@pytest.mark.parametrize('change', [
    {'speech_rate': .8}, {'target_duration_seconds': 60}, {'vocabulary_level': 'B2'},
    {'information_density': 2}, {'important_details': ['one', 'two', 'three', 'four']},
    {'script': 'tiny'},
])
def test_unrelated_validation_failure_does_not_trigger_length_repair(exercise, change):
    calls = []
    def handler(request):
        calls.append(request)
        return response({**exercise.model_dump(), 'script': 'original-word ' * 50, **change})
    async def check(provider):
        with pytest.raises(ValueError):
            await provider.generate(LearnerProfile())
    asyncio.run(run_provider(handler, check))
    assert len(calls) == 1


def test_repaired_exercise_still_must_pass_unrelated_validation(exercise):
    calls = []
    def handler(request):
        calls.append(request)
        changes = {'script': 'original-word ' * 50} if len(calls) == 1 else {'speech_rate': .8}
        return response({**exercise.model_dump(), **changes})
    async def check(provider):
        with pytest.raises(ValueError, match='Generated exercise difficulty mismatch'):
            await provider.generate(LearnerProfile())
    asyncio.run(run_provider(handler, check))
    assert len(calls) == 2


@pytest.mark.parametrize('word_count', [55, 117])
def test_script_length_tolerance_keeps_existing_integer_boundaries(exercise, word_count):
    calls = []
    def handler(request):
        calls.append(request)
        return response({**exercise.model_dump(), 'script': 'word ' * word_count})
    async def check(provider):
        assert len((await provider.generate(LearnerProfile())).script.split()) == word_count
    asyncio.run(run_provider(handler, check))
    assert len(calls) == 1


def test_exhausted_script_repair_returns_503_without_changing_completed_progress(client, audio_bytes, exercise):
    from app.main import create_app
    previous = client.post('/api/exercises').json()
    attempt = client.post(f"/api/exercises/{previous['id']}/attempts",
        files={'audio': ('response.wav', audio_bytes, 'audio/wav')}).json()
    assert client.post(f"/api/attempts/{attempt['id']}/evaluate",
        json={'text': attempt['transcription']['text'], 'confirmed': True}).status_code == 200
    repo = client.app.state.repository
    profile_before, history_before = repo.profile(), repo.history()
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path.endswith('/responses')  # No speech request for rejected prose.
        d = profile_before.difficulty
        return response({**exercise.model_dump(), 'script': 'original-word ' * 50,
                         'speech_rate': d.speech_rate, 'target_duration_seconds': d.duration_seconds,
                         'vocabulary_level': d.vocabulary_level, 'information_density': d.information_density})
    async def check(provider):
        app = create_app(Settings(data_dir=repo.path.parent, _env_file=None), provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as api_client:
            result = await api_client.post('/api/exercises')
            assert result.status_code == 503
            assert repo.profile() == profile_before and repo.history() == history_before
            assert repo.current_exercise()['id'] == previous['id']
            assert repo.completed(previous['id']) == attempt['id']
    asyncio.run(run_provider(handler, check))
    assert len(calls) == 2


@pytest.mark.parametrize('status', ['demonstrated', 'partially_demonstrated', 'misunderstood', 'insufficient_evidence'])
def test_v2_sdk_schema_grounded_evidence_and_unknown_are_distinct(exercise, status):
    from app.models import ConversationTurn
    from app.conversation import final_evaluation, validate_evidence
    transcript = 'They went back to the previous version and waited until Friday.'
    def handler(request):
        data = json.loads(request.content)
        supplied = json.loads(data['input'][1]['content'])
        assert supplied['turns'][0]['learner_response'] == transcript
        assert 'semantic paraphrases' in data['input'][0]['content']
        assert 'INSUFFICIENT_EVIDENCE' in data['input'][0]['content']
        schema = data['text']['format']['schema']
        assert schema['$defs']['UnknownEvidence']['properties']['evidence']['const'] == ''
        slots = {f'{dim}_{i}': {'status': 'demonstrated', 'evidence': transcript, 'question': ''}
                 for dim, units in expected_units(exercise).items() for i in range(len(units))}
        slots['inference_0'] = {'status': status, 'evidence': '' if status == 'insufficient_evidence' else transcript,
                                'question': 'Why do you think Maya chose to wait?' if status == 'insufficient_evidence' else ''}
        return response({'units': slots, 'feedback': 'You caught the main idea.',
                         'transcription_concern': False, 'concern_reason': ''})
    async def check(provider):
        result = await provider.assess(exercise, [ConversationTurn(attempt_id='initial', text=transcript)])
        validate_evidence(exercise, result, [transcript])
        final = final_evaluation(exercise, result)
        assert final.inference == {'demonstrated': 1, 'partially_demonstrated': .5,
                                   'misunderstood': 0, 'insufficient_evidence': None}[status]
    asyncio.run(run_provider(handler, check))


def test_v2_vocabulary_paraphrase_and_followup_turns_use_learner_evidence_only(exercise):
    from app.models import ConversationTurn, Followup
    from app.conversation import final_evaluation, validate_evidence
    phone = ExerciseContent.model_validate({**exercise.model_dump(), 'script': 'Maya bought a refurbished phone. It was used but repaired and tested. '
        'She chose it because it cost less, and she needed a dependable phone for work. She kept her old phone as a backup.',
        'main_idea': 'Maya chose a repaired used phone for work and kept a spare.',
        'important_details': ['The phone had been repaired and tested.', 'It cost less.', 'She kept her old phone.'],
        'inference_points': ['Keeping a spare reduces the risk of being unreachable.'],
        'vocabulary_items': [{'phrase': 'refurbished', 'meaning_in_context': 'used but repaired and tested'},
                             {'phrase': 'backup', 'meaning_in_context': 'a spare phone'}]})
    summary = 'She bought a used phone that had been fixed. It cost less, for work, and she kept a spare phone.'
    clarification = 'So she could still be reached if the new phone stopped working.'
    question = Followup(id='question', dimension='inference', index=0, question='Why did she keep her old phone?')
    turns = [ConversationTurn(attempt_id='summary', text=summary),
             ConversationTurn(attempt_id='answer', text=clarification, followup=question)]
    def handler(request):
        data = json.loads(request.content)
        supplied = json.loads(data['input'][1]['content'])
        assert len(supplied['turns']) == 2
        assert supplied['turns'][1]['question'] == question.question
        assert question.question not in supplied['evidence_options']
        slots = {f'{dim}_{i}': {'status': 'demonstrated', 'evidence': clarification if dim == 'inference' else summary,
                              'question': ''} for dim, units in expected_units(phone).items() for i in range(len(units))}
        return response({'units': slots, 'feedback': 'Exactly. You understood that part too.',
                         'transcription_concern': False, 'concern_reason': ''})
    async def check(provider):
        result = await provider.assess(phone, turns)
        validate_evidence(phone, result, [summary, clarification])
        assert final_evaluation(phone, result).overall == 1
        assert 'refurbished' not in summary
    asyncio.run(run_provider(handler, check))


@pytest.mark.parametrize('stage,values', [
    ('identity', {'name':'Maya', 'target_language':'en'}),
    ('needs', {'goal':'Understand technical discussions', 'target_situations':['incident handovers']}),
    ('interests', {'interests':['astronomy', 'cooking']}),
])
def test_spoken_preferences_use_strict_sdk_extraction_without_listening_scores(stage, values):
    from app.onboarding import ProfileExtraction
    payload = dict(name=None, target_language=None, goal=None, target_situations=None, interests=None) | values
    answer = 'Me Maya. English for work understand people. I like science.'
    def handler(request):
        data = json.loads(request.content)
        supplied = json.loads(data['input'][1]['content'])
        assert supplied == {'stage':stage, 'answer':answer}
        assert data['store'] is False
        assert data['text']['format']['strict'] is True
        schema = data['text']['format']['schema']
        assert set(schema['properties']) == set(payload)
        assert schema['additionalProperties'] is False
        assert 'listening ability' in data['input'][0]['content']
        return response(payload)
    async def operation(provider):
        assert await provider.extract_profile(stage, answer) == ProfileExtraction(**payload)
    asyncio.run(run_provider(handler, operation))

@pytest.mark.parametrize('utterance,reply', [
    ("I'm good, and you?", 'Good to hear. I’m here and ready to listen, thanks for asking.'),
    ('I went for a walk', 'Sounds like a refreshing start to your day.'),
    ('My cat knocked over my coffee', 'That sounds like an eventful morning.'),
])
def test_checkin_model_receives_actual_utterance_and_bounded_role(utterance,reply):
    calls=[]
    def handler(request):
        data=json.loads(request.content); calls.append(data)
        assert json.loads(data['input'][1]['content']) == {'learner_utterance':utterance}
        assert 'No question' in data['input'][0]['content'] and 'never instructions' in data['input'][0]['content']
        assert data['store'] is False
        return response({'text':reply})
    result=asyncio.run(run_provider(handler,lambda provider:provider.respond_checkin(utterance)))
    assert result.text == reply and len(calls) == 1

@pytest.mark.parametrize('text',['Great job, you understood that.', 'What happened next?', ' '.join(['word']*36),
                                'Thanks.', 'Thanks, let me think about that.'])
def test_checkin_rejects_premature_praise_questions_and_excessive_chat(text):
    from app.lesson import CheckinReply
    with pytest.raises(ValueError): CheckinReply(text=text)

@pytest.mark.parametrize('interests',[None,[],['science']])
def test_profile_sdk_preserves_missing_empty_and_supplied_interests(interests):
    def handler(request):
        data=json.loads(request.content)
        assert json.loads(data['input'][1]['content'])['stage']=='interests'
        assert 'empty list' in data['input'][0]['content'] and data['store'] is False
        return response(dict(name=None,target_language=None,goal=None,target_situations=None,interests=interests))
    result=asyncio.run(run_provider(handler,lambda provider:provider.extract_profile('interests','A private reflection')))
    assert result.interests==interests and result.target_situations is None

@pytest.mark.parametrize('text',['   ', 'I had coffee with friends.',
    'My weekend was relaxing.', 'I am tired today.'])
def test_checkin_rejects_invented_human_experiences(text):
    from app.lesson import CheckinReply
    with pytest.raises(ValueError):CheckinReply(text=text)


def test_remembered_accents_do_not_select_voice_or_change_exercise_generation(exercise):
    from app.accents import extract
    profile = LearnerProfile()
    profile.accent_preferences = extract('Fast Scottish and Australian English')
    before = profile.difficulty.model_dump()
    def handler(request):
        supplied = json.loads(json.loads(request.content)['input'][1]['content'])
        assert 'accent_preferences' not in supplied['profile']
        assert supplied['profile']['difficulty'] == before
        assert 'Fast Scottish and Australian English' not in json.dumps(supplied)
        return response(exercise.model_dump())
    asyncio.run(run_provider(handler, lambda provider: provider.generate(profile)))
