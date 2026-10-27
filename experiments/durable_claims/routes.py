"""LOCAL harness only: replace the real upload route, retain real auth and validation.

Claims-only excludes exactly this route from the legacy middleware lease in this
isolated Python process. Bridge mode retains that lease. No application flag added.
"""
from fastapi import UploadFile, Request, Response, HTTPException
from starlette.responses import JSONResponse

from app.audio import validate_upload
from app.upload_idempotency import upload_id_from
from .service import process
from .store import Store, Conflict, claims_only_allowed


def install(app, settings, provider, *, mode='claims', old_workers=0):
    if mode not in ('bridge', 'claims'):
        raise ValueError('Unknown protocol')
    if mode == 'claims':
        claims_only_allowed(old_workers=old_workers, participating_writers=True)
        import re
        import app.auth as auth
        original = auth.lease_exempt
        pattern = re.compile(r'/api/exercises/[0-9a-f-]{36}/attempts')
        auth.lease_exempt = lambda method, path: (method == 'POST' and bool(pattern.fullmatch(path))) or original(method, path)
    path = '/api/exercises/{exercise_id}/attempts'
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, 'path', None) != path]
    # RequestRepository is captured by the original routes; locate via app state? The
    # authenticated middleware stores its request scope on this same proxy.
    from app.auth import LearnerAuthentication
    app.middleware_stack = app.build_middleware_stack()
    auth = app.middleware_stack
    while not isinstance(auth, LearnerAuthentication):
        auth = auth.app
    repo_proxy = auth.request_repository

    @app.post(path)
    async def upload(exercise_id: str, audio: UploadFile, request: Request, response: Response):
        try:
            key = upload_id_from(request.headers)
            if key is None:
                raise HTTPException(422, 'A stable recording ID is required by this local experiment')
            data, filename = await validate_upload(audio, settings)
            store = Store(repo_proxy.current.get())
            kind, result = await process(store, exercise_id, key, data, filename, provider)
            if kind == 'replay':
                response.headers['X-Audli-Replayed'] = 'true'
            if kind in ('busy', 'processing'):
                # Explicit experiment protocol; existing web client is not claimed compatible.
                return JSONResponse({'processing': True}, status_code=202, headers={'Retry-After': '1'})
            return result
        except Conflict:
            raise HTTPException(409, 'Recording no longer matches the active question') from None
        except ValueError:
            raise HTTPException(404, 'Exercise not found') from None
        finally:
            await audio.close()

    @app.get('/api/poc/uploads/{exercise_id}/{key}')
    async def status(exercise_id: str, key: str):
        from .service import database_call
        try:
            result = await database_call(Store(repo_proxy.current.get()).status, exercise_id, key)
            return result if result else JSONResponse({'processing': True}, status_code=202)
        except Conflict:
            raise HTTPException(409, 'No recoverable operation') from None
        except ValueError:
            raise HTTPException(404, 'Exercise not found') from None
