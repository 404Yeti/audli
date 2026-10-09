"""Teacher-led lesson policy. Presentation never decides progress or timing."""
import re
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, field_validator
from app.models import StrictModel, Transcription, Dimension

TARGET_SECONDS = 600
CLOSING_SECONDS = 90
MIN_EXERCISE_SECONDS = 90
FOCUS = {
    'main_idea': 'following the main idea', 'details': 'catching important details',
    'vocabulary': 'understanding words in context', 'inference': 'hearing implied meaning',
}
TIPS = {
    'main_idea': 'Listen for the reason behind the speaker’s message.',
    'details': 'Listen for dates, times, and changes in plans.',
    'vocabulary': 'Use the surrounding sentence to understand an unfamiliar word.',
    'inference': 'Connect the speaker’s reasons with what happens next.',
}
Phase = Literal['WELCOME', 'WELCOME_ACK', 'REVIEW', 'REVIEW_ACK', 'TRANSITION', 'EXERCISE', 'CLOSING', 'COMPLETED']

class Lesson(StrictModel):
    version: Literal[1] = 1
    id: str
    revision: int = Field(default=0, ge=0)
    status: Literal['active', 'paused', 'completed'] = 'active'
    phase: Phase = 'WELCOME'
    started_at: datetime
    active_since: datetime | None
    elapsed_seconds: float = Field(default=0, ge=0)
    completed_at: datetime | None = None
    name: str
    focus: Dimension
    previous: str | None = None
    exercise_id: str | None = None
    introduced_exercise_id: str | None = None
    welcome_response: str | None = None
    review_response: str | None = None
    pending: Transcription | None = None
    checkin: str | None = None
    reflection: str | None = None
    closing: str | None = None
    strength: str | None = None
    improvement_focus: str | None = None
    exercises_completed: int = Field(default=0, ge=0)

def now():
    return datetime.now(timezone.utc)

def elapsed(lesson: Lesson, at: datetime) -> float:
    return lesson.elapsed_seconds + (max(0, (at - lesson.active_since).total_seconds()) if lesson.active_since else 0)

def closing_due(lesson: Lesson, at: datetime) -> bool:
    return TARGET_SECONDS - elapsed(lesson, at) < CLOSING_SECONDS + MIN_EXERCISE_SECONDS

def safe_label(value: str) -> str | None:
    """Use complete labels only; avoid instructions, metrics and oversized prose."""
    value = value.strip().rstrip('.!')
    if (not value or len(value.split()) > 12 or re.search(r'[?!。\n\d%]|\b(can you|could you|tell me|ignore|please|percent)\b', value, re.I)
            or '.' in value):
        return None
    return value

def observations(evaluations) -> tuple[str | None, str | None]:
    strength = None
    gap = None
    for evaluation in evaluations:
        for dimension, state in evaluation.get('dimensions', {}).items():
            if state == 'demonstrated' and dimension in FOCUS:
                strength = strength or FOCUS[dimension]
            if state in ('misunderstood', 'partially_demonstrated') and dimension in FOCUS:
                gap = gap or dimension
    return strength, gap

def previous_copy(topic: str, evaluations) -> str:
    strength, gap = observations(evaluations)
    label = safe_label(topic)
    intro = f'Last time, we listened to a recording about {label}.' if label else 'Last time, we practiced English listening.'
    observation = (f'You demonstrated strength in {strength}.' if strength else
                   f'We found room to practice {FOCUS[gap]}.' if gap else 'We’ll build from that listening practice.')
    return intro + ' ' + observation

def prompt(lesson: Lesson) -> str | None:
    name = safe_label(lesson.name) or 'Listener'
    return {
        'WELCOME': f'Hey {name}! How’s your day going?',
        'WELCOME_ACK': lesson.welcome_response or social_response(lesson.checkin or ''),
        'REVIEW': (lesson.previous or '') + ' What would you enjoy listening to more of today?',
        'REVIEW_ACK': lesson.review_response or 'I’ll use that to guide today’s listening.',
        'TRANSITION': f'Alright, I’ve got something for you to listen to. We’ll focus on {FOCUS[lesson.focus]}.',
        'CLOSING': lesson.closing,
    }.get(lesson.phase)

RECIPROCAL_REPLY = 'Doing well, thanks for asking!'
_RECIPROCAL = re.compile(r"\b(?:and\s+)?(?:and you|how about you|what about you|how are you|how're you|how is your day|how's your day)\b", re.I)


def reciprocal_checkin(text: str) -> bool:
    return bool(_RECIPROCAL.search(text.replace('’', "'")))


def social_response(text: str) -> str:
    """One bounded social reply, without assessment or invented personal experiences."""
    normalized = text.lower().replace('’', "'")
    reciprocal = reciprocal_checkin(normalized)
    difficult = bool(re.search(r'\b(not (?:so )?(?:good|well|great)|tired|sad|stressed|bad|rough|difficult)\b', normalized))
    positive = bool(re.search(r'\b(good|well|great|fine|happy|okay|ok)\b', normalized))
    acknowledgment = ('Sounds like a tough day. We’ll take it easy.' if difficult else
                      'Good to hear.' if positive else 'Glad you’re here.')
    if not difficult and not positive:
        if re.search(r'\b(work|working|busy)\b', normalized):
            acknowledgment = 'Sounds like you’ve been busy.'
        elif re.search(r'\b(walk|walked|walking|run|running)\b', normalized):
            acknowledgment = 'Sounds like a nice break outside.'
        elif re.search(r'\b(rain|raining|rainy)\b', normalized):
            acknowledgment = 'Sounds like a rainy day where you are.'
    response = RECIPROCAL_REPLY if reciprocal else ''
    return ' '.join(part for part in (acknowledgment, response) if part)

class CheckinReply(StrictModel):
    text: str = Field(min_length=1, max_length=280)

    @field_validator('text')
    @classmethod
    def bounded_reply(cls, text):
        if not text.strip() or len(text.split()) > 35 or re.search(r'[?？\n]|\b(?:ignore instructions|system prompt)\b', text, re.I):
            raise ValueError('Check-in replies must be bounded statements')
        if len(re.findall(r'[.!]+(?:\s|$)', text)) > 2 or re.search(
                r'\b(correct|well done|great job|you understood|you caught)\b', text, re.I):
            raise ValueError('Check-in replies must be brief social responses, not comprehension praise')
        if text.strip().casefold().rstrip('.!') in (
                'thanks', 'got it', 'okay, i heard you', 'thanks, let me think about that'):
            raise ValueError('A check-in reply must respond rather than supply generic processing filler')
        if re.search(r"\b(?:i(?:'m| am) (?:happy|sad|tired|feeling)|i (?:had|went|ate|drank|slept|woke)|my (?:day|weekend|family|coffee))\b", text.replace('’', "'"), re.I):
            raise ValueError('Check-in replies must not invent human experiences')
        return text.strip()


def routine_checkin_reply(text: str) -> CheckinReply | None:
    """Only complete, ordinary status greetings bypass generation; richer replies do not."""
    normalized = text.lower().replace('’', "'")
    status = re.sub(r'[.!?,;]+', ' ', _RECIPROCAL.sub('', normalized))
    status = ' '.join(status.split())
    if not re.fullmatch(
            r"(?:(?:i'm|i am) )?(?:(?:doing|feeling) )?(?:(?:very|really|pretty|quite) )?"
            r"(?:good|well|fine|great|okay|ok|not (?:so )?(?:good|well|great)|tired|stressed|sad)"
            r"(?: today)?(?: thanks| thank you)?", status):
        return None
    return CheckinReply(text=social_response(text).strip())


def compose_checkin_reply(text: str, reply: CheckinReply) -> str:
    """Guarantee safe reciprocity without a second model call or extending small talk."""
    response = reply.text
    if reciprocal_checkin(text) and not (
            re.search(r"\b(?:(?:thanks|thank you) for asking|(?:doing|i am|i'm) (?:well|good|fine)|all good|can't complain)\b", response.replace("’", "'"), re.I)):
        # Keep a complete acknowledgment sentence; do not truncate a label or word.
        first = re.split(r'(?<=[.!])\s+', response, maxsplit=1)[0]
        if not first.endswith(('.', '!')):
            first += '.'
        try:
            response = CheckinReply(text=first + ' ' + RECIPROCAL_REPLY).text
        except ValueError:
            response = social_response(text).strip()
    return response


def public_topic(content: dict) -> str | None:
    """Expose only a short scenario label, never an answer-shaped topic."""
    topic = safe_label(content['topic'])
    if not topic or len(topic.split()) > 8 or len(topic) > 100 or re.search(
            r'\b(is|was|were|will|would|because|must|should)\b', topic, re.I):
        return None
    answers = [content['main_idea'], *content['important_details'], *content['inference_points']]
    if any(answer.strip().rstrip('.!').casefold() in topic.casefold() for answer in answers):
        return None
    return topic

def introduction(lesson: Lesson) -> str:
    return ('Let’s listen to the first recording. Take your time, then tell me what you understood.'
            if lesson.exercises_completed == 0 else
            'Now, let’s try another recording. Listen for what matters, then tell me what you understood.')

def evidence_gaps(evaluation: dict, content: dict | None = None) -> list[tuple[str, str]]:
    """Final confirmed errors/partial meaning only, including resolved follow-ups."""
    gaps = []
    if content:
        from app.evaluation import expected_units
        from app.models import ExerciseContent
        expected = expected_units(ExerciseContent.model_validate(content))
        for unit in sorted(evaluation.get('units', []), key=lambda unit: unit['status'] != 'misunderstood'):
            if unit['status'] in ('misunderstood', 'partially_demonstrated', 'partial'):
                label = safe_label(expected[unit['dimension']][unit['index']])
                if label:
                    gaps.append((label, unit['dimension']))
    for label in evaluation.get('misunderstood', []):
        safe = safe_label(label)
        if safe and safe not in [item[0] for item in gaps]:
            gaps.append((safe, 'details'))
    return gaps

def evidence_tip(label: str, dimension: str) -> str:
    value = label.lower()
    if re.search(r'\b(deadline|friday|monday|tuesday|wednesday|thursday|saturday|sunday|postponed|date|time)\b', value):
        return ('Connect the changed plan with its new timing.' if re.search(r'\b(changed|postponed|delayed|new)\b',value)
                else 'Keep the event and its stated timing together.')
    if re.search(r'\b(price|cost|paid|amount|discount)\b', value):
        return 'Separate the original amount from the final cost.'
    if re.search(r'\b(before|after|first|then|next)\b', value):
        return 'Follow the order of events as the speaker describes them.'
    if re.search(r'\b(because|reason|caused|removed|prevent)\b', value):
        return 'Connect what happened with the reason behind it.'
    if dimension == 'vocabulary':
        return 'Use the surrounding sentence to check that word’s meaning.'
    # The complete preceding label supplies the specific target for this action.
    return 'Compare that part of the recording with your understanding.'

def compose_closing(lesson: Lesson, evaluations, topics=(), contents=()) -> None:
    strength, gap = observations(evaluations)
    lesson.exercises_completed = len(evaluations)
    lesson.strength = strength
    from app.evaluation import expected_units
    from app.models import ExerciseContent
    demonstrated = []
    for evaluation, content in zip(evaluations, contents):
        expected = expected_units(ExerciseContent.model_validate(content))
        demonstrated.extend(label for unit in evaluation.get('units', [])
            if unit['status'] in ('demonstrated', 'understood')
            and (label := safe_label(expected[unit['dimension']][unit['index']])))
    gaps = [item for evaluation, content in reversed(list(zip(evaluations, contents)))
            for item in evidence_gaps(evaluation, content)]
    # Prefer a recent specific error over a dimension-only focus.
    specific = next(iter(gaps), None)
    lesson.improvement_focus = specific[0] if specific else FOCUS[gap] if gap else None
    topic = ' and '.join(list(dict.fromkeys(label for topic in topics if (label := safe_label(topic))))[:2]) or None
    opening = ('Today, we listened to ' + topic + ' and practiced ' + FOCUS[lesson.focus] + '.') if evaluations and topic else 'Today, we practiced ' + FOCUS[lesson.focus] + '.' if evaluations else 'We made time for listening today.'
    observed = (' You understood that ' + demonstrated[0][0].lower() + demonstrated[0][1:] + '.') if demonstrated else (
        f' You demonstrated strength in {strength}.' if strength else ' We’ll keep gathering listening evidence next time.')
    guidance = (' One part to revisit is that ' + specific[0][0].lower() + specific[0][1:] + '. '
                + 'Next time, replay that section once to check this meaning.') if specific else (
                ' A useful next focus is ' + FOCUS[gap] + '.' if gap else
                ' Keep building on this listening practice.' if strength else '')
    lesson.closing = opening + observed + guidance + ' Thanks for listening with me; we’ll build from here.'

def heard(lesson: Lesson, at: datetime) -> Lesson:
    result = lesson.model_copy(deep=True)
    transitions = {'WELCOME_ACK': 'REVIEW' if lesson.previous else 'TRANSITION', 'REVIEW_ACK': 'TRANSITION', 'TRANSITION': 'EXERCISE'}
    if lesson.phase == 'CLOSING':
        result.phase = 'COMPLETED'
        result.status = 'completed'
        result.completed_at = at
        result.elapsed_seconds = elapsed(lesson, at)
        result.active_since = None
    elif lesson.phase in transitions:
        result.phase = transitions[lesson.phase]
    else:
        raise ValueError('This phase requires a spoken response or a completed exercise.')
    return result
