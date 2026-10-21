"""Request-time enforcement of the daily lesson limit (AUD-47).

off: every method returns immediately (production behavior unchanged).
observe: decisions are computed and logged as would_refuse; nothing is ever blocked or mutated.
enforce: refusals raise LimitRefused, rendered as a structured HTTP 429 (409 when no cooldown applies).
Cached audio is never gated: replay and review make no provider calls.
"""
import logging
from app.lesson import now
from app.lesson_limit import LimitRefused, window_phase, grace_end, deadline, WINDOW, GRACE_ACTION_LIMIT
from app.storage.repository import learner_tag

LOG = logging.getLogger('uvicorn.error')
OPEN = ('active', 'paused')


class LessonLimits:
    def __init__(self, repo, settings, clock=None):
        self.repo, self.settings, self.clock = repo, settings, clock or now

    @property
    def mode(self):
        return self.settings.lesson_limit_mode

    def _apply(self, refusal, where):
        if self.mode == 'enforce':
            raise refusal
        LOG.info('lesson_limit would_refuse code=%s where=%s learner=%s', refusal.code, where, learner_tag(self.repo.learner_id))

    def _next(self, at, lesson=None):
        return self.repo.cooldown_until(at) or (lesson.started_at + WINDOW if lesson is not None else at)

    def _grace_slot(self, lesson, at, code):
        """Wrap-up work after the deadline is a bounded closing sequence, not a way to keep practicing.

        The count lives in the ledger row for THIS lesson (shared by every worker, restart-proof) and is taken in one
        atomic statement on admission: a failed or cancelled provider call still counts, and a request from any other
        lesson matches no row. Observe mode never takes slots (it must not change learner state)."""
        if self.mode != 'enforce' or at < deadline(lesson):
            return None
        if self.repo.take_grace_slot(lesson.id, GRACE_ACTION_LIMIT, at) is None:
            return LimitRefused(code, at, self._next(at, lesson))
        return None

    def _refusal(self, purpose, at, lesson):
        lesson = lesson or self.repo.lesson()
        if lesson is not None and lesson.status in OPEN:
            phase = window_phase(lesson, at)
            if phase == 'open':
                return None
            if phase == 'closing' and purpose == 'wrapup':
                return self._grace_slot(lesson, at, 'lesson_time_expired')
            return LimitRefused('lesson_time_expired', at, self._next(at, lesson))
        if lesson is not None:  # An explicitly addressed completed or expired lesson.
            if lesson.status == 'completed' and purpose == 'wrapup' and at < grace_end(lesson):
                return self._grace_slot(lesson, at, 'lesson_cooldown')
            return LimitRefused('lesson_cooldown', at, self._next(at, lesson))
        until = self.repo.cooldown_until(at)
        if until is None:
            return None  # Before the first lesson or after the window: onboarding and the next start stay open.
        latest = self.repo.latest_lesson()
        if purpose == 'wrapup' and latest is not None and latest.status == 'completed' and at < grace_end(latest):
            return self._grace_slot(latest, at, 'lesson_cooldown')
        return LimitRefused('lesson_cooldown', at, until)

    def admit(self, purpose, where, lesson=None):
        """Gate one provider-backed action (STT, LLM, TTS cache miss, generation) for this learner."""
        if self.mode == 'off':
            return
        at = self.clock()
        refusal = self._refusal(purpose, at, lesson)
        if refusal is not None:
            self._apply(refusal, where)

    def admit_exercise(self, where, exercise_id):
        """Exercise-scoped practice: needs the open lesson that owns the exercise; legacy lesson-free use is refused."""
        if self.mode == 'off':
            return
        lesson = self.repo.lesson_for_exercise(exercise_id)
        if lesson is not None and lesson.status in OPEN:
            return self.admit('practice', where, lesson)
        self.admit_legacy(where)

    def admit_legacy(self, where):
        """Lesson-free generation and assessment: only inside an open lesson, never during cooldown."""
        if self.mode == 'off':
            return
        at = self.clock()
        if self.repo.lesson() is not None:
            return self.admit('practice', where)
        until = self.repo.cooldown_until(at)
        self._apply(LimitRefused('lesson_cooldown', at, until) if until else LimitRefused('lesson_required', at), where)

    def block_onboarding_restart(self):
        if self.mode == 'off':
            return
        at = self.clock()
        until = self.repo.cooldown_until(at)
        if until is not None:
            self._apply(LimitRefused('lesson_cooldown', at, until), 'onboarding.restart')

    def restart_blocked_until(self):
        """Enforce mode: when an onboarding restart becomes available again (None = available now)."""
        return self.repo.cooldown_until(self.clock()) if self.mode == 'enforce' else None

    def past_deadline(self, lesson):
        """True once the lesson's 10 wall-clock minutes are over: no further follow-up questions."""
        return self.mode == 'enforce' and lesson is not None and lesson.status in OPEN and self.clock() >= deadline(lesson)
