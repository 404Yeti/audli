"""Pure domain rules for 2.0 listening lessons: content validation and deterministic scoring."""
import hashlib
import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models import StrictModel

CEFR = Literal['A1', 'A2', 'B1', 'B2', 'C1', 'C2']
ID = r'^[a-z0-9][a-z0-9_-]{0,31}$'


class Speaker(StrictModel):
    id: str = Field(pattern=ID)
    name: str = Field(min_length=1, max_length=40)
    role: str = Field(min_length=1, max_length=40)


class Sentence(StrictModel):
    index: int = Field(ge=0)
    speaker: str = Field(pattern=ID)
    text: str = Field(min_length=1, max_length=400)


class Cue(StrictModel):
    """Playback window of one sentence in one audio variant, in milliseconds."""
    index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class Option(StrictModel):
    id: str = Field(pattern=ID)
    text: str = Field(min_length=1, max_length=200)


class MultipleChoice(StrictModel):
    """Learner-visible part of a question. The answer key is a separate type that never reaches a learner response."""
    prompt: str = Field(min_length=1, max_length=300)
    options: list[Option] = Field(min_length=3, max_length=5)

    @field_validator('options')
    @classmethod
    def unique_options(cls, options):
        if len({o.id for o in options}) != len(options) or len({o.text.casefold() for o in options}) != len(options):
            raise ValueError('Options must be unique')
        return options


class AnswerKey(StrictModel):
    correct_option_id: str = Field(pattern=ID)
    explanation: str = Field(min_length=1, max_length=400)


class LessonContent(StrictModel):
    """Everything that is frozen into one published lesson version."""
    slug: str = Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$', max_length=80)
    title: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=120)
    language: Literal['en'] = 'en'
    cefr: CEFR
    listen_for: str = Field(min_length=1, max_length=200)
    speakers: list[Speaker] = Field(min_length=1, max_length=4)
    sentences: list[Sentence] = Field(min_length=2, max_length=60)
    exercise: MultipleChoice
    key: AnswerKey

    @model_validator(mode='after')
    def consistent(self):
        if [s.index for s in self.sentences] != list(range(len(self.sentences))):
            raise ValueError('Sentence indexes must be consecutive from 0')
        known = {s.id for s in self.speakers}
        if len(known) != len(self.speakers) or any(s.speaker not in known for s in self.sentences):
            raise ValueError('Every sentence needs a declared speaker')
        if self.key.correct_option_id not in {o.id for o in self.exercise.options}:
            raise ValueError('The answer key must name one of the options')
        return self

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_alignment(cues: list[Cue], sentence_count: int, duration_ms: int) -> None:
    """One ordered, non-overlapping cue per sentence, all inside the recording."""
    if [c.index for c in cues] != list(range(sentence_count)):
        raise ValueError('Alignment needs exactly one cue per sentence, in order')
    previous_end = 0
    for cue in cues:
        if cue.start_ms < previous_end or cue.end_ms <= cue.start_ms or cue.end_ms > duration_ms:
            raise ValueError('Alignment cues must be ordered, non-overlapping and inside the audio')
        previous_end = cue.end_ms


# Heuristic guard, not a CEFR classifier: it catches scripts that are obviously the wrong size for the level.
LEVEL_LIMITS = {'A1': (40, 120, 12), 'A2': (60, 180, 16), 'B1': (90, 260, 20), 'B2': (120, 400, 26), 'C1': (150, 600, 34), 'C2': (150, 800, 40)}


def check_level_fit(content: LessonContent) -> list[str]:
    low, high, longest = LEVEL_LIMITS[content.cefr]
    counts = [len(re.findall(r"[\w'’-]+", s.text)) for s in content.sentences]
    problems = []
    if not low <= sum(counts) <= high:
        problems.append(f'{sum(counts)} words is outside {low}-{high} for {content.cefr}')
    if max(counts) > longest:
        problems.append(f'a {max(counts)}-word sentence exceeds {longest} for {content.cefr}')
    return problems


def score_multiple_choice(key: AnswerKey, option_id: str) -> bool:
    return option_id == key.correct_option_id
