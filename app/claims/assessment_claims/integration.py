"""Guard-aware route installation, called only by validated runtime startup."""
from app.auth import LearnerAuthentication
from app.claims.durable_claims.service import database_call
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
    specialized=re.compile(rf'/api/(exercises/{UUID}/(attempts|record-again|conversation/ready)|attempts/{UUID}/assess|lessons/{UUID}/advance|operations/{UUID}/record-again|(?:exercises|lessons)/{UUID}/recordings/[0-9a-f]{{16}}/record-again|onboarding/restart|account/delete)')
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
    if '/api/account/delete' in original:
        # The account is gone after commit, so a generic post-response receipt
        # cannot be finalized. Admit the actual erase transaction instead.
        from fastapi import Request,HTTPException
        from app.account import DeleteAccount,require_live_session,reauthenticate
        from app.claims.release_readiness.account_erasure import erase
        from app.claims.durable_claims.store import Conflict
        app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/api/account/delete']
        @app.post('/api/account/delete')
        async def delete_owned_account(body:DeleteAccount,request:Request):
            repo=auth.request_repository.current.get()
            await database_call(require_live_session,repo,request)
            await reauthenticate(auth.identity,request,body.password,repo.learner_id)
            try:
                return await database_call(erase,repo,settings.data_dir/'exercise_audio')
            except Conflict:
                raise HTTPException(409,'An operation is active or unresolved. Recover it before deleting your account.') from None
            except HTTPException:
                raise
            except Exception:
                # Database exceptions can contain SQL parameters/account IDs.
                raise HTTPException(503,'Deletion could not be confirmed. Retry or contact support.') from None
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

    from .lifecycle import replace_by_recording
    from pydantic import Field
    class ReplaceByRecording(StrictModel):
        revision:int|None=Field(default=None,ge=0)
        pending:str|None=None
        followup:str|None=None
    @app.post('/api/exercises/{resource_id}/recordings/{recording}/record-again')
    async def replace_exercise(resource_id:str,recording:str,body:ReplaceByRecording):
        return await database_call(lambda:replace_by_recording(auth.request_repository.current.get(),settings,'exercise',resource_id,recording,**body.model_dump()))
    @app.post('/api/lessons/{resource_id}/recordings/{recording}/record-again')
    async def replace_lesson(resource_id:str,recording:str,body:ReplaceByRecording):
        return await database_call(lambda:replace_by_recording(auth.request_repository.current.get(),settings,'lesson',resource_id,recording,**body.model_dump()))

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
