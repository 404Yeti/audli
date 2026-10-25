"""AUD-89 P0: privacy-safe correlation of audio upload requests (diagnostic only; on with AUDLI_TURN_TRACE).

The question this answers: when a recording is slow or never arrives, is the delay in the phone's upload, in a proxy in front of
the API (Vercel rewrite, Cloudflare), or inside the API? For every ``POST .../attempts`` one ``Upload trace`` line records when
the request was first seen, how many body bytes arrived and when, whether the client went away before the body or the response
completed, and the status. Nothing is read ahead or buffered: this is a pure ASGI pass-through that only counts the messages
that flow through it, so the body guard and the handlers behave exactly as before. A body that arrives in one burst
(``body_ms`` near 0 after a long client-side time) shows a proxy upstream of this process buffered it.

Numbers, route templates, a random upload id and infrastructure request ids only: no audio, text, tokens, URLs with ids, IP
addresses, user agents or account identifiers.
"""
import asyncio
import json
import logging
import re
from time import perf_counter

from app import turntrace

UPLOAD_ID = re.compile(r'^[0-9a-f]{16}$')
INFRA_ID = re.compile(r'^[A-Za-z0-9:_.-]{1,64}$')       # cf-ray / x-vercel-id: opaque request ids of the proxies, not people
_logger = logging.getLogger('uvicorn.error')


def _text(value: bytes, pattern: re.Pattern) -> str | None:
    try:
        text = value.decode('ascii')
    except UnicodeError:
        return None
    return text if pattern.match(text) else None


class UploadTrace:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not turntrace.enabled or scope['type'] != 'http' or scope['method'] != 'POST' or not scope['path'].endswith('/attempts'):
            return await self.app(scope, receive, send)
        started, wall = perf_counter(), turntrace.now_ms()
        declared = upload = turn = cf_ray = vercel_id = None
        for key, value in scope['headers']:
            if key == b'content-length':
                try:
                    declared = max(0, min(int(value), 1 << 31))
                except ValueError:
                    declared = None
            elif key == b'x-audli-upload-id':
                upload = _text(value, UPLOAD_ID)
            elif key == b'x-audli-turn':
                turn = _text(value, turntrace.TURN_ID)
            elif key == b'cf-ray':
                cf_ray = _text(value, INFRA_ID)
            elif key == b'x-vercel-id':
                vercel_id = _text(value, INFRA_ID)
        seen = {'received': 0, 'first': None, 'body': None, 'disconnect': None, 'before_body': False, 'status': None}

        async def traced_receive():
            message = await receive()
            kind = message['type']
            if kind == 'http.request':
                chunk = message.get('body', b'')
                seen['received'] += len(chunk)
                now = perf_counter()
                if chunk and seen['first'] is None:
                    seen['first'] = now
                if not message.get('more_body', False) and seen['body'] is None:
                    seen['body'] = now
            elif kind == 'http.disconnect' and seen['disconnect'] is None:
                seen['disconnect'] = perf_counter()
                seen['before_body'] = seen['body'] is None
            return message

        async def traced_send(message):
            if message['type'] == 'http.response.start' and seen['status'] is None:
                seen['status'] = int(message['status'])
            await send(message)

        outcome = None
        try:
            await self.app(scope, traced_receive, traced_send)
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except Exception:
            outcome = 'error'
            raise
        finally:
            try:
                self._log(scope, wall, started, declared, upload, turn, cf_ray, vercel_id, seen, outcome)
            except Exception:                      # diagnostics never affect a request
                pass

    @staticmethod
    def _log(scope, wall, started, declared, upload, turn, cf_ray, vercel_id, seen, outcome):
        def since(moment):
            return None if moment is None else int(round((moment - started) * 1000))
        if outcome is None:
            if seen['status'] is not None:
                outcome = 'completed'
            elif seen['disconnect'] is not None:
                outcome = 'disconnect_before_body' if seen['before_body'] else 'disconnect_before_response'
            else:
                outcome = 'no_response'
        record = {'v': 1, 'kind': 'upload', 'route': f"POST {turntrace.template(scope['path'])}", 'start_ms': wall,
                  'upload': upload, 'turn': turn, 'declared': declared, 'received': seen['received'],
                  'first_ms': since(seen['first']), 'body_ms': since(seen['body']), 'disconnect_ms': since(seen['disconnect']),
                  'status': seen['status'], 'outcome': outcome, 'total_ms': since(perf_counter()),
                  'cf_ray': cf_ray, 'vercel_id': vercel_id}
        _logger.info('Upload trace %s', json.dumps(record, separators=(',', ':')))
