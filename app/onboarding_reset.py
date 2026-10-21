"""Authenticated owner-scoped onboarding reset; preserves accounts and history."""
from typing import Literal
from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from app.models import LearnerProfile, StrictModel
from app.storage import schema as s
from app.storage.repository import profile_values


class ResetOnboarding(StrictModel):
    confirmation: Literal['RESTART ONBOARDING']
    revision: int = Field(ge=0)


RESET_FIELDS = ('name', 'target_language', 'goal', 'interests', 'target_situations',
                'accent_preferences', 'onboarding_status', 'onboarding', 'initial_listening_profile')
RESET_DESCRIPTION = ('Your preferred name, English language choice, listening goal, interests, target situations, '
                     'accent preferences, initial onboarding snapshot, and all onboarding answers and checkpoints will reset. '
                     'Your account, lessons, attempts, learning scores, difficulty, and accessibility settings stay saved.')


def reset_onboarding(repo, revision):
    with repo.transaction() as db:
        row = db.execute(select(s.profiles).where(s.profiles.c.user_id == repo.learner_id)
                         .with_for_update()).mappings().one()
        profile = LearnerProfile.model_validate(row['data'])
        if profile.onboarding.revision != revision:
            # Retry of the same reset is safe until the new onboarding advances.
            if (profile.onboarding.revision == revision + 1
                    and profile.onboarding_status == 'not_started'):
                return {'destination': 'onboarding', 'revision': profile.onboarding.revision}
            raise HTTPException(409, 'Onboarding changed. Reload before restarting.')
        # Discard only unevaluated work. Existing request leases serialize this
        # transaction with provider-backed saves across authenticated API workers.
        # Removed IDs make delayed callbacks fail ownership/existence checks.
        unfinished = list(db.execute(select(s.exercises.c.id, s.exercises.c.session_id)
            .where(s.exercises.c.user_id == repo.learner_id,
                ~select(s.attempts.c.id).where(s.attempts.c.exercise_id == s.exercises.c.id,
                    s.attempts.c.status == 'evaluated').exists())).all())
        ids = [item.id for item in unfinished]
        if ids:
            for table in (s.evidence, s.turns, s.assessments, s.conversations,
                          s.followups, s.coach_audio, s.lesson_exercises, s.attempts):
                db.execute(table.delete().where(table.c.exercise_id.in_(ids)))
            db.execute(s.exercises.delete().where(s.exercises.c.id.in_(ids),
                s.exercises.c.user_id == repo.learner_id))
            sessions = [item.session_id for item in unfinished if item.session_id]
            db.execute(s.sessions.delete().where(s.sessions.c.id.in_(sessions),
                s.sessions.c.user_id == repo.learner_id, s.sessions.c.status == 'active',
                ~select(s.exercises.c.id).where(s.exercises.c.session_id == s.sessions.c.id).exists()))
        open_lessons = select(s.lessons.c.id).where(s.lessons.c.user_id == repo.learner_id,
            s.lessons.c.status.in_(s.OPEN_LESSON_STATUSES))
        db.execute(s.lesson_exercises.delete().where(s.lesson_exercises.c.user_id == repo.learner_id,
            s.lesson_exercises.c.lesson_id.in_(open_lessons)))
        db.execute(s.lessons.delete().where(s.lessons.c.user_id == repo.learner_id,
            s.lessons.c.status.in_(s.OPEN_LESSON_STATUSES)))
        defaults = LearnerProfile()
        for field in RESET_FIELDS:
            setattr(profile, field, getattr(defaults, field))
        # Do not reuse question/revision tokens from any previous onboarding run.
        profile.onboarding.welcome = 'introduction'
        profile.onboarding.revision = revision + 1
        profile.onboarding.name_question_revision = revision + 1
        db.execute(s.profiles.update().where(s.profiles.c.user_id == repo.learner_id)
                   .values(**profile_values(profile)))
    return {'destination': 'onboarding', 'revision': profile.onboarding.revision}


def register_onboarding_reset_routes(app, repo, lock):
    @app.get('/api/onboarding/reset-controls')
    async def controls():
        return {'restart_available': True, 'reset_description': RESET_DESCRIPTION,
                'onboarding_revision': repo.profile().onboarding.revision}

    @app.post('/api/onboarding/restart')
    async def restart(body: ResetOnboarding):
        async with lock:
            return reset_onboarding(repo, body.revision)
