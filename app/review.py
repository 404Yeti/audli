"""Read-only Review contracts and deterministic interpretation of saved evidence."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import StrictModel, Dimension, Difficulty, ExerciseContent
from app.evaluation import expected_units
from app.improvement import ImprovementState

DIMENSIONS = ('main_idea', 'details', 'vocabulary', 'inference')
LABELS = {'main_idea': 'Main ideas', 'details': 'Supporting details',
          'vocabulary': 'Words in context', 'inference': 'Implied meaning'}
ACTIONS = {'main_idea': 'Listen for the reason behind the speaker’s message.',
           'details': 'Listen once for the main point, then replay to check supporting details.',
           'vocabulary': 'Use the surrounding sentence to understand an unfamiliar word.',
           'inference': 'Connect the speaker’s reasons with what happens next.'}
Kind = Literal['lesson', 'earlier_practice']
Outcome = Literal['demonstrated', 'partial', 'misunderstood', 'not_demonstrated']
PatternLabel = Literal['Consistent strength', 'Developing', 'Needs more practice', 'Not enough evidence']


class ReviewUnit(StrictModel):
    dimension: Dimension
    index: int
    status: Outcome
    meaning: str
    evidence: str | None


class SavedAnswer(StrictModel):
    """One saved spoken response, shown only for an evaluated passage. Never the script or expected answers."""
    attempt_id: str
    question: str | None
    text: str
    uncertain: bool


class PassageReport(StrictModel):
    exercise_id: str
    title: str
    topic: str | None
    level: str | None
    accent: str | None
    accent_fallback: bool
    units: list[ReviewUnit]
    answers: list[SavedAnswer]
    assessment_available: bool
    saved_feedback: str | None
    next_focus: str
    adaptation_reason: str | None
    transcript_url: str
    audio_url: str | None
    improvement: ImprovementState | None = None  # Advisory language coaching; absent unless the feature is on.


class ReviewLesson(StrictModel):
    id: str
    kind: Kind
    completed: bool
    completed_at: datetime | None
    title: str
    topics: list[str]
    levels: list[str]
    takeaway: str
    assessed_passages: int


class LessonReport(StrictModel):
    lesson: ReviewLesson
    passages: list[PassageReport]


class ListeningPattern(StrictModel):
    dimension: Dimension
    title: str
    label: PatternLabel = 'Not enough evidence'
    observations: int = 0
    practice_days: int = 0
    first_date: str | None = None
    last_date: str | None = None
    context: str | None = None


class ReviewSummary(StrictModel):
    completed_lessons: int
    structured_lessons: int
    earlier_practice: int
    practice_days: int
    undated_lessons: int
    timezone_used: str
    timezone_fallback: bool
    current_practice_level: str | None


class ReviewDashboard(StrictModel):
    summary: ReviewSummary
    insight: str
    patterns_version: Literal['review-patterns-v1'] = 'review-patterns-v1'
    patterns: list[ListeningPattern]
    lessons: list[ReviewLesson]
    assessed_practice: list[ReviewLesson]
    next_cursor: str | None


@dataclass
class Entry:
    id: str
    kind: Kind
    completed: bool
    completed_at: datetime | None
    title: str | None = None
    passages: list[dict] = field(default_factory=list)

    @property
    def key(self):
        return f'{self.kind}:{self.id}'


def timestamp(value) -> datetime | None:
    """Missing/naive/corrupt historical dates are unknown, not today's date."""
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None:
            return None
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def display_zone(name: str | None) -> tuple[ZoneInfo, bool]:
    try:
        return ZoneInfo(name), False if name else True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo('UTC'), True


def chronological(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda item: (item.completed_at or datetime.min.replace(tzinfo=timezone.utc), item.key), reverse=True)


def text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def interpreted_units(row: dict) -> tuple[list[ReviewUnit], bool]:
    """Never turn a flattened feedback list or a partial schema into new evidence."""
    evaluation = row.get('evaluation') or {}
    if not isinstance(evaluation, dict) or evaluation.get('version') not in (None, 'v0.1', 'v0.2'):
        return [], False
    mapping = ({'demonstrated': 'demonstrated', 'partially_demonstrated': 'partial',
                'misunderstood': 'misunderstood', 'insufficient_evidence': 'not_demonstrated'}
               if evaluation.get('version') == 'v0.2' else
               {'understood': 'demonstrated', 'partial': 'partial', 'misunderstood': 'misunderstood', 'missed': 'not_demonstrated'})
    try:
        expected = expected_units(ExerciseContent.model_validate(row['content']))
        required = {(dim, i) for dim, meanings in expected.items() for i in range(len(meanings))}
        units, seen = [], set()
        for unit in evaluation.get('units', []):
            key = unit['dimension'], unit['index']
            status = mapping[unit['status']]
            if type(key[1]) is not int or key not in required or key in seen:
                return [], False
            evidence = text(unit.get('evidence'))
            if status != 'not_demonstrated' and not evidence:
                return [], False
            seen.add(key)
            units.append(ReviewUnit(dimension=key[0], index=key[1], status=status,
                meaning=expected[key[0]][key[1]], evidence=evidence if status != 'not_demonstrated' else None))
        return units, seen == required
    except (ValueError, KeyError, TypeError, IndexError):
        return [], False


def recommendation(units: list[ReviewUnit]) -> str:
    for status in ('misunderstood', 'partial'):
        unit = next((unit for unit in units if unit.status == status), None)
        if unit:
            return ACTIONS[unit.dimension]
    if not units or any(unit.status == 'not_demonstrated' for unit in units):
        return 'Try another listening lesson to give Audli more evidence of your understanding.'
    return 'Keep practicing with new passages and check the details against the main point.'


def lesson_card(entry: Entry) -> ReviewLesson:
    topics, levels = [], []
    demonstrated, partial, incorrect, unknown = 0, 0, 0, 0
    for row in entry.passages:
        content = row['content'] or {}
        if topic := text(content.get('topic')):
            topics.append(topic)
        if level := content.get('vocabulary_level'):
            if level in ('A2', 'B1', 'B2', 'C1'):
                levels.append(level)
        units, _ = interpreted_units(row)
        demonstrated += sum(unit.status == 'demonstrated' for unit in units)
        partial += sum(unit.status == 'partial' for unit in units)
        incorrect += sum(unit.status == 'misunderstood' for unit in units)
        unknown += sum(unit.status == 'not_demonstrated' for unit in units)
    takeaway = ('Some meaning needs another look.' if incorrect else
                'Some understanding was incomplete.' if partial else
                'Some information was not demonstrated.' if unknown else
                'Understanding was demonstrated in your responses.' if demonstrated else
                'Detailed assessment unavailable for this lesson.')
    return ReviewLesson(id=entry.id, kind=entry.kind, completed=entry.completed,
        completed_at=entry.completed_at, title=entry.title or next((text(row['content'].get('title')) for row in entry.passages
            if text(row['content'].get('title'))), None) or 'Listening lesson',
        topics=list(dict.fromkeys(topics)), levels=list(dict.fromkeys(levels)), takeaway=takeaway,
        assessed_passages=len(entry.passages))


def saved_answers(row: dict) -> list[SavedAnswer]:
    """Learner responses in order. Missing or blank history yields nothing, never reconstructed text."""
    answers = []
    for item in row.get('answers') or []:
        body = text(item.get('text'))
        if body:
            answers.append(SavedAnswer(attempt_id=item['attempt_id'], question=text(item.get('question')),
                text=body, uncertain=bool(item.get('uncertain'))))
    return answers


def passage_report(row: dict, improvement: ImprovementState | None = None) -> PassageReport:
    content, evaluation = row['content'] or {}, row.get('evaluation') or {}
    units, complete = interpreted_units(row)
    voice = content.get('passage_voice') or {}
    accent = text(voice.get('accent'))
    return PassageReport(exercise_id=row['exercise_id'], title=text(content.get('title')) or 'Listening passage',
        topic=text(content.get('topic')), level=content.get('vocabulary_level') if content.get('vocabulary_level') in ('A2', 'B1', 'B2', 'C1') else None,
        accent=accent if accent != 'default' else None, accent_fallback=bool(voice.get('fallback')),
        units=units, answers=saved_answers(row), assessment_available=complete, saved_feedback=text(evaluation.get('feedback')),
        next_focus=recommendation(units), adaptation_reason=text((row.get('adaptation') or {}).get('reason')),
        transcript_url=f"/api/exercises/{row['exercise_id']}/transcript",
        audio_url=f"/api/exercises/{row['exercise_id']}/audio" if row['audio_available'] else None,
        improvement=improvement)


def context_key(row: dict) -> tuple | None:
    content = row['content'] or {}
    voice = content.get('passage_voice') or {}
    accent = text(voice.get('accent'))
    try:
        difficulty = Difficulty.model_validate(row['difficulty'])
        # Missing actual routing is not inferred from preferences or consent.
        if not accent or not all(key in row['difficulty'] for key in ('speech_rate', 'duration_seconds', 'vocabulary_level', 'information_density')):
            return None
        return (difficulty.speech_rate, difficulty.duration_seconds, difficulty.vocabulary_level,
                difficulty.information_density, accent, bool(voice.get('fallback')))
    except (ValueError, TypeError):
        return None


def listening_patterns(entries: list[Entry], zone: ZoneInfo) -> list[ListeningPattern]:
    groups = {dimension: defaultdict(list) for dimension in DIMENSIONS}
    for entry in chronological(entries)[:10]:
        if not entry.completed_at or not entry.passages:
            continue
        contexts = [context_key(row) for row in entry.passages]
        if None in contexts or len(set(contexts)) != 1:
            continue
        observations = []
        for row in entry.passages:
            units, complete = interpreted_units(row)
            if not complete or (row.get('evaluation') or {}).get('version') != 'v0.2':
                break
            observations.extend(units)
        else:
            for dimension in DIMENSIONS:
                statuses = {unit.status for unit in observations if unit.dimension == dimension}
                if not statuses or 'not_demonstrated' in statuses:
                    continue
                outcome = 'misunderstood' if 'misunderstood' in statuses else 'partial' if 'partial' in statuses else 'demonstrated'
                groups[dimension][contexts[0]].append((entry.completed_at.astimezone(zone).date().isoformat(), outcome))
    patterns = []
    for dimension in DIMENSIONS:
        pattern = ListeningPattern(dimension=dimension, title=LABELS[dimension])
        # Prefer the context with the most recent eligible observation; stable ties.
        candidates = sorted(groups[dimension].items(), key=lambda item: (item[1][0][0], str(item[0])), reverse=True)
        for context, observations in candidates:
            days = {day for day, _ in observations}
            if len(observations) < 3 or len(days) < 2:
                continue
            last = [outcome for _, outcome in observations[:3]]
            pattern.label = ('Consistent strength' if set(last) == {'demonstrated'} else
                             'Needs more practice' if sum(outcome != 'demonstrated' for outcome in last) >= 2 else 'Developing')
            pattern.observations, pattern.practice_days = len(observations), len(days)
            pattern.first_date, pattern.last_date = min(days), max(days)
            rate, duration, level, density, accent, fallback = context
            pattern.context = f'{level} material · {duration}s target · pace {rate:g} · density {density} · {accent}' + (' (fallback)' if fallback else '')
            break
        patterns.append(pattern)
    return patterns


def insight(patterns: list[ListeningPattern]) -> str:
    strength = next((p for p in patterns if p.label == 'Consistent strength'), None)
    focus = next((p for p in patterns if p.label == 'Needs more practice'), None)
    parts = []
    if strength:
        parts.append(f'Your responses consistently demonstrated understanding of {strength.title.lower()} in repeated saved assessments.')
    if focus:
        parts.append(f'A useful area to practice is {focus.title.lower()}.')
    return ' '.join(parts) or 'Complete a few more lessons to discover your listening patterns.'
