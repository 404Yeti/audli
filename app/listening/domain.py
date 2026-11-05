"""Pure domain rules for 2.0 listening lessons: content validation and deterministic scoring."""
import hashlib
import json
import re
import unicodedata
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models import StrictModel

CEFR = Literal['A1', 'A2', 'B1', 'B2', 'C1', 'C2']
ID = r'^[a-z0-9][a-z0-9_-]{0,31}$'
BLANK = '___'


def normalize_text(value: str) -> str:
    """Case, accents-of-form, punctuation and spacing do not decide a fill-in-the-blank answer; the words do."""
    folded = unicodedata.normalize('NFKC', value).casefold().replace('’', "'")
    return ' '.join(re.sub(r"[^\w' ]+", ' ', folded).split())


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


class FillBlank(StrictModel):
    """The sentence shows exactly one ___ gap. The accepted answers live in FillKey and never reach a learner before they answer."""
    prompt: str = Field(min_length=1, max_length=300)

    @field_validator('prompt')
    @classmethod
    def one_gap(cls, prompt):
        if prompt.count(BLANK) != 1:
            raise ValueError('A fill-in-the-blank prompt needs exactly one ___ gap')
        return prompt


class FillKey(StrictModel):
    accepted: list[str] = Field(min_length=1, max_length=6)
    explanation: str = Field(min_length=1, max_length=400)

    @field_validator('accepted')
    @classmethod
    def usable(cls, accepted):
        normal = [normalize_text(a) for a in accepted]
        if any(not n or len(n) > 40 for n in normal) or len(set(normal)) != len(normal):
            raise ValueError('Accepted answers must be short, non-empty and distinct')
        return accepted


class Sequencing(StrictModel):
    """Items are stored in the order the learner first sees them (deliberately not the correct order)."""
    prompt: str = Field(min_length=1, max_length=300)
    items: list[Option] = Field(min_length=3, max_length=6)

    @field_validator('items')
    @classmethod
    def unique_items(cls, items):
        if len({i.id for i in items}) != len(items) or len({i.text.casefold() for i in items}) != len(items):
            raise ValueError('Items must be unique')
        return items


class SequenceKey(StrictModel):
    order: list[str] = Field(min_length=3, max_length=6)
    explanation: str = Field(min_length=1, max_length=400)


class Spoken(StrictModel):
    """A question answered aloud. `points` are the ideas a good answer contains; they are the rubric and stay private until assessed."""
    prompt: str = Field(min_length=1, max_length=300)
    points: list[Option] = Field(min_length=1, max_length=5)

    @field_validator('points')
    @classmethod
    def unique_points(cls, points):
        if len({p.id for p in points}) != len(points):
            raise ValueError('Key point ids must be unique')
        return points


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
    # Milestone 02. Optional so a Milestone 01 lesson stays valid; when present they follow the multiple choice in this order.
    spoken: Spoken | None = None
    fill_blank: FillBlank | None = None
    fill_key: FillKey | None = None
    sequencing: Sequencing | None = None
    sequence_key: SequenceKey | None = None

    @model_validator(mode='after')
    def consistent(self):
        if (self.fill_blank is None) != (self.fill_key is None) or (self.sequencing is None) != (self.sequence_key is None):
            raise ValueError('Each exercise needs both its content and its key')
        if self.sequence_key and self.sequencing and (sorted(self.sequence_key.order) != sorted(i.id for i in self.sequencing.items)
                                                      or [i.id for i in self.sequencing.items] == self.sequence_key.order):
            raise ValueError('The sequence key must order exactly the listed items, and the first view must not already be correct')
        if [s.index for s in self.sentences] != list(range(len(self.sentences))):
            raise ValueError('Sentence indexes must be consecutive from 0')
        known = {s.id for s in self.speakers}
        if len(known) != len(self.speakers) or any(s.speaker not in known for s in self.sentences):
            raise ValueError('Every sentence needs a declared speaker')
        if self.key.correct_option_id not in {o.id for o in self.exercise.options}:
            raise ValueError('The answer key must name one of the options')
        return self

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode='json', exclude_none=True), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


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


def score_fill_blank(key: FillKey, text: str) -> bool:
    answer = normalize_text(text)
    return bool(answer) and answer in {normalize_text(a) for a in key.accepted}


def score_sequence(key: SequenceKey, order: list[str]) -> bool:
    return order == key.order


def sequence_positions_correct(key: SequenceKey, order: list[str]) -> int:
    return sum(1 for given, wanted in zip(order, key.order) if given == wanted)


# ---- spoken response: grounding --------------------------------------------------------------------------------------------
# A model only classifies each key point and quotes the learner. Everything the learner reads is built here from the lesson's own
# key points and verified quotes, so the assessment can never contain an invented score, praise or claim about mastery.
POINT_STATES = ('covered', 'partly', 'missing', 'unclear')
MIN_WORDS = 3


def word_count(text: str) -> int:
    return len(normalize_text(text).split())


def ground_assessment(raw: list[dict], point_ids: list[str], transcript: str) -> list[dict]:
    """Validate a provider's per-point verdicts against the rubric and the learner's own words. Raises ValueError if unusable."""
    if not isinstance(raw, list) or [p.get('id') if isinstance(p, dict) else None for p in raw] != point_ids:
        raise ValueError('Assessment must answer every key point exactly once, in order')
    heard = normalize_text(transcript)
    verdicts = []
    for item in raw:
        state, quote = item.get('state'), item.get('quote')
        if state not in POINT_STATES:
            raise ValueError('Unknown key point state')
        if state in ('covered', 'partly'):
            # A claim of understanding needs evidence the learner actually said. Without it the honest answer is "unclear".
            if not isinstance(quote, str) or not normalize_text(quote) or f' {normalize_text(quote)} ' not in f' {heard} ':
                state, quote = 'unclear', None
            else:
                quote = quote.strip()[:200]
        else:
            quote = None
        verdicts.append({'id': item['id'], 'state': state, 'quote': quote})
    return verdicts
