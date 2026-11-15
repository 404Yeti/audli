"""Support diagnostics for the 2.0 listening API (AUD-120).

Every response under /api/listening/ (except signed media links) carries ``X-Audli-Reference``, a random per-request id the learner
can quote to support. Responses with status >= 400 also write ONE structured log line: reference, method, route template (never the
concrete URL), status and a safe error classification. Nothing else is logged: no tokens, bodies, signed URLs, request ids,
lesson content, or learner identifiers beyond the existing non-reversible ``learner_tag``.
"""
import logging
from hashlib import sha256
from uuid import uuid4

from starlette.datastructures import MutableHeaders

LOG = logging.getLogger('audli.listening.diagnostics')
REFERENCE_HEADER = 'X-Audli-Reference'
ERROR_CODE_HEADER = 'X-Audli-Error-Code'
PREFIX = '/api/listening/'
SKIPPED = ('/api/listening/media/', '/api/listening/ops/')          # signed media carries its own credential; ops has its own bearer check


def short_hash(value: str) -> str:
    """A non-reversible 8-character label, used to tell two request ids apart in logs without logging either."""
    return sha256(value.encode()).hexdigest()[:8]


def classify(status: int, code: str | None) -> str:
    """A closed vocabulary, so a log line can never carry free text from a request or a learner."""
    if code:
        return code
    if status == 401:
        return 'unauthenticated'
    if status == 403:
        return 'forbidden'
    if status == 404:
        return 'not_found'
    if status == 413:
        return 'too_large'
    if status == 422:
        return 'invalid'
    if status == 429:
        return 'rate_limited'
    if status == 503:
        return 'unavailable'
    return 'server_error' if status >= 500 else 'client_error'


class ListeningDiagnostics:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not path.startswith(PREFIX) or path.startswith(SKIPPED):
            return await self.app(scope, receive, send)
        reference = uuid4().hex[:12]
        scope.setdefault('state', {})['audli_reference'] = reference

        async def send_with_reference(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers[REFERENCE_HEADER] = reference
                status = message['status']
                if status >= 400:
                    route = getattr(scope.get('route'), 'path', None) or 'unmatched'
                    LOG.warning('listening_error ref=%s method=%s route=%s status=%s kind=%s', reference, scope['method'], route, status,
                                classify(status, headers.get(ERROR_CODE_HEADER)))
            await send(message)
        await self.app(scope, receive, send_with_reference)


def reference_of(request) -> str:
    return getattr(request.state, 'audli_reference', None) or 'unavailable'
