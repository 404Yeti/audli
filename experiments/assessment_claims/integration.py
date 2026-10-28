"""Explicit local-only full-route compatibility installation, no startup import."""
from app.auth import LearnerAuthentication
from experiments.durable_claims.service import database_call
from .routes import install as install_stage
from .bridge import Bridge,BridgeStore,install_transactions


def install(app,settings,provider,mode='claims',old_workers=0):
    # Keep originals for complete lesson projection/closing/homework lifecycle.
    original={r.path:r for r in app.router.routes if hasattr(r,'path')}
    install_stage(app,settings,provider,mode,old_workers)
    auth=app.middleware_stack
    while not isinstance(auth,LearnerAuthentication):auth=auth.app
    from .routes import UUID
    import re
    specialized=re.compile(rf'/api/(exercises/{UUID}/(attempts|record-again|conversation/ready)|attempts/{UUID}/assess|lessons/{UUID}/advance|operations/{UUID}/record-again|onboarding/restart)')
    # Original complete lesson response, heard/closing, reset and expiry semantics.
    restore={'/api/lessons/{lesson_id}','/api/lessons/{lesson_id}/end','/api/onboarding/restart','/api/exercises/{exercise_id}/conversation/listened','/api/exercises/{exercise_id}/conversation'}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None) not in restore]
    app.router.routes.extend(original[p] for p in restore)
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/api/onboarding/restart']
    from app.onboarding_reset import ResetOnboarding
    from .lifecycle import reset
    @app.post('/api/onboarding/restart')
    async def restart(body:ResetOnboarding):
        return await database_call(reset,auth.request_repository.current.get(),settings,body.revision)
    # Expiry GETs may write, so bridge them too rather than treating GET as read-only.
    if mode=='claims':auth.app=auth.app.app  # Remove the earlier unsupported-writer gate.
    install_transactions(auth.repository.engine)
    from .bridge import writer
    import inspect
    for name in dir(provider):
        method=getattr(provider,name)
        if not name.startswith('_') and inspect.iscoroutinefunction(method):
            def wrap(function):
                async def call(*args,**kwargs):
                    context=writer.get()
                    if context:context.provider_started=True
                    return await function(*args,**kwargs)
                return call
            setattr(provider,name,wrap(method))
    # Keep GET settlement behind admission, but pure status never acquires ownership.
    bridge=Bridge(auth.app,auth.request_repository,settings,specialized,mode)
    auth.app=bridge
    if mode=='claims':auth.lease_exempt=lambda method,path:True
    @app.get('/api/operation-capabilities')
    async def capabilities():return {'durable_operations':True,'protocol':1}
    from app.models import StrictModel
    from typing import Literal
    from .lifecycle import record_again
    class ReplaceRecording(StrictModel):
        kind:Literal['upload','request']
    @app.post('/api/operations/{operation_id}/record-again')
    async def replace_recording(operation_id:str,body:ReplaceRecording):
        return await database_call(record_again,auth.request_repository.current.get(),settings,operation_id,body.kind)

    # Generic receipts share the exact same authenticated status route.
    for route in app.router.routes:
        if getattr(route,'path',None)=='/api/operations/{operation_id}':
            stage=route.endpoint
            async def status(operation_id:str):
                from uuid import UUID
                from fastapi import HTTPException
                try:UUID(operation_id)
                except ValueError:raise HTTPException(404,'Operation not found') from None
                value=await database_call(lambda:BridgeStore(auth.request_repository.current.get()).status(operation_id))
                return value if value is not None else await stage(operation_id)
            # APIRoute dependency captures the original endpoint; replace the route.
            app.router.routes.remove(route)
            app.get('/api/operations/{operation_id}')(status)
            break
    return app
