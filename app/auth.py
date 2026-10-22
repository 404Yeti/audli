"""Supabase Auth verification and isolated request-scoped repository identities."""
from contextvars import ContextVar
import asyncio
import re
from hashlib import sha256
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from uuid import UUID

import httpx
from fastapi import HTTPException
from starlette.responses import JSONResponse


class RequestRepository:
    """Existing route closures delegate to this request's verified learner scope."""
    def __init__(self, local_repository=None):
        self.current = ContextVar('audli_request_repository', default=local_repository)

    def __getattr__(self, name):
        repository = self.current.get()
        if repository is None:
            raise RuntimeError('Learner repository accessed outside an authenticated request')
        return getattr(repository, name)


class SupabaseIdentity:
    def __init__(self, settings, client=None):
        self.url = settings.supabase_url + '/auth/v1/user'
        self.key = settings.supabase_publishable_key.get_secret_value()
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), follow_redirects=False)

    async def verify(self, headers):
        authorization = [value for key, value in headers if key.lower() == b'authorization']
        if len(authorization) != 1:
            raise HTTPException(401, 'Sign in to continue.')
        try:
            scheme, token = authorization[0].decode('ascii').split(' ', 1)
            if scheme.lower() != 'bearer' or not token or len(token) > 8192 or any(char.isspace() for char in token):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise HTTPException(401, 'Sign in to continue.') from None
        try:
            response = await self.client.get(self.url, headers={'apikey': self.key, 'Authorization': 'Bearer ' + token})
        except httpx.HTTPError:
            raise HTTPException(503, 'Sign-in verification is unavailable. Please retry.') from None
        if response.status_code in (400, 401, 403):
            raise HTTPException(401, 'Your session expired or is invalid. Please sign in again.')
        if response.status_code != 200:
            raise HTTPException(503, 'Sign-in verification is unavailable. Please retry.')
        try:
            user = response.json()
            # Anonymous accounts are not an account-backed learner identity.
            if user.get('is_anonymous') or not user.get('email_confirmed_at'):
                raise ValueError()
            return str(UUID(user['id']))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(401, 'Your session expired or is invalid. Please sign in again.') from None

    async def close(self):
        await self.client.aclose()


# Authenticated, but never reads or writes learner state, so it must not queue for (or churn
# connections polling) the per-account save lease while a real step is in flight (AUD-68).
LEASE_EXEMPT_PATHS = frozenset({'/api/telemetry/turn'})
# AUD-77: advisory Review coaching calls a provider for seconds. Its handler takes only short, atomic, owner-scoped
# transactions of its own (claim, completion), so it must not hold the account-wide lease across that call.
# Exactly one method and one path shape, lowercase canonical UUID; authentication has already succeeded when this applies.
LEASE_EXEMPT_ROUTES = (('POST', re.compile(r'/api/exercises/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/review/improvement')),)


def lease_exempt(method: str, path: str) -> bool:
    return path in LEASE_EXEMPT_PATHS or any(method == verb and pattern.fullmatch(path) for verb, pattern in LEASE_EXEMPT_ROUTES)


class LearnerAuthentication:
    def __init__(self, app, identity, repository, request_repository):
        self.app, self.identity = app, identity
        self.repository, self.request_repository = repository, request_repository
        # Mutations share an owner-specific lease across API workers, including
        # provider awaits. Reset cannot run while another worker saves a lesson.
        self.lease_engine = (create_engine(repository.engine.url, poolclass=NullPool,
            connect_args={'connect_timeout': 5, 'prepare_threshold': None})
            if repository.engine.dialect.name == 'postgresql' else None)

    async def __call__(self, scope, receive, send):
        public = scope.get('path') in ('/api/health', '/api/auth/config')
        if scope['type'] != 'http' or public or not scope.get('path', '').startswith('/api'):
            return await self.app(scope, receive, send)
        try:
            learner_id = await self.identity.verify(scope['headers'])
        except HTTPException as error:
            headers = {'WWW-Authenticate': 'Bearer'} if error.status_code == 401 else {}
            return await JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=headers)(scope, receive, send)
        lease = None
        token = None
        try:
            if (self.lease_engine is not None and scope['method'] not in ('GET', 'HEAD', 'OPTIONS')
                    and not lease_exempt(scope['method'], scope['path'])):
                key = int.from_bytes(sha256(('audli-account:' + learner_id).encode()).digest()[:8], 'big', signed=True)
                for _ in range(100):
                    candidate = self.lease_engine.connect()
                    try:
                        candidate.execute(text("SELECT set_config('idle_in_transaction_session_timeout', '0', true)"))
                        acquired = candidate.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}).scalar_one()
                    except BaseException:
                        candidate.close()
                        raise
                    if acquired:
                        lease = candidate
                        break
                    candidate.close()
                    await asyncio.sleep(.05)
                if lease is None:
                    raise HTTPException(409, 'Your account is saving another step. Wait and retry.')
            # Identity still comes only from the Auth-verified bearer.
            scoped = self.repository.for_learner(learner_id)
            token = self.request_repository.current.set(scoped)
            await self.app(scope, receive, send)
        except HTTPException as error:
            await JSONResponse({'detail': error.detail}, status_code=error.status_code)(scope, receive, send)
        finally:
            if token is not None:
                self.request_repository.current.reset(token)
            if lease is not None:
                lease.close()
