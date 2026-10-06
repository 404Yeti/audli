"""Supabase Auth verification and isolated request-scoped repository identities."""
from contextvars import ContextVar
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


class LearnerAuthentication:
    def __init__(self, app, identity, repository, request_repository):
        self.app, self.identity = app, identity
        self.repository, self.request_repository = repository, request_repository

    async def __call__(self, scope, receive, send):
        public = scope.get('path') in ('/api/health', '/api/auth/config')
        if scope['type'] != 'http' or public or not scope.get('path', '').startswith('/api'):
            return await self.app(scope, receive, send)
        try:
            learner_id = await self.identity.verify(scope['headers'])
        except HTTPException as error:
            headers = {'WWW-Authenticate': 'Bearer'} if error.status_code == 401 else {}
            return await JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=headers)(scope, receive, send)
        # Database initialization is idempotent and transactional. Do not create users
        # until the Auth server has validated the token; no browser UUID is accepted.
        scoped = self.repository.for_learner(learner_id)
        token = self.request_repository.current.set(scoped)
        try:
            await self.app(scope, receive, send)
        finally:
            self.request_repository.current.reset(token)
