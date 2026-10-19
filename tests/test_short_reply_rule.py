"""Characterization of the under-three-word rule (AUD-68 Phase 0). Documents CURRENT behavior only.

Nothing here weakens a gate. See docs/AUD-68-SHORT-REPLY-RULE.md for the proposed context-aware design,
which requires owner approval and its own tests before any behavior change.
"""
from types import SimpleNamespace

import asyncio
import math
import pytest

from app.config import Settings
from app.homework_voice import SHORT_ASSESSMENT_WARNING, reliable_choice
from app.models import Transcription
from app.recognition import reliable_recognition
from app.services.openai_provider import OpenAIProvider

MIN = .65


def provider_returning(text, probability=.95):
    provider = OpenAIProvider(Settings(openai_api_key='test-key-not-real', _env_file=None))
    words = max(1, len(text.split()))
    logprobs = [SimpleNamespace(logprob=math.log(probability)) for _ in range(words)]
    async def create(**kwargs):
        return SimpleNamespace(text=text, logprobs=logprobs)
    provider.client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    return provider


def transcribe(text, probability=.95):
    return asyncio.run(provider_returning(text, probability).transcribe(b'x', 'a.wav'))


@pytest.mark.parametrize('text, short', [
    ('Football.', True), ('Yes please.', True), ('Keep them.', True), ('Maybe later.', True),
    ('Travel and music.', False), ('I am fine.', False), ('Not very good.', False),
])
def test_provider_flags_replies_under_three_words_even_when_confident(text, short):
    result = transcribe(text)
    assert (SHORT_ASSESSMENT_WARNING in result.uncertainty) is short
    assert result.confidence is not None and result.confidence >= .9   # the flag is about length, not acoustics


def test_short_warning_constant_matches_the_provider_message():
    assert SHORT_ASSESSMENT_WARNING in transcribe('Yes.').uncertainty   # guards the duplicated string coupling


def test_general_gate_rejects_any_uncertainty_including_the_short_warning():
    short = transcribe('Yes please.')
    assert reliable_recognition(short, MIN) is False
    assert reliable_recognition(transcribe('I am a little tired today.'), MIN) is True
    assert reliable_recognition(Transcription(text='I am a little tired today.', confidence=None), MIN) is False
    assert reliable_recognition(transcribe('I am a little tired today.', .3), MIN) is False


def test_homework_choice_already_tolerates_short_known_intents_only():
    assert reliable_choice(transcribe('Yes please.'), MIN) is True
    assert reliable_choice(transcribe('No thanks.'), MIN) is True
    assert reliable_choice(transcribe('Football.'), MIN) is False          # not a known choice
    assert reliable_choice(transcribe('Yes please.', .3), MIN) is False     # confidence still gates
    noisy = Transcription(text='Yes please.', confidence=.9, uncertainty=[SHORT_ASSESSMENT_WARNING, 'Speech recognition was uncertain; please verify or record again.'])
    assert reliable_choice(noisy, MIN) is False                              # any other warning still gates
