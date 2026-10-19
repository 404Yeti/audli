"""AUD-71 Step A: the refactor must not change a single accept/reject decision, and counters only observe."""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import Transcription
from app.recognition import (CONTEXTS, LOW_CONFIDENCE_WARNING, NO_CONFIDENCE_WARNING, SHORT_REPLY_WARNING,
                             RecognitionIssue, check_recognition, observe_recognition, recognition_issues,
                             reliable_recognition)
from app.telemetry import aggregator
from test_short_reply_rule import provider_returning

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/recognition_gate.json').read_text())


def legacy(transcription, minimum):
    """Verbatim copy of the implementation before AUD-71."""
    return bool(transcription.text.strip() and not transcription.uncertainty
                and transcription.confidence is not None and transcription.confidence >= minimum)


def make(case):
    return Transcription(text=case['text'], confidence=case['confidence'], uncertainty=case['uncertainty'])


@pytest.mark.parametrize('minimum', [0, .5, .65, .85, 1])
def test_new_gate_is_identical_to_the_old_one_for_every_combination(minimum):
    texts = ['', ' ', 'Yes.', 'Yes please.', 'I am fine today thanks']
    confidences = [None, 0, .49, .5, .64, .65, .66, .85, 1]
    warnings = [[], [SHORT_REPLY_WARNING], [LOW_CONFIDENCE_WARNING], [NO_CONFIDENCE_WARNING],
                [SHORT_REPLY_WARNING, LOW_CONFIDENCE_WARNING], ['Unrecognized provider note']]
    for text, confidence, uncertainty in itertools.product(texts, confidences, warnings):
        t = Transcription(text=text, confidence=confidence, uncertainty=uncertainty)
        assert reliable_recognition(t, minimum) is legacy(t, minimum), (text, confidence, uncertainty, minimum)
        assert (not recognition_issues(t, minimum)) is legacy(t, minimum)


def test_shared_fixture_matches_current_behavior():
    assert len(FIXTURE['cases']) > 250
    for case in FIXTURE['cases']:
        assert reliable_recognition(make(case), case['minimum']) is case['reliable'], case


def test_issue_classification_names_every_reason():
    t = lambda text, confidence, uncertainty=(): Transcription(text=text, confidence=confidence, uncertainty=list(uncertainty))
    assert recognition_issues(t('Fine thanks again', .9), .65) == frozenset()
    assert recognition_issues(t(' ', .9), .65) == {RecognitionIssue.EMPTY}
    assert recognition_issues(t('Fine thanks again', None), .65) == {RecognitionIssue.NO_CONFIDENCE}
    assert recognition_issues(t('Fine thanks again', .5), .65) == {RecognitionIssue.LOW_CONFIDENCE}
    assert recognition_issues(t('Yes.', .9, [SHORT_REPLY_WARNING]), .65) == {RecognitionIssue.TOO_SHORT}
    assert recognition_issues(t('Fine thanks again', .9, ['x']), .65) == {RecognitionIssue.OTHER_WARNING}
    assert recognition_issues(t('', None, [SHORT_REPLY_WARNING]), .65) == {
        RecognitionIssue.EMPTY, RecognitionIssue.NO_CONFIDENCE, RecognitionIssue.TOO_SHORT}


def test_constants_are_exactly_what_the_transcriber_emits():
    import asyncio
    short = asyncio.run(provider_returning('Yes.').transcribe(b'x', 'a.wav'))
    low = asyncio.run(provider_returning('Some recognized speech here', .3).transcribe(b'x', 'a.wav'))
    assert short.uncertainty == [SHORT_REPLY_WARNING] and LOW_CONFIDENCE_WARNING in low.uncertainty
    provider = provider_returning('Some recognized speech here')
    async def create(**kwargs): return SimpleNamespace(text='Some recognized speech here', logprobs=None)
    provider.client.audio.transcriptions.create = create
    none = asyncio.run(provider.transcribe(b'x', 'a.wav'))
    assert none.uncertainty == [NO_CONFIDENCE_WARNING] and none.confidence is None
    assert RecognitionIssue.NO_CONFIDENCE in recognition_issues(none, .65)


def test_homework_alias_is_the_same_constant():
    from app.homework_voice import SHORT_ASSESSMENT_WARNING
    assert SHORT_ASSESSMENT_WARNING is SHORT_REPLY_WARNING


# ------------------------------------------------------------------------------------ counters
ON, OFF = SimpleNamespace(latency_aggregates=True), SimpleNamespace(latency_aggregates=False)


def test_counters_record_context_result_and_issues_without_content():
    aggregator.reset()
    short = Transcription(text='Football secret-word', confidence=.9, uncertainty=[SHORT_REPLY_WARNING])
    assert check_recognition(ON, 'lesson.answer', short, .65) is False
    assert check_recognition(ON, 'lesson.answer', Transcription(text='Fine thanks again', confidence=.9), .65) is True
    observe_recognition(ON, 'homework.decision', short, .65, True)      # accepted by the choice rule despite TOO_SHORT
    counters = aggregator.snapshot()['counters']
    assert counters == {'recognition.lesson.answer.accepted': 1, 'recognition.lesson.answer.rejected': 1,
                        'recognition.lesson.answer.issue.too_short': 1, 'recognition.homework.decision.accepted': 1,
                        'recognition.homework.decision.issue.too_short': 1}
    assert 'secret-word' not in json.dumps(aggregator.snapshot())


def test_counters_are_off_without_the_flag_and_bounded_when_on():
    aggregator.reset()
    t = Transcription(text='Yes.', confidence=.9, uncertainty=[SHORT_REPLY_WARNING])
    assert check_recognition(OFF, 'lesson.answer', t, .65) is False
    assert aggregator.snapshot()['counters'] == {}
    check_recognition(ON, 'learner typed this', t, .65)          # unknown contexts collapse, never become keys
    assert all(name.startswith('recognition.other.') for name in aggregator.snapshot()['counters'])
    for i in range(500):
        aggregator.count(f'x.{i}')
    assert len(aggregator.snapshot()['counters']) <= 129
    assert {'exercise.assess', 'lesson.answer', 'planner.checkin', 'onboarding.answer', 'homework.review', 'homework.decision'} == CONTEXTS


def test_http_decisions_and_responses_are_unchanged_and_counted(tmp_path, audio_bytes):
    """A hands-free assessment of an uncertain attempt is still a 422 with no state change, and is now counted."""
    from app.config import Settings
    from app.main import create_app
    from conftest import ASGIClient, FakeProvider
    from test_conversation import begin, scripted
    aggregator.reset()
    provider = FakeProvider()
    client = ASGIClient(create_app(Settings(data_dir=tmp_path, provider='openai', latency_aggregates=True, _env_file=None), provider), provider)
    calls = scripted(client, [{}])
    exercise = begin(client)
    client.provider.uncertain = True
    attempt = client.post(f"/api/exercises/{exercise['id']}/attempts", files={'audio': ('r.wav', audio_bytes, 'audio/wav')}).json()
    response = client.post(f"/api/attempts/{attempt['id']}/assess", json={'text': attempt['transcription']['text'], 'confirmed': True, 'hands_free': True})
    assert response.status_code == 422 and response.json()['uncertain'] and not calls
    counters = aggregator.snapshot()['counters']
    assert counters['recognition.exercise.assess.rejected'] == 1 and counters['recognition.exercise.assess.issue.low_confidence'] == 1
