"""Recognition sufficiency for a server-owned check-in question, never evidence."""
from datetime import datetime
from typing import Literal

from app.accent_intent import interpret, is_closed_offer_reply
from app.accent_practice import CONSENT_SECONDS
from app.accents import LABELS
from app.lesson import Lesson
from app.recognition import SHORT_ASSESSMENT_WARNING, reliable_recognition

PendingRecognition = Literal['accepted', 'uncertain']


def pending_recognition(lesson: Lesson, minimum: float, at: datetime) -> PendingRecognition | None:
    """Only a complete choice for a live offer may ignore the length warning.

    No transcript/warning is changed. This verdict authorizes neither consent nor
    progression: /answer recomputes it under the current revision before applying
    the existing intent/availability policy. No new persisted state is needed.
    """
    value = lesson.pending
    if value is None:
        return None
    if lesson.status != 'active' or lesson.phase not in ('WELCOME', 'REVIEW'):
        return 'uncertain'
    if reliable_recognition(value, minimum):
        return 'accepted'
    if (not value.text.strip() or value.confidence is None or value.confidence < minimum
            or not value.uncertainty
            or any(warning != SHORT_ASSESSMENT_WARNING for warning in value.uncertainty)):
        return 'uncertain'
    # An offer is either the unresolved welcome question or the explicit REVIEW
    # confirmation (including its one bounded clarification). Old grants, topic
    # questions and expired lesson windows are never a fresh yes/no question.
    age = (at - lesson.started_at).total_seconds()
    if not 0 <= age < CONSENT_SECONDS or lesson.accent_consent.state != 'none':
        return 'uncertain'
    if lesson.phase == 'WELCOME' and not lesson.accent_resolved and not lesson.accent_discovery:
        context = lesson.accent_offered
    elif lesson.phase == 'REVIEW' and lesson.accent_confirmation:
        context = lesson.accent_consent.accent
        # A bounded clarification asks about this target, which can differ from
        # the original offer (or follow a spontaneous request with no offer).
        # Before clarification, the queued offer and confirmation must agree.
        if not lesson.accent_resolved and context != lesson.accent_offered:
            return 'uncertain'
    else:
        return 'uncertain'
    if context not in LABELS or not is_closed_offer_reply(value.text):
        return 'uncertain'
    parsed = interpret(value.text, offered=context, practice_question=lesson.accent_confirmation)
    return 'accepted' if parsed.kind in ('accept', 'decline') else 'uncertain'
