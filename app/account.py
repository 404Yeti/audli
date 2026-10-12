"""Account operations: authenticated owner only; no browser administrative credentials."""
from hashlib import sha256
import base64
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4
from uuid import UUID

import httpx
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr
from sqlalchemy import select, delete, text

from app.models import StrictModel, LearnerProfile
from app.storage import schema as s
from app.storage.repository import profile_values, utcnow, aware


class DeleteAccount(StrictModel):
    confirmation: Literal['DELETE MY ACCOUNT']
    password: SecretStr = Field(min_length=1, max_length=1024)


class ResetOnboarding(StrictModel):
    confirmation: Literal['RESTART ONBOARDING']
    revision: int = Field(ge=0)


RESET_FIELDS = ('name', 'target_language', 'goal', 'interests', 'target_situations',
                'accent_preferences', 'onboarding_status', 'onboarding', 'initial_listening_profile')
RESET_DESCRIPTION = ('Your preferred name, English language choice, listening goal, interests, target situations, '
                     'accent preferences, initial onboarding snapshot, and all onboarding answers and checkpoints will reset. '
                     'Your account, lessons, attempts, learning scores, difficulty, and accessibility settings stay saved.')
MFA_SUPPORT = ('Self-service deletion does not support multi-factor authentication yet. '
               'Keep MFA enabled and contact robbie@audli.ai to request account deletion. '
               'Support must verify your identity with MFA; never send your password or recovery codes.')


def require_live_session(repo, request):
    """Bind the already Auth-verified JWT to a still-live, owned session."""
    if repo.engine.dialect.name != 'postgresql':
        return
    try:
        payload = request.headers['authorization'].split(' ', 1)[1].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        session_id = str(UUID(claims['session_id']))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise HTTPException(401, 'Sign in again before deleting your account.') from None
    with repo.engine.connect() as db:
        if not db.execute(text('SELECT id FROM auth.sessions WHERE id = :session AND user_id = :owner'),
                          {'session': session_id, 'owner': repo.learner_id}).first():
            raise HTTPException(401, 'Sign in again before deleting your account.')


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


def erase_application_data(db, owner):
    """Explicit child-first cleanup of the inspected schema in the caller's transaction."""
    for lesson in db.execute(select(s.lessons.c.id, s.lessons.c.data).where(s.lessons.c.user_id == owner)).mappings():
        for number in range(lesson['data']['exercises_completed'] + 1):
            scope = sha256(f"{owner}:{lesson['id']}:{number}".encode()).hexdigest()
            reservation = db.execute(select(s.accent_reservations).where(s.accent_reservations.c.scope == scope)).mappings().first()
            if reservation and reservation['state'] != 'success' and aware(reservation['lease_until']) > utcnow():
                # A timed-out provider may still be running. Break the owner link
                # while preserving its remaining concurrency slot and spent budget.
                db.execute(s.accent_reservations.update().where(s.accent_reservations.c.scope == scope)
                           .values(scope=sha256(uuid4().bytes).hexdigest()))
            else:
                db.execute(delete(s.accent_reservations).where(s.accent_reservations.c.scope == scope))
    exercises = select(s.exercises.c.id).where(s.exercises.c.user_id == owner)
    attempts = select(s.attempts.c.id).where(s.attempts.c.exercise_id.in_(exercises))
    for table, predicate in (
        (s.evidence, s.evidence.c.exercise_id.in_(exercises)),
        (s.turns, s.turns.c.exercise_id.in_(exercises)),
        (s.assessments, s.assessments.c.exercise_id.in_(exercises)),
        (s.events, s.events.c.attempt_id.in_(attempts)),
        (s.evaluations, s.evaluations.c.attempt_id.in_(attempts)),
        (s.conversations, s.conversations.c.exercise_id.in_(exercises)),
        (s.followups, s.followups.c.exercise_id.in_(exercises)),
        (s.coach_audio, s.coach_audio.c.exercise_id.in_(exercises)),
        (s.lesson_exercises, s.lesson_exercises.c.user_id == owner),
        (s.attempts, s.attempts.c.id.in_(attempts)),
        (s.exercises, s.exercises.c.user_id == owner),
        (s.lessons, s.lessons.c.user_id == owner),
        (s.sessions, s.sessions.c.user_id == owner),
        (s.audio_assets, s.audio_assets.c.user_id == owner),
        (s.profiles, s.profiles.c.user_id == owner),
        (s.users, s.users.c.id == owner),
    ):
        db.execute(delete(table).where(predicate))
    # Aggregate budget counters survive deletion to enforce spending caps.


def delete_account(repo, audio_dir):
    if repo.engine.dialect.name != 'postgresql':
        raise HTTPException(503, 'Account deletion requires the trusted PostgreSQL backend.')
    with repo.transaction() as db:
        owner = repo.learner_id
        # Storage currently has no buckets/objects. Fail closed if that architecture
        # changes: SQL deletion of metadata would leave physical objects behind.
        if db.execute(text('SELECT EXISTS(SELECT 1 FROM storage.buckets)')).scalar_one():
            raise HTTPException(503, 'Account deletion needs a storage review. Contact Audli support; your account has not been deleted.')
        # Serialize against Auth-side session creation/update while deleting.
        if not db.execute(text('SELECT id FROM auth.users WHERE id = :owner FOR UPDATE'), {'owner': owner}).first():
            raise HTTPException(401, 'Your account is no longer available. Please sign out.')
        names = db.execute(select(s.audio_assets.c.audio_name).where(s.audio_assets.c.user_id == owner)).scalars().all()
        names += db.execute(select(s.exercises.c.audio_name).where(s.exercises.c.user_id == owner)).scalars().all()
        names += db.execute(select(s.coach_audio.c.audio_name).join(s.exercises)
                            .where(s.exercises.c.user_id == owner)).scalars().all()
        foreign_assets = db.execute(select(s.audio_assets.c.audio_name).where(s.audio_assets.c.audio_name.in_(names),
                          s.audio_assets.c.user_id != owner)).first()
        foreign_exercises = db.execute(select(s.exercises.c.audio_name).where(s.exercises.c.audio_name.in_(names),
                          s.exercises.c.user_id != owner)).first()
        foreign_coaching = db.execute(select(s.coach_audio.c.audio_name).join(s.exercises)
                          .where(s.coach_audio.c.audio_name.in_(names), s.exercises.c.user_id != owner)).first()
        if foreign_assets or foreign_exercises or foreign_coaching:
            raise HTTPException(503, 'Audio ownership requires review. Your account has not been deleted.')
        root = Path(audio_dir).resolve()
        for name in set(names):
            if not name or Path(name).name != name or (root / name).is_symlink():
                raise HTTPException(503, 'Audio cleanup requires support. Your account has not been deleted.')
            # Cache removal can be retried; DB blobs remain available if rollback occurs.
            (root / name).unlink(missing_ok=True)
        erase_application_data(db, owner)
        db.execute(text('DELETE FROM auth.refresh_tokens WHERE user_id = :owner'), {'owner': owner})
        db.execute(text('DELETE FROM auth.sessions WHERE user_id = :owner'), {'owner': owner})
        # Auth audit records are operational security records, not lesson history.
        # Remove attributable flow state and SCIM mappings (which otherwise SET NULL).
        db.execute(text('DELETE FROM auth.flow_state WHERE user_id = :owner'), {'owner': owner})
        db.execute(text('DELETE FROM auth.scim_users WHERE user_id = :owner'), {'owner': owner})
        result = db.execute(text('DELETE FROM auth.users WHERE id = :owner'), {'owner': owner})
        if result.rowcount != 1:
            raise HTTPException(503, 'Deletion could not be confirmed. Please retry.')
    return {'deleted': True}


async def reauthenticate(identity, request, password, owner):
    bearer = request.headers['authorization']
    try:
        user_response = await identity.client.get(identity.url, headers={'apikey': identity.key, 'Authorization': bearer})
        if user_response.status_code != 200:
            raise HTTPException(401, 'Sign in again before deleting your account.')
        user = user_response.json()
        if user.get('id') != owner:
            raise HTTPException(403, 'Reauthentication did not match your account.')
        if any(f.get('status') == 'verified' for f in user.get('factors', [])):
            raise HTTPException(403, MFA_SUPPORT)
        response = await identity.client.post(identity.url.removesuffix('/user') + '/token?grant_type=password',
            headers={'apikey': identity.key}, json={'email': user.get('email'), 'password': password.get_secret_value()})
        if response.status_code in (400, 401, 403, 422):
            raise HTTPException(403, 'Your password could not be verified. Try again or reset your password.')
        if response.status_code != 200:
            raise HTTPException(503, 'Reauthentication is unavailable. Your account has not been deleted.')
        result = response.json()
        token = result.get('access_token')
        # Never accept credentials that authenticate a different user or MFA at aal1.
        if result.get('user', {}).get('id') != owner or not isinstance(token, str) or not token:
            raise HTTPException(403, 'Reauthentication did not match your account.')
        factors = result.get('user', {}).get('factors', [])
        if any(f.get('status') == 'verified' for f in factors):
            raise HTTPException(403, MFA_SUPPORT)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(503, 'Reauthentication is unavailable. Your account has not been deleted.') from None
    finally:
        # A password check creates a temporary session. Attempt local revocation even if
        # subsequent cleanup fails; it is never returned to/stored by the browser.
        if 'token' in locals() and isinstance(token, str) and token:
            try:
                await identity.client.post(identity.url.removesuffix('/user') + '/logout?scope=local',
                    headers={'apikey': identity.key, 'Authorization': 'Bearer ' + token})
            except httpx.HTTPError:
                pass


def register_account_routes(app, repo, settings, identity, lock, audio_dir):
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if request.url.path == '/api/account/delete':
            # Validation errors must never echo passwords or the submitted body.
            return JSONResponse(status_code=422, content={'detail': 'Confirm permanent deletion and enter your current password.'})
        return await request_validation_exception_handler(request, error)

    def reset_allowed():
        return settings.environment == 'development' or repo.learner_id in {str(value) for value in settings.onboarding_test_accounts}

    @app.get('/api/account/controls')
    async def controls():
        return {'delete_available': identity is not None and settings.persistence == 'postgres',
                'restart_available': reset_allowed(), 'reset_description': RESET_DESCRIPTION,
                'onboarding_revision': repo.profile().onboarding.revision}

    @app.post('/api/account/restart-onboarding')
    async def restart(body: ResetOnboarding):
        if not reset_allowed():
            raise HTTPException(403, 'Onboarding restart is restricted to authorized test accounts.')
        async with lock:
            return reset_onboarding(repo, body.revision)

    @app.post('/api/account/delete')
    async def remove(body: DeleteAccount, request: Request):
        if identity is None:
            raise HTTPException(403, 'Sign in to an account before deleting it.')
        async with lock:
            require_live_session(repo, request)
            await reauthenticate(identity, request, body.password, repo.learner_id)
            try:
                return delete_account(repo, audio_dir)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, 'Deletion could not be confirmed. Your account may still exist; retry or contact support.') from None
