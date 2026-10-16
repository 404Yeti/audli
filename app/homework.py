"""Provider-neutral homework contracts. No scoring or lesson mutation."""
from datetime import datetime
from typing import Literal, Protocol
from uuid import uuid4
import re
import time
from pydantic import Field, model_validator
from app.models import StrictModel

Status = Literal['offered', 'assigned', 'completed', 'discussed', 'skipped', 'unavailable']

class Capabilities(StrictModel):
    external_playback: bool = True
    embedded_playback: bool = False
    authorized_transcript: bool = False
    grounded_evaluation: bool = False

class ListeningContent(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    provider: str = Field(min_length=1, max_length=40)
    provider_id: str = Field(min_length=1, max_length=100)
    canonical_url: str = Field(max_length=300)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(max_length=1000)
    topic: str = Field(max_length=100)
    language: str | None = Field(default=None, max_length=20)
    duration_seconds: int = Field(ge=1, le=86400)
    publisher: str = Field(min_length=1, max_length=200)
    estimated_difficulty: str | None = None
    difficulty_confidence: float | None = Field(default=None, ge=0, le=1)
    transcript_available: bool = False
    transcript_authorization: Literal['none', 'authorized'] = 'none'
    capabilities: Capabilities = Field(default_factory=Capabilities)
    validated_at: datetime
    availability: Literal['available', 'unavailable'] = 'available'
    public: bool
    processed: bool
    live: bool
    age_restricted: bool
    region_allowed: list[str] = Field(default_factory=list, max_length=250)
    region_blocked: list[str] = Field(default_factory=list, max_length=250)

    @model_validator(mode='after')
    def grounding(self):
        if (self.capabilities.authorized_transcript or self.capabilities.grounded_evaluation) and self.transcript_authorization != 'authorized':
            raise ValueError('Grounding requires an authorized transcript')
        return self

class SearchPlan(StrictModel):
    topic: str = Field(min_length=1, max_length=100)
    queries: list[str] = Field(min_length=1, max_length=1)
    practice_level: Literal['A2', 'B1', 'B2', 'C1']
    min_seconds: int = 60
    max_seconds: int = 600
    region: str | None = Field(default=None, pattern=r'^[A-Z]{2}$')

class Assignment(StrictModel):
    id: str
    lesson_id: str
    status: Status
    objective: str
    topic: str | None = None
    content: ListeningContent | None = None
    message: str | None = None
    # Contract for a future reflection; not an evaluation claim.
    reflection_grounding: Literal['unverified_self_report'] = 'unverified_self_report'

class HomeworkView(StrictModel):
    enabled: bool
    assignments: list[Assignment] = Field(default_factory=list)

class Decision(StrictModel):
    accept: bool
    topic: str | None = Field(default=None, min_length=1, max_length=100)

class StatusChange(StrictModel):
    status: Literal['completed', 'skipped']

class ContentProvider(Protocol):
    def capabilities(self) -> Capabilities: ...
    async def search(self, plan: SearchPlan) -> list[str]: ...
    async def metadata(self, ids: list[str], topic: str) -> list[ListeningContent]: ...
    def validate(self, content: ListeningContent, plan: SearchPlan) -> bool: ...

class DiscoveryUnavailable(Exception):
    """Fixed public failure; never expose provider bodies/credentials."""


def clean_topic(value: str) -> str:
    # Search terms, not instructions/operators/URLs. Bound before sending externally.
    return ' '.join(re.findall(r"[A-Za-z0-9]+", value))[:100].strip()


def search_plan(profile, lesson, topic=None, region=None):
    override = bool(topic)
    topic = clean_topic(topic or (profile.interests[0] if profile.interests else '') or lesson.plan_topic or 'everyday life')
    if not topic:
        raise ValueError('Choose a topic using English words.')
    level = profile.difficulty.vocabulary_level
    hint = 'simple English explanation' if level == 'A2' else 'English short explanation'
    # Accent preference is a search hint only; never an asserted property.
    accents = profile.accent_preferences
    accent = next(iter(accents.accents), '')
    recent = clean_topic(lesson.plan_topic or '')[:40] if not override else ''
    return SearchPlan(topic=topic, queries=[f'{topic} {hint} {clean_topic(accent)} {recent}'.strip()],
        practice_level=level, max_seconds=300 if level == 'A2' else 600, region=region)


def relevance(content: ListeningContent, plan: SearchPlan) -> int:
    words = set(re.findall(r'[a-z0-9]+', plan.topic.lower())) - {'a', 'an', 'the', 'and', 'of', 'in', 'to', 'about'}
    metadata = set(re.findall(r'[a-z0-9]+', (content.title + ' ' + content.summary).lower()))
    return len(words & metadata)


def objective(focus: str) -> str:
    return {'main_idea': 'Listen for the main idea and explain it in your own words.',
        'inference': 'Listen for the speaker’s point and the clues that support it.',
        'vocabulary': 'Listen for a useful expression and how the speaker uses it.'}.get(focus,
        'Listen for the main idea and two details that support it.')


class HomeworkEngine:
    """One registered provider, bounded cache; selection stays in application code."""
    def __init__(self, provider: ContentProvider):
        from collections import OrderedDict
        self.provider = provider
        self.cache = OrderedDict()

    async def discover(self, plan):
        key = plan.model_dump_json()
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            return [item.model_copy(deep=True) for item in cached[1]]
        contents = await self.provider.metadata(await self.provider.search(plan), plan.topic)
        self.cache[key] = (time.monotonic() + 900, contents)
        self.cache.move_to_end(key)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return contents
