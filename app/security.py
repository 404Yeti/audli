from urllib.parse import urlparse
from starlette.responses import JSONResponse

class LocalRequestGuard:
    """Bound request bodies before multipart parsing and reject cross-site writes."""
    def __init__(self, app, max_audio_bytes: int):
        self.app = app
        self.max_audio_bytes = max_audio_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        origin = headers.get(b'origin')
        if origin and urlparse(origin.decode()).hostname not in ('localhost','127.0.0.1','[::1]','::1'):
            return await JSONResponse({'detail':'This prototype accepts local browser requests only.'},status_code=403)(scope,receive,send)
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
