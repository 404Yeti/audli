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
            raise HTTPException(409, 'Onboarding changed. Reload before restarting.')
        if db.execute(select(s.lessons.c.id).where(s.lessons.c.user_id == repo.learner_id,
                         s.lessons.c.status != 'completed')).first():
            raise HTTPException(409, 'Finish your current lesson before restarting onboarding.')
        exercise = repo.current_exercise()
        if exercise and not repo.completed(exercise['id']):
            raise HTTPException(409, 'Finish your current exercise before restarting onboarding.')
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
