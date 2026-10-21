"""Daily lesson allowance policy (AUD-47). Pure functions; the repository owns persistence and atomicity."""
from datetime import datetime, timedelta
from typing import Literal
from app.models import StrictModel

WINDOW = timedelta(hours=24)
LESSON_MAX_SECONDS = 600
# Provider-backed wrap-up actions allowed per lesson after its deadline (closing speech, feedback, farewell, homework, planner).
GRACE_ACTION_LIMIT = 12
Mode = Literal['off', 'observe']


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


class Eligibility(StrictModel):
    mode: Mode
    # Phase 2 never blocks; clients must not gate the classroom on this until enforcement ships.
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
    common = dict(mode=mode, server_time=at, last_started_at=last_started_at, open_lesson=open_lesson)
    if open_lesson is not None:
        return Eligibility(eligible=False, reason='lesson_in_progress', **common)
    if cooling:
        return Eligibility(eligible=False, reason='cooldown', next_available_at=next_eligible_at,
                           retry_after_seconds=max(1, int((next_eligible_at - at).total_seconds() + .999)), **common)
    return Eligibility(eligible=True, reason='eligible', **common)
