"""Audli 2.0 AI model layer: per-task model configuration, the OpenAI SDK adapters against a mocked HTTP transport, malformed and failing
providers, retries through the real job queue, and the guarantee that no retiring model is a 2.0 default.

No network, no key, no paid call: every OpenAI request here goes to httpx.MockTransport. Mock tests establish request shape and handling,
not model accuracy or that the real service accepts these parameters (that is what the approved live matrix is for).
"""
import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.listening import schema as l
from app.listening.domain import ground_assessment
from app.listening.models import AUDIO_PRICES, RETIRING, TEXT_PRICES, AssessmentModel, ModelPlan, TranscriptionModel, audio_cost, text_cost
from app.listening.providers import (AssessmentInput, OpenAIAssessor, OpenAITranscriber, ProviderPermanent, ProviderTemporary, Transcript,
                                     build_providers, make_client, parse_verdicts, verdict_schema)
from app.listening.worker import run_once
from app.storage.repository import utcnow
from test_listening import make_repository  # noqa: F401  (fixture)
from test_listening_journey import PEOPLE_WORDS, by_kind, journey, refresh, start_pending, stored  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]
POINTS = [{'id': 'first', 'text': 'Maria first wanted the mushroom pasta'}, {'id': 'reason', 'text': 'The restaurant had run out of mushrooms'},
          {'id': 'final', 'text': 'She ordered the chicken instead'}]
REQUEST = AssessmentInput('Ordering food', ['Sam: Good evening!'], 'What happened?', POINTS, 'she wanted pasta but they had no mushrooms so chicken')
GOOD = [{'id': 'first', 'state': 'covered', 'quote': 'she wanted pasta'}, {'id': 'reason', 'state': 'covered', 'quote': 'they had no mushrooms'},
        {'id': 'final', 'state': 'partly', 'quote': 'so chicken'}]


def client(handler):
    return make_client('sk-test-not-real', 10, httpx.Client(transport=httpx.MockTransport(handler)))


def responses_body(text, status='completed', refusal=None):
    content = [{'type': 'refusal', 'refusal': refusal}] if refusal else [{'type': 'output_text', 'text': text, 'annotations': []}]
    return {'id': 'resp_1', 'object': 'response', 'created_at': 0, 'status': status, 'model': 'gpt-6-luna', 'parallel_tool_calls': False, 'tool_choice': 'auto',
            'tools': [], 'output': [{'type': 'message', 'id': 'msg_1', 'role': 'assistant', 'status': 'completed', 'content': content}]}


def chat_body(text, finish='stop', refusal=None):
    return {'id': 'chatcmpl_1', 'object': 'chat.completion', 'created': 0, 'model': 'gpt-4.1-mini',
            'choices': [{'index': 0, 'finish_reason': finish, 'message': {'role': 'assistant', 'content': text, 'refusal': refusal}}]}


def error_body(status, code=None, message='x'):
    return {'error': {'message': message, 'type': 'error', 'param': None, 'code': code}}


def assessor(handler, **plan):
    return OpenAIAssessor(client(handler), AssessmentModel(**{'model': 'gpt-6-luna', 'api': 'responses', 'reasoning': '', 'timeout': 10, **plan}))


def transcriber(handler, model='gpt-transcribe'):
    return OpenAITranscriber(client(handler), TranscriptionModel(model, 10))


# ---- configuration -------------------------------------------------------------------------------------------------------------

def test_models_are_chosen_by_task_in_one_place_and_none_of_the_defaults_is_retiring():
    plan = ModelPlan.from_settings(Settings(_env_file=None))
    assert (plan.transcription.model, plan.assessment.model, plan.assessment.api, plan.assessment.reasoning, plan.generation.model) == \
           ('gpt-transcribe', 'gpt-6-luna', 'responses', '', 'gpt-6.1-terra')
    assert plan.retiring() == {}
    assert ModelPlan.from_settings(Settings(listening_transcription_model='gpt-4o-mini-transcribe', listening_assessment_model='gpt-4.1-mini', _env_file=None)
                                   ).retiring() == {'gpt-4o-mini-transcribe': '2027-02-26', 'gpt-4.1-mini': '2027-01-14'}


def test_environment_overrides_every_task_so_rollback_needs_no_code_change(monkeypatch):
    monkeypatch.setenv('AUDLI_LISTENING_ASSESSMENT_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('AUDLI_LISTENING_ASSESSMENT_API', 'chat')
    monkeypatch.setenv('AUDLI_LISTENING_TRANSCRIPTION_MODEL', 'gpt-4o-mini-transcribe')
    monkeypatch.setenv('AUDLI_LISTENING_GENERATION_MODEL', 'gpt-6-luna')
    monkeypatch.setenv('AUDLI_LISTENING_ASSESSMENT_REASONING', 'low')
    plan = ModelPlan.from_settings(Settings(_env_file=None))
    assert (plan.assessment.model, plan.assessment.api, plan.assessment.reasoning, plan.transcription.model, plan.generation.model) == \
           ('gpt-4.1-mini', 'chat', 'low', 'gpt-4o-mini-transcribe', 'gpt-6-luna')
    assert plan.transcription.language_parameter == 'language'
    monkeypatch.setenv('AUDLI_LISTENING_ASSESSMENT_API', 'completions')
    with pytest.raises(Exception):
        Settings(_env_file=None)                                   # an unknown endpoint is a startup error, not a runtime surprise


def test_only_the_model_plan_reads_model_names_from_settings():
    for path in (ROOT / 'app/listening').glob('*.py'):
        if path.name in ('models.py', 'providers.py', 'worker.py'):
            continue
        assert not re.search(r'listening_(transcription|assessment|generation)_model', path.read_text()), path.name


def test_cost_helpers_use_the_published_price_table():
    assert text_cost('gpt-6-luna', 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert audio_cost('gpt-transcribe', 120) == pytest.approx(0.009)
    assert set(RETIRING) >= {'gpt-4o-mini-transcribe', 'gpt-4.1-mini', 'gpt-4o-mini'} and 'gpt-6-luna' in TEXT_PRICES and 'gpt-transcribe' in AUDIO_PRICES


def test_building_openai_providers_needs_a_key_a_guard_and_uses_the_plan():
    from app.listening.ai_budget import GuardedAssessor, GuardedTranscriber
    guard = object()
    with pytest.raises(RuntimeError, match='OPENAI_API_KEY is required'):
        build_providers(Settings(listening_provider='openai', _env_file=None), guard)
    for blank in ('', '   '):                                  # CI and some hosts export the variable as an empty string
        with pytest.raises(RuntimeError, match='OPENAI_API_KEY is required'):
            build_providers(Settings(listening_provider='openai', OPENAI_API_KEY=blank, _env_file=None), guard)
    settings = Settings(listening_provider='openai', OPENAI_API_KEY='sk-test-not-real', _env_file=None)
    with pytest.raises(RuntimeError, match='spending guard'):
        build_providers(settings)
    heard, judge = build_providers(settings, guard)
    assert isinstance(heard, GuardedTranscriber) and isinstance(judge, GuardedAssessor)
    assert isinstance(heard.inner, OpenAITranscriber) and isinstance(judge.inner, OpenAIAssessor)
    assert (heard.inner.plan.model, judge.inner.plan.model) == ('gpt-transcribe', 'gpt-6-luna')
    assert judge.inner.client.max_retries == 0 and judge.inner.client.timeout == 45


# ---- transcription ---------------------------------------------------------------------------------------------------------------

def test_gpt_transcribe_sends_the_languages_list_and_nothing_retired():
    seen = {}

    def handler(request):
        seen['body'], seen['path'], seen['auth'] = request.content, request.url.path, request.headers['authorization']
        return httpx.Response(200, json={'text': ' hello there ', 'languages': [{'code': 'en'}], 'usage': {'type': 'duration', 'seconds': 3}})
    result = transcriber(handler).transcribe(b'\x00' * 400, 'audio/webm')
    assert result == Transcript('hello there', True) and seen['path'].endswith('/audio/transcriptions') and seen['auth'] == 'Bearer sk-test-not-real'
    body = seen['body']
    assert b'name="model"' in body and b'gpt-transcribe' in body and b'name="languages' in body and b'name="language"' not in body
    assert b'logprobs' not in body and b'filename="answer.webm"' in body and b'name="response_format"' in body


def test_older_transcription_models_get_the_single_language_parameter():
    seen = {}

    def handler(request):
        seen['body'] = request.content
        return httpx.Response(200, json={'text': 'hi'})
    assert transcriber(handler, 'gpt-4o-mini-transcribe').transcribe(b'x' * 300, 'audio/mp4') == Transcript('hi', True)
    assert b'name="language"' in seen['body'] and b'name="languages' not in seen['body'] and b'filename="answer.mp4"' in seen['body']


def test_no_detected_language_means_unreliable_speech_not_a_transcript():
    out = transcriber(lambda r: httpx.Response(200, json={'text': 'Thank you for watching!', 'languages': []})).transcribe(b'x' * 300, 'audio/webm')
    assert out == Transcript('Thank you for watching!', False)            # the worker stores no words for this (see the pipeline test below)


@pytest.mark.parametrize('payload', [{}, {'text': None}, {'text': 5}, {'transcript': 'x'}])
def test_a_transcription_response_without_text_is_a_temporary_fault(payload):
    with pytest.raises(ProviderTemporary):
        transcriber(lambda r: httpx.Response(200, json=payload)).transcribe(b'x' * 300, 'audio/webm')


ERRORS = [(400, None, ProviderPermanent, 'provider_bad_request'), (401, 'invalid_api_key', ProviderPermanent, 'provider_auth'),
          (403, None, ProviderPermanent, 'provider_auth'), (404, 'model_not_found', ProviderPermanent, 'provider_model'),
          (422, None, ProviderPermanent, 'provider_bad_request'), (408, None, ProviderTemporary, 'provider_unavailable'),
          (409, None, ProviderTemporary, 'provider_unavailable'), (429, 'rate_limit_exceeded', ProviderTemporary, 'provider_unavailable'),
          (429, 'insufficient_quota', ProviderPermanent, 'provider_quota'), (500, None, ProviderTemporary, 'provider_unavailable'),
          (502, None, ProviderTemporary, 'provider_unavailable'), (503, None, ProviderTemporary, 'provider_unavailable')]


@pytest.mark.parametrize('status,code,kind,expected', ERRORS)
def test_provider_errors_are_classified_for_both_adapters(status, code, kind, expected):
    def handler(request):
        return httpx.Response(status, json=error_body(status, code))
    for call in (lambda: transcriber(handler).transcribe(b'x' * 300, 'audio/webm'), lambda: assessor(handler).assess(REQUEST),
                 lambda: assessor(handler, api='chat', model='gpt-4.1-mini').assess(REQUEST)):
        with pytest.raises(kind) as caught:
            call()
        assert caught.value.code == expected


@pytest.mark.parametrize('failure', [httpx.ConnectTimeout('slow'), httpx.ReadTimeout('slow'), httpx.ConnectError('down'), httpx.RemoteProtocolError('cut')])
def test_network_failures_are_temporary(failure):
    def handler(request):
        raise failure
    for call in (lambda: transcriber(handler).transcribe(b'x' * 300, 'audio/webm'), lambda: assessor(handler).assess(REQUEST)):
        with pytest.raises(ProviderTemporary):
            call()


def test_the_sdk_never_retries_on_its_own():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json=error_body(503))
    with pytest.raises(ProviderTemporary):
        assessor(handler).assess(REQUEST)
    assert len(calls) == 1                                       # the job queue owns retrying; a hidden retry would resend the learner's words


# ---- assessment ------------------------------------------------------------------------------------------------------------------

def test_responses_request_is_strict_structured_and_stores_nothing():
    seen = {}

    def handler(request):
        seen['path'], seen['data'] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json=responses_body(json.dumps({'points': GOOD})))
    assert assessor(handler).assess(REQUEST) == GOOD
    data = seen['data']
    assert seen['path'].endswith('/responses') and data['model'] == 'gpt-6-luna' and data['store'] is False and data['max_output_tokens'] == 700
    assert 'temperature' not in data and 'reasoning' not in data                 # unverified parameters are not sent unless configured
    fmt = data['text']['format']
    assert (fmt['type'], fmt['name'], fmt['strict']) == ('json_schema', 'key_point_verdicts', True)
    schema = fmt['schema']
    entry = schema['properties']['points']['items']
    assert schema['additionalProperties'] is False and entry['additionalProperties'] is False and set(entry['required']) == {'id', 'state', 'quote'}
    assert entry['properties']['id']['enum'] == ['first', 'reason', 'final'] and entry['properties']['state']['enum'] == ['covered', 'partly', 'missing', 'unclear']
    assert schema['properties']['points']['minItems'] == schema['properties']['points']['maxItems'] == 3
    assert 'DATA' in data['instructions'] and json.loads(data['input'])['learner_text'] == REQUEST.transcript
    assert 'answer_key' not in data['input']


def test_reasoning_effort_is_sent_only_when_configured():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=responses_body(json.dumps({'points': GOOD})))
    assessor(handler, reasoning='none').assess(REQUEST)
    assert seen[0]['reasoning'] == {'effort': 'none'}


def test_chat_completions_path_exists_for_rollback_to_older_models():
    seen = {}

    def handler(request):
        seen['path'], seen['data'] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json=chat_body(json.dumps({'points': GOOD})))
    assert assessor(handler, api='chat', model='gpt-4.1-mini', reasoning='low').assess(REQUEST) == GOOD
    data = seen['data']
    assert seen['path'].endswith('/chat/completions') and data['model'] == 'gpt-4.1-mini' and data['store'] is False and data['max_completion_tokens'] == 700
    assert data['response_format']['json_schema']['strict'] is True and data['reasoning_effort'] == 'low' and 'temperature' not in data
    assert [m['role'] for m in data['messages']] == ['system', 'user']


@pytest.mark.parametrize('text', ['not json at all', '', '[]', '{"points": "none"}', '{"verdicts": []}', 'null'])
def test_malformed_assessment_text_is_an_unusable_response(text):
    for call in (lambda: assessor(lambda r: httpx.Response(200, json=responses_body(text))).assess(REQUEST),
                 lambda: assessor(lambda r: httpx.Response(200, json=chat_body(text)), api='chat').assess(REQUEST)):
        with pytest.raises(ValueError):
            call()


def test_truncated_or_filtered_output_is_unusable_and_a_refusal_is_permanent():
    with pytest.raises(ValueError):
        assessor(lambda r: httpx.Response(200, json=responses_body('{"points": [', status='incomplete'))).assess(REQUEST)
    with pytest.raises(ValueError):
        assessor(lambda r: httpx.Response(200, json=chat_body('{"points": [', finish='length')), api='chat').assess(REQUEST)
    with pytest.raises(ProviderPermanent) as refused:
        assessor(lambda r: httpx.Response(200, json=responses_body('', refusal='I cannot help with that.'))).assess(REQUEST)
    assert refused.value.code == 'provider_refused'
    with pytest.raises(ProviderPermanent):
        assessor(lambda r: httpx.Response(200, json=chat_body(None, refusal='no')), api='chat').assess(REQUEST)


def test_well_formed_but_wrong_verdicts_are_caught_by_the_domain_layer_not_trusted():
    transcript = REQUEST.transcript
    good = parse_verdicts(json.dumps({'points': GOOD}))
    assert [v['state'] for v in ground_assessment(good, ['first', 'reason', 'final'], transcript)] == ['covered', 'covered', 'partly']
    wrong_ids = [{**v, 'id': 'x' + v['id']} for v in GOOD]
    reordered = list(reversed(GOOD))
    for bad in (wrong_ids, reordered, GOOD[:2], GOOD + GOOD[:1], [{**v, 'state': 'excellent'} for v in GOOD]):
        with pytest.raises(ValueError):
            ground_assessment(bad, ['first', 'reason', 'final'], transcript)
    invented = [{**v, 'quote': 'something she never said'} for v in GOOD]
    assert {v['state'] for v in ground_assessment(invented, ['first', 'reason', 'final'], transcript)} == {'unclear'}
    assert verdict_schema(['a'])['properties']['points']['minItems'] == 1


# ---- through the real job queue --------------------------------------------------------------------------------------------------

def pipeline(handler, plan=None):
    c = client(handler)
    settings = Settings(_env_file=None)
    plan = plan or ModelPlan.from_settings(settings)
    return OpenAITranscriber(c, plan.transcription), OpenAIAssessor(c, plan.assessment)


def route(transcript_json, assessment_json=None, assessment_status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path.endswith('/audio/transcriptions'):
            return httpx.Response(200, json=transcript_json)
        if assessment_status != 200:
            return httpx.Response(assessment_status, json=error_body(assessment_status))
        return httpx.Response(200, json=responses_body(json.dumps(assessment_json)))
    return handler


def drive(j, heard, judge, steps=1, start=None):
    start = start or utcnow()
    return [run_once(j.engine, j.objects, heard, judge, 'w', now=start + timedelta(minutes=30 * n)) for n in range(steps)]


def test_a_real_looking_provider_round_trip_assesses_in_the_background_and_deletes_the_recording(journey):
    j = journey
    view = start_pending(j)
    heard, judge = pipeline(route({'text': PEOPLE_WORDS, 'languages': [{'code': 'en'}]},
                                  {'points': [{'id': 'first', 'state': 'missing', 'quote': None}, {'id': 'reason', 'state': 'covered', 'quote': 'run out of mushrooms'},
                                              {'id': 'final', 'state': 'covered', 'quote': 'chose the chicken'}]}))
    assert drive(j, heard, judge) == ['done']
    result = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert result['status'] == 'assessed' and [p['state'] for p in result['points']] == ['missing', 'covered', 'covered']
    with j.engine.connect() as db:
        assert db.execute(select(l.spoken.c.recording_key)).scalar_one() is None


def test_unintelligible_speech_is_unknown_and_never_reaches_the_assessment_model(journey):
    j = journey
    view = start_pending(j)
    seen = []
    heard, judge = pipeline(route({'text': 'Thank you for watching, see you next time!', 'languages': []}, seen=seen))
    assert drive(j, heard, judge) == ['done']
    result = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert (result['status'], result['insufficient'], result['points'], result['transcript']) == ('assessed', True, None, '')
    assert not any(path.endswith('/responses') for path in seen)          # no paid assessment call, and the invented words were discarded


def test_temporary_provider_errors_retry_through_the_queue_then_succeed(journey):
    j = journey
    view = start_pending(j)
    attempts = []

    def handler(request):
        if request.url.path.endswith('/audio/transcriptions'):
            return httpx.Response(200, json={'text': PEOPLE_WORDS, 'languages': [{'code': 'en'}]})
        attempts.append(1)
        if len(attempts) <= 2:
            return httpx.Response(429, json=error_body(429, 'rate_limit_exceeded'))
        return httpx.Response(200, json=responses_body(json.dumps({'points': [{'id': p['id'], 'state': 'missing', 'quote': None} for p in POINTS]})))
    heard, judge = pipeline(handler)
    assert drive(j, heard, judge, steps=3) == ['retry', 'retry', 'done']
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['status'] == 'assessed'
    row, job = stored(j)
    assert job['attempts'] == 3 and row['transcript'] == PEOPLE_WORDS


@pytest.mark.parametrize('status,code,shown,stored_code', [(401, 'invalid_api_key', 'provider_error', 'provider_auth'), (404, 'model_not_found', 'provider_error', 'provider_model'),
                                                          (429, 'insufficient_quota', 'provider_error', 'provider_quota'), (400, None, 'provider_error', 'provider_bad_request')])
def test_configuration_and_quota_faults_fail_at_once_with_an_operator_code_and_a_learner_safe_one(journey, status, code, shown, stored_code):
    j = journey
    view = start_pending(j)

    def handler(request):
        return httpx.Response(status, json=error_body(status, code))
    heard, judge = pipeline(handler)
    assert drive(j, heard, judge, steps=1) == ['failed']                       # no retries of something that cannot succeed
    row, job = stored(j)
    assert (row['failure_code'], job['attempts']) == (stored_code, 1) and row['recording_key'] is None
    result = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert result['status'] == 'failed' and result['failure_code'] == shown


def test_a_model_that_keeps_returning_garbage_ends_as_failed_with_the_words_kept_for_a_later_retry(journey):
    j = journey
    view = start_pending(j)

    def handler(request):
        if request.url.path.endswith('/audio/transcriptions'):
            return httpx.Response(200, json={'text': PEOPLE_WORDS, 'languages': [{'code': 'en'}]})
        return httpx.Response(200, json=responses_body('sorry, here is some prose instead of JSON'))
    heard, judge = pipeline(handler)
    outcomes = drive(j, heard, judge, steps=6)
    assert [o for o in outcomes if o] == ['retry', 'retry', 'retry', 'failed']
    result = by_kind(refresh(j.client, view))['spoken']['spoken']
    assert (result['status'], result['failure_code'], result['transcript'], result['retryable']) == ('failed', 'unusable_assessment', PEOPLE_WORDS, True)


def test_a_verdict_for_the_wrong_points_is_retried_not_shown(journey):
    j = journey
    view = start_pending(j)
    heard, judge = pipeline(route({'text': PEOPLE_WORDS, 'languages': [{'code': 'en'}]}, {'points': [{'id': 'zzz', 'state': 'covered', 'quote': 'x'}] * 3}))
    assert drive(j, heard, judge, steps=1) == ['retry']
    assert by_kind(refresh(j.client, view))['spoken']['spoken']['status'] == 'pending'


def test_no_learner_text_or_audio_reaches_logs(journey, caplog):
    j = journey
    start_pending(j)
    caplog.set_level('INFO', logger='audli.listening.worker')
    heard, judge = pipeline(route({'text': PEOPLE_WORDS, 'languages': [{'code': 'en'}]}, {'points': [{'id': p['id'], 'state': 'missing', 'quote': None} for p in POINTS]}))
    drive(j, heard, judge)
    text = ' '.join(r.getMessage() for r in caplog.records)
    assert 'outcome=done' in text and 'mushrooms' not in text and j.owner not in text and 'sk-' not in text


def test_a_deployment_that_names_a_retiring_model_is_warned_about_before_it_breaks():
    plan = ModelPlan.from_settings(Settings(listening_assessment_model='gpt-4o-mini', _env_file=None))
    assert plan.retiring() == {'gpt-4o-mini': '2027-01-14'}
    assert SimpleNamespace(**plan.retiring())
