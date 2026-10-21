"""Daily lesson allowance policy (AUD-47). Pure functions; the repository owns persistence and atomicity."""
from datetime import datetime, timedelta
from typing import Literal
from app.models import StrictModel

WINDOW = timedelta(hours=24)
LESSON_MAX_SECONDS = 600
# Provider-backed wrap-up actions allowed per lesson after its deadline (closing speech, feedback, farewell, homework, planner).
GRACE_ACTION_LIMIT = 12
Mode = Literal['off', 'observe', 'enforce']


class LessonCooldown(Exception):
    """A new lesson was refused because the rolling window is still open."""
    def __init__(self, next_eligible_at: datetime):
        super().__init__('lesson_cooldown')
        self.next_eligible_at = next_eligible_at


class OpenLesson(StrictModel):
    id: str
    status: Literal['active', 'paused']
    started_at: datetime
    expires_at: datetime
    grace_ends_at: datetime


class Eligibility(StrictModel):
    mode: Mode
    # True only in enforce mode. Observe and off never block, so clients must not gate on those.
    enforced: bool = False
    eligible: bool
    reason: Literal['eligible', 'cooldown', 'lesson_in_progress']
    server_time: datetime
    next_available_at: datetime | None = None
    retry_after_seconds: int | None = None
    last_started_at: datetime | None = None
    open_lesson: OpenLesson | None = None
    lesson_max_seconds: int = LESSON_MAX_SECONDS


def evaluate(mode: Mode, at: datetime, next_eligible_at: datetime | None, last_started_at: datetime | None,
             open_lesson: OpenLesson | None) -> Eligibility:
    """`at` is always server time. A learner with an open lesson resumes it; start never creates a second."""
    cooling = next_eligible_at is not None and at < next_eligible_at
    common = dict(mode=mode, enforced=mode == 'enforce', server_time=at, last_started_at=last_started_at, open_lesson=open_lesson)
    if open_lesson is not None:
        return Eligibility(eligible=False, reason='lesson_in_progress', **common)
    if cooling:
        return Eligibility(eligible=False, reason='cooldown', next_available_at=next_eligible_at,
                           retry_after_seconds=max(1, int((next_eligible_at - at).total_seconds() + .999)), **common)
    return Eligibility(eligible=True, reason='eligible', **common)


GRACE_SECONDS = 180
Purpose = Literal['practice', 'wrapup']


def deadline(lesson) -> datetime:
    return lesson.started_at + timedelta(seconds=LESSON_MAX_SECONDS)


def grace_end(lesson) -> datetime:
    return deadline(lesson) + timedelta(seconds=GRACE_SECONDS)


def window_phase(lesson, at: datetime) -> Literal['open', 'closing', 'over']:
    """Wall-clock position inside the lesson: new practice only while open; closing sequence during grace."""
    if at < deadline(lesson):
        return 'open'
    return 'closing' if at < grace_end(lesson) else 'over'


MESSAGES = {
    'lesson_cooldown': 'You’ve finished today’s listening lesson. Your next one opens soon; your saved lessons, feedback and progress are still here.',
    'lesson_time_expired': 'Today’s 10-minute lesson time is up. Your saved work is safe, and your next lesson opens soon.',
    'lesson_required': 'Start your daily lesson to practice with Audli.',
}


class LimitRefused(Exception):
    """Refusal rendered as a structured HTTP 429 (or 409 when no cooldown applies)."""
    def __init__(self, code: str, at: datetime, next_available_at: datetime | None = None):
        super().__init__(code)
        self.code, self.server_time, self.next_available_at = code, at, next_available_at

    @property
    def status_code(self) -> int:
        return 429 if self.next_available_at is not None else 409

    def retry_after(self) -> int | None:
        if self.next_available_at is None:
            return None
        return max(1, int((self.next_available_at - self.server_time).total_seconds() + .999))

    def body(self) -> dict:
        iso = lambda value: value.isoformat().replace('+00:00', 'Z')  # Same shape as every other server timestamp.
        return {'detail': MESSAGES[self.code], 'error': {'code': self.code, 'server_time': iso(self.server_time),
            'next_available_at': iso(self.next_available_at) if self.next_available_at else None,
            'retry_after_seconds': self.retry_after()}}
