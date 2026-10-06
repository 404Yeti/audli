from urllib.parse import urlparse
from starlette.responses import JSONResponse

def parse_browser_origin(value: str) -> tuple[str, str, int]:
    """Parse a serialized HTTP(S) origin, never a URL path or host pattern."""
    if not value or any(char.isspace() or ord(char) < 32 for char in value) or '\\' in value:
        raise ValueError('Expected an HTTP(S) origin without whitespace')
    parsed = urlparse(value)
    if parsed.netloc.startswith('[') and parsed.netloc.partition(']')[2][:1] not in ('', ':'):
        raise ValueError('Invalid bracketed origin authority')
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.params or parsed.query or parsed.fragment
            or '?' in value or '#' in value or '*' in value or '%' in parsed.netloc
            or parsed.netloc.endswith(':')):
        raise ValueError('Expected an HTTP(S) origin without credentials, paths or wildcards')
    port = parsed.port
    return parsed.scheme, parsed.hostname, port if port is not None else (443 if parsed.scheme == 'https' else 80)

class LocalRequestGuard:
    """Bound request bodies before multipart parsing and reject cross-site writes."""
    def __init__(self, app, max_audio_bytes: int, allowed_origins: tuple[str, ...] = ()):
        self.app = app
        self.max_audio_bytes = max_audio_bytes
        self.allowed_origins = frozenset(parse_browser_origin(value) for value in allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        origin = headers.get(b'origin')
        if origin is not None:
            try:
                parsed_origin = parse_browser_origin(origin.decode('ascii'))
                permitted = parsed_origin[1] in ('localhost', '127.0.0.1', '::1') or parsed_origin in self.allowed_origins
            except (ValueError, UnicodeError):
                permitted = False
            if not permitted:
                return await JSONResponse({'detail':'This browser origin is not permitted.'},status_code=403)(scope,receive,send)
        maximum = self.max_audio_bytes + 65536 if scope['path'].endswith('/attempts') else 40000
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            declared = maximum + 1
        if declared > maximum:
            return await JSONResponse({'detail':'Request body is too large.'},status_code=413)(scope,receive,send)
        data = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            data.extend(message.get('body', b''))
            if len(data) > maximum:
                return await JSONResponse({'detail':'Request body is too large.'},status_code=413)(scope,receive,send)
            if not message.get('more_body', False):
                break
        consumed = False
        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type':'http.request','body':bytes(data),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
