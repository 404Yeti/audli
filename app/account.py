"""Permanent account deletion (AUD-34): authenticated owner only; no browser administrative credentials.

Inert unless AUDLI_ACCOUNT_DELETION_ENABLED is on: without it there is no delete route and the controls report it unavailable.

Which rows are erased is derived from the schema, not from a hand-kept list. Every table in `app.storage.schema.metadata` is
erased unless it is classified in `NOT_LEARNER_OWNED` (shared catalogs, aggregate budgets, the migration ledger). Each table's
ownership predicate follows its foreign keys to `users`, and tables are deleted children first in the metadata's dependency
order. A table that has no ownership path and is not classified is a programming error and fails loudly, so a future table
cannot silently leave learner data behind or make deletion fail on its foreign key. Tables that do not exist in the connected
database (features whose migration is not applied) are skipped.
"""
import base64
import json
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr
from sqlalchemy import delete, inspect, select, text

from app.models import StrictModel
from app.storage import schema as s
from app.storage.repository import aware, utcnow


class DeleteAccount(StrictModel):
    confirmation: Literal['DELETE MY ACCOUNT']
    password: SecretStr = Field(min_length=1, max_length=1024)


MFA_SUPPORT = ('Self-service deletion does not support multi-factor authentication yet. '
               'Keep MFA enabled and contact robbie@audli.ai to request account deletion. '
               'Support must verify your identity with MFA; never send your password or recovery codes.')


def owner_predicate(table, owner, trail=()):
    """SQL selecting this table's rows that belong to `owner`, following foreign keys to `users`; None if there is no path.

    Only NOT NULL foreign keys count as ownership paths: a nullable one (a turn's optional follow-up) would silently skip
    the rows whose value is NULL. Candidates are tried in a fixed order so the plan is deterministic."""
    if table.name == 'users':
        return table.c.id == owner
    if 'user_id' in table.c:
        return table.c.user_id == owner
    for key in sorted(table.foreign_keys, key=lambda item: (item.parent.name, item.column.table.name)):
        parent = key.column.table
        if key.parent.nullable or parent.name in trail or parent.name in s.NOT_LEARNER_OWNED:
            continue
        inner = owner_predicate(parent, owner, trail + (table.name,))
        if inner is not None:
            return key.parent.in_(select(key.column).where(inner))
    return None


def erase_plan(owner):
    """(table, predicate) for every learner-owned table, children before parents."""
    plan = []
    for table in reversed(s.metadata.sorted_tables):
        if table.name in s.NOT_LEARNER_OWNED:
            continue
        predicate = owner_predicate(table, owner)
        if predicate is None:
            raise RuntimeError(f'{table.name} has no ownership path to users: add one or classify it in NOT_LEARNER_OWNED')
        plan.append((table, predicate))
    return plan


def release_accent_reservations(db, owner):
    for lesson in db.execute(select(s.lessons.c.id, s.lessons.c.data).where(s.lessons.c.user_id == owner)).mappings():
        for number in range(lesson['data']['exercises_completed'] + 1):
            scope = sha256(f"{owner}:{lesson['id']}:{number}".encode()).hexdigest()
            reservation = db.execute(select(s.accent_reservations).where(s.accent_reservations.c.scope == scope)).mappings().first()
            if reservation and reservation['state'] != 'success' and aware(reservation['lease_until']) > utcnow():
                # A timed-out provider may still be running. Break the owner link while preserving its remaining
                # concurrency slot and spent budget.
                db.execute(s.accent_reservations.update().where(s.accent_reservations.c.scope == scope)
                           .values(scope=sha256(uuid4().bytes).hexdigest()))
            else:
                db.execute(delete(s.accent_reservations).where(s.accent_reservations.c.scope == scope))


def erase_application_data(db, owner):
    """Erase every learner-owned row in the caller's transaction (see the module docstring). Aggregate counters remain."""
    release_accent_reservations(db, owner)
    present = set(inspect(db).get_table_names())
    for table, predicate in erase_plan(owner):
        if table.name in present:
            db.execute(delete(table).where(predicate))


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


def delete_account(repo, audio_dir):
    if repo.engine.dialect.name != 'postgresql':
        raise HTTPException(503, 'Account deletion requires the trusted PostgreSQL backend.')
    with repo.transaction() as db:
        owner = repo.learner_id
        # Storage currently has no buckets/objects. Fail closed if that architecture changes: SQL deletion of metadata
        # would leave physical objects behind.
        if db.execute(text('SELECT EXISTS(SELECT 1 FROM storage.buckets)')).scalar_one():
            raise HTTPException(503, 'Account deletion needs a storage review. Contact Audli support; your account has not been deleted.')
        # Serialize against Auth-side session creation/update and against first-request initialization (which share-locks
        # this row) while deleting.
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
        # Auth audit records are operational security records, not lesson history. Remove attributable flow state and
        # SCIM mappings (which otherwise SET NULL).
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
        # A password check creates a temporary session. Attempt local revocation even if subsequent cleanup fails; it is
        # never returned to/stored by the browser.
        if 'token' in locals() and isinstance(token, str) and token:
            try:
                await identity.client.post(identity.url.removesuffix('/user') + '/logout?scope=local',
                    headers={'apikey': identity.key, 'Authorization': 'Bearer ' + token})
            except httpx.HTTPError:
                pass


def register_account_routes(app, repo, settings, identity, lock, audio_dir):
    available = settings.account_deletion_enabled and identity is not None and settings.persistence == 'postgres'

    @app.get('/api/account/controls')
    async def controls():
        return {'delete_available': available}

    if not available:
        return

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if request.url.path == '/api/account/delete':
            # Validation errors must never echo passwords or the submitted body.
            return JSONResponse(status_code=422, content={'detail': 'Confirm permanent deletion and enter your current password.'})
        return await request_validation_exception_handler(request, error)

    @app.post('/api/account/delete')
    async def remove(body: DeleteAccount, request: Request):
        async with lock:
            require_live_session(repo, request)
            await reauthenticate(identity, request, body.password, repo.learner_id)
            try:
                return delete_account(repo, audio_dir)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, 'Deletion could not be confirmed. Your account may still exist; retry or contact support.') from None
