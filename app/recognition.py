"""Conservative recognition gate for automatic, unedited spoken answers.

AUD-71 Step A: the warning texts and the reasons a transcription is not trusted now live in one place, with
numeric observability. The accept/reject decision is unchanged: a transcription is reliable only when it has
text, a confidence at or above the minimum, and no uncertainty entry at all.
"""
from enum import Enum
from app.models import Transcription
from app.telemetry import aggregator

# Texts attached by the transcriber (OpenAIProvider.transcribe). Consumers compare against these constants,
# never against retyped strings.
SHORT_REPLY_WARNING = 'Too little recognized speech to evaluate reliably.'
LOW_CONFIDENCE_WARNING = 'Speech recognition was uncertain; please verify or record again.'
NO_CONFIDENCE_WARNING = 'Transcription confidence unavailable; please verify the text.'
MINIMUM_REPLY_WORDS = 3

# Fixed vocabulary for counters: names are code constants, never learner content.
CONTEXTS = frozenset({'exercise.assess', 'lesson.answer', 'planner.checkin', 'onboarding.answer',
                      'homework.review', 'homework.decision'})


class RecognitionIssue(str, Enum):
    EMPTY = 'empty'
    NO_CONFIDENCE = 'no_confidence'
    LOW_CONFIDENCE = 'low_confidence'
    TOO_SHORT = 'too_short'
    OTHER_WARNING = 'other_warning'


def recognition_issues(transcription: Transcription, minimum: float) -> frozenset[RecognitionIssue]:
    """Every reason this transcription is not trusted. Empty set == reliable."""
    issues = set()
    if not transcription.text.strip():
        issues.add(RecognitionIssue.EMPTY)
    if transcription.confidence is None:
        issues.add(RecognitionIssue.NO_CONFIDENCE)
    elif transcription.confidence < minimum:
        issues.add(RecognitionIssue.LOW_CONFIDENCE)
    for warning in transcription.uncertainty:
        issues.add(RecognitionIssue.TOO_SHORT if warning == SHORT_REPLY_WARNING
                   else RecognitionIssue.LOW_CONFIDENCE if warning == LOW_CONFIDENCE_WARNING
                   else RecognitionIssue.NO_CONFIDENCE if warning == NO_CONFIDENCE_WARNING
                   else RecognitionIssue.OTHER_WARNING)
    return frozenset(issues)


def reliable_recognition(transcription: Transcription, minimum: float) -> bool:
    return not recognition_issues(transcription, minimum)


def observe_recognition(settings, context: str, transcription: Transcription, minimum: float, accepted: bool) -> None:
    """Numeric counters only (flag-gated): context x result, and one count per issue present."""
    if not getattr(settings, 'latency_aggregates', False):
        return
    context = context if context in CONTEXTS else 'other'
    aggregator.count(f'recognition.{context}.{"accepted" if accepted else "rejected"}')
    for issue in recognition_issues(transcription, minimum):
        aggregator.count(f'recognition.{context}.issue.{issue.value}')


def check_recognition(settings, context: str, transcription: Transcription, minimum: float) -> bool:
    """``reliable_recognition`` plus counters. Same boolean, no other effect."""
    accepted = reliable_recognition(transcription, minimum)
    observe_recognition(settings, context, transcription, minimum, accepted)
    return accepted
