"""Choose what tomorrow's challenge is about: a pure function of the history, the date and which voices/formats exist right now.

No database, no clock, no provider. Rotation rules (all deterministic, ties broken by a hash of the date so a retry on the same day picks the same thing):
* a subtopic is used once, ever (a topic that was rejected today is also excluded for the rest of that day);
* the category differs from the last RECENT_CATEGORIES days; the two "overused" categories (work, food/cafe) may fill at most OVERUSED_MAX of the last 14 days;
* the CEFR level is the one used least recently among A1-B2 (never the same as yesterday);
* the listening format (monologue / conversation / mixed-accent) and the accent are the least recently used *supported* ones. An accent or format with no approved
  voice is never offered, so no substitution can ever happen downstream.
"""
import hashlib
from dataclasses import dataclass
from datetime import date

from app.listening.daily_taxonomy import BY_KEY, LEVELS, OVERUSED, TAXONOMY, Subtopic

RECENT_CATEGORIES = 3
OVERUSED_WINDOW, OVERUSED_MAX = 14, 3
MONOLOGUE, CONVERSATION, MIXED = 'monologue', 'conversation', 'mixed_accent_conversation'
HINT = {MONOLOGUE: ' One speaker only.', CONVERSATION: ' Two speakers talk to each other.', MIXED: ' Two speakers with different accents talk to each other.'}


@dataclass(frozen=True)
class Plan:
    topic_key: str
    category: str
    topic: str
    scenario: str
    cefr: str
    kind: str                # monologue | conversation | mixed_accent_conversation
    accent: str
    accent2: str | None
    minutes: int
    source: str = 'pool'     # pool | ai

    def spec(self) -> dict:
        """The dict the generation pipeline's Spec validates. Mixed and same-accent conversations are both format 'conversation' (accent2 tells them apart)."""
        return {'topic': self.topic, 'cefr': self.cefr, 'accent': self.accent, 'minutes': self.minutes, 'format': 'story' if self.kind == MONOLOGUE else 'conversation',
                'scenario': self.scenario, 'accent2': self.accent2}

    def as_json(self) -> dict:
        return {'topic_key': self.topic_key, 'category': self.category, 'topic': self.topic, 'scenario': self.scenario, 'cefr': self.cefr, 'kind': self.kind,
                'accent': self.accent, 'accent2': self.accent2, 'minutes': self.minutes, 'source': self.source}

    @classmethod
    def from_json(cls, data: dict) -> 'Plan':
        return cls(**{k: data[k] for k in ('topic_key', 'category', 'topic', 'scenario', 'cefr', 'kind', 'accent', 'accent2', 'minutes', 'source')})


def _tie(day: str, key: str) -> str:
    return hashlib.sha256(f'{day}:{key}'.encode()).hexdigest()


def _least_recent(options: list[str], recent: list[str], day: str, avoid: str | None = None) -> str:
    """The option used longest ago (or never); `recent` is newest-first. Avoids `avoid` when there is any alternative."""
    pool = [o for o in options if o != avoid] or list(options)
    def age(option):                                   # larger = longer ago; never used = largest
        return recent.index(option) if option in recent else len(recent) + 1
    return sorted(pool, key=lambda o: (-age(o), _tie(day, o)))[0]


def pool_remaining(used_keys: set[str], pool: tuple[Subtopic, ...] = TAXONOMY) -> int:
    return sum(1 for s in pool if s.key not in used_keys)


def choose(day: date, history: list[dict], availability: dict, rejected_keys: set[str] = frozenset(), minutes: int = 2,
           levels: tuple[str, ...] = LEVELS, extra: tuple[Subtopic, ...] = (), force_extra: bool = False) -> Plan | None:
    """`history` = accepted challenges, newest first: {topic_key, category, cefr, kind, accent, accent2}. `availability` = generation.format_availability().
    `extra` = additional (AI-proposed) subtopics offered this call; `force_extra` restricts the choice to them. Returns None when nothing can be offered."""
    d = day.isoformat()
    levels = tuple(l for l in levels if l in LEVELS)
    used = {h['topic_key'] for h in history} | set(rejected_keys)
    pool = [s for s in (extra if force_extra else (*TAXONOMY, *extra)) if s.key not in used]
    if not pool or not levels:
        return None
    # Level first (so the pool is filtered for it), then category rules, relaxing in a fixed order if they leave nothing.
    level = _least_recent(list(levels), [h['cefr'] for h in history], d, avoid=history[0]['cefr'] if history else None)
    kinds = [k for k in (MONOLOGUE, CONVERSATION, MIXED) if availability.get(k)]
    if not kinds:
        return None
    suited = [s for s in pool if s.suits(level)] or pool
    recent_cats = [h['category'] for h in history[:RECENT_CATEGORIES]]
    overused_recent = sum(1 for h in history[:OVERUSED_WINDOW] if h['category'] in OVERUSED)
    strict = [s for s in suited if s.category not in recent_cats and not (s.category in OVERUSED and overused_recent >= OVERUSED_MAX)]
    relaxed = [s for s in suited if not (s.category in OVERUSED and overused_recent >= OVERUSED_MAX)]
    for candidates in (strict, relaxed, suited):
        if candidates:
            break
    # Prefer the category used longest ago, then a stable hash.
    cat_history = [h['category'] for h in history]
    category = _least_recent(sorted({s.category for s in candidates}), cat_history, d)
    sub = sorted((s for s in candidates if s.category == category), key=lambda s: _tie(d, s.key))[0]
    kind = _least_recent(kinds, [h['kind'] for h in history], d, avoid=history[0]['kind'] if history else None)
    accents = list(availability[kind])
    recent_accents = [a for h in history for a in (h['accent'], h.get('accent2')) if a]
    accent = _least_recent(accents, recent_accents, d)
    accent2 = None
    if kind == MIXED:
        others = [a for a in accents if a != accent]
        if not others:
            return None
        accent2 = _least_recent(others, recent_accents, d)
    scenario = (sub.scenario[:200 - len(HINT[kind])] + HINT[kind]).strip()
    return Plan(sub.key, sub.category, sub.topic, scenario, level, kind, accent, accent2, minutes, 'ai' if sub in extra else 'pool')


def breakdown(history: list[dict]) -> dict:
    """Operator view of diversity over what has been published: counts by category, level, kind, accent."""
    out: dict[str, dict[str, int]] = {'category': {}, 'cefr': {}, 'kind': {}, 'accent': {}}
    for h in history:
        for field in out:
            for value in ([h['accent'], h.get('accent2')] if field == 'accent' else [h[field]]):
                if value:
                    out[field][value] = out[field].get(value, 0) + 1
    return out


def make_subtopic(key: str, category: str, topic: str, scenario: str, lo: str = 'A1', hi: str = 'B2') -> Subtopic:
    return Subtopic(key, category, topic, scenario, lo, hi)


__all__ = ['Plan', 'choose', 'pool_remaining', 'breakdown', 'make_subtopic', 'BY_KEY', 'MONOLOGUE', 'CONVERSATION', 'MIXED']
