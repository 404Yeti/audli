"""Fenced provider routes; startup validates schema and backend role."""
import re
import asyncio
from hashlib import sha256
from fastapi import Request,Response,UploadFile,HTTPException,Form
from starlette.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from app.audio import validate_upload
from app.auth import LearnerAuthentication
from app.conversation_api import TurnConfirmation
from app.lesson_api import LessonRevision
from app.upload_idempotency import upload_id_from
from app.storage import schema as s
from app.claims.durable_claims.store import Conflict,assert_local,claims_only_allowed
from app.claims.durable_claims.service import database_call
from .store import StageStore

UUID=r'[0-9a-f-]{36}'


def install(app,settings,provider,mode='claims',old_workers=0):
    if mode not in ('claims','bridge'):raise ValueError('Unknown ownership mode')
    if mode=='claims':claims_only_allowed(old_workers=old_workers,participating_writers=True)
    app.middleware_stack=app.build_middleware_stack();auth=app.middleware_stack
    while not isinstance(auth,LearnerAuthentication):auth=auth.app
    assert_local(auth.repository.engine)
    proxy=auth.request_repository
    if mode=='claims':
        original=auth.lease_exempt
        migrated=re.compile(rf'/api/(exercises/{UUID}/(attempts|record-again|conversation/(listened|ready))|attempts/{UUID}/assess|lessons/{UUID}/(advance|end)|onboarding/restart)')
        auth.lease_exempt=lambda method,path:(method=='POST' and bool(migrated.fullmatch(path))) or original(method,path)
        class Gate:
            def __init__(self,app):self.app=app
            async def __call__(self,scope,receive,send):
                if scope['type']=='http' and scope['method'] not in ('GET','HEAD','OPTIONS') and scope['path'].startswith('/api/') and not migrated.fullmatch(scope['path']) and scope['path']!='/api/telemetry/turn':
                    return await JSONResponse({'detail':'This writer is not part of the staged migration.'},status_code=409)(scope,receive,send)
                return await self.app(scope,receive,send)
        auth.app=Gate(auth.app)
    if mode=='bridge':
        class LegacyContext:
            def __init__(self,app):self.app=app
            async def __call__(self,scope,receive,send):
                from .bridge import legacy_lease
                token=legacy_lease.set(True)
                try:return await self.app(scope,receive,send)
                finally:legacy_lease.reset(token)
        auth.app=LegacyContext(auth.app)
    def store():return StageStore(proxy.current.get(),settings)
    async def error_guard(function):
        try:return await function()
        except Conflict:raise HTTPException(409,'This operation no longer owns the current question.') from None
        except DBAPIError as error:
            code=getattr(error.orig,'sqlstate',None)
            raise HTTPException(404 if code=='P0002' else 409 if code=='P0001' else 503,'The operation could not be confirmed. Keep your recording and retry.') from None
        except ValueError:raise HTTPException(503,'Assessment validation failed. Your answer remains saved.') from None
    paths=['/api/exercises/{exercise_id}/attempts','/api/attempts/{attempt_id}/assess',
        '/api/exercises/{exercise_id}/conversation','/api/exercises/{exercise_id}/conversation/ready','/api/exercises/{exercise_id}/conversation/listened',
        '/api/lessons/{lesson_id}/advance','/api/lessons/{lesson_id}/end','/api/lessons/{lesson_id}','/api/onboarding/restart']
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None) not in paths]

    async def run(exercise,kind,request):
        st=store();state,value,lease=await database_call(st.claim,exercise,kind,request)
        if state=='processing':return JSONResponse({'processing':True,**value},status_code=202,headers={'Retry-After':'1'})
        if state=='uncertain':return JSONResponse({'detail':'Please check the recognition or record again.','uncertain':True},status_code=422)
        if state=='done':return value
        try:
            if kind=='assess':
                from app.models import ExerciseContent,Conversation,ConversationTurn
                c=Conversation.model_validate(value['conversation']['data'])
                turns=[*c.turns,ConversationTurn(attempt_id=request['attempt_id'],text=request['text'].strip(),followup=c.active_followup)]
                result=await provider.assess(ExerciseContent.model_validate(value['exercise']['content']),turns)
            else:result=None
        except asyncio.CancelledError:
            try:await database_call(st.abandon,lease)
            except Exception:pass  # Unknown cleanup stays fenced until expiry.
            raise
        except Exception:
            try:await database_call(st.abandon,lease)
            except Exception:pass
            raise HTTPException(503,'Assessment could not be confirmed; your answer remains saved.') from None
        result=await database_call(st.finish,lease,result)
        if result.get('uncertain'):return JSONResponse({'detail':'Please check recognition.','uncertain':True},status_code=422)
        return result

    @app.get('/api/operations/{operation_id}')
    async def operation_status(operation_id:str):
        from uuid import UUID
        try:UUID(operation_id)
        except ValueError:raise HTTPException(404,'Operation not found') from None
        return await error_guard(lambda:database_call(store().status,operation_id))

    @app.post('/api/exercises/{exercise_id}/attempts')
    async def upload(exercise_id:str,audio:UploadFile,request:Request,response:Response,replaces_attempt_id:str|None=Form(default=None)):
        try:
            key=upload_id_from(request.headers)
            operation_key=request.headers.get('X-Audli-Operation-Id','')
            if re.fullmatch('[0-9a-f]{16}',operation_key):
                if key and key!=operation_key:raise HTTPException(409,'Recording identities disagree. Keep your recording.')
                key=key or operation_key
            if not key:raise HTTPException(428,'Stable recording identity required. Keep your recording while updating the page.')
            data,name=await validate_upload(audio,settings)
            st=store()
            async def execute():
                state,value,lease=await database_call(st.claim,exercise_id,'upload',{'upload_id':key,'bytes_hash':sha256(data).hexdigest(),**({'replaces_attempt_id':replaces_attempt_id} if replaces_attempt_id else {})})
                if state=='processing':return JSONResponse({'processing':True,**value},status_code=202)
                if state=='done':response.headers['X-Audli-Replayed']='true';return value
                try:t=await provider.transcribe(data,name)
                except asyncio.CancelledError:
                    try:await database_call(st.abandon,lease)
                    except Exception:pass
                    raise
                except Exception:
                    try:await database_call(st.abandon,lease)
                    except Exception:pass
                    raise HTTPException(503,'Transcription could not be confirmed. Keep your recording.') from None
                return await database_call(st.finish,lease,t)
            return await error_guard(execute)
        finally:await audio.close()

    async def exercise_for_attempt(attempt):
        def lookup():
            row=proxy.attempt(attempt)
            if not row:raise HTTPException(404,'Attempt not found')
            return row['exercise_id']
        return await error_guard(lambda:database_call(lookup))

    @app.post('/api/attempts/{attempt_id}/assess')
    async def assess(attempt_id:str,body:TurnConfirmation):
        exercise=await exercise_for_attempt(attempt_id)
        return await error_guard(lambda:run(exercise,'assess',{'attempt_id':attempt_id,**body.model_dump()}))

    from app.models import StrictModel
    class RecordAgain(StrictModel):
        pending_attempt_id:str
        followup_id:str|None=None

    @app.post('/api/exercises/{exercise_id}/record-again')
    async def record_again(exercise_id:str,body:RecordAgain):
        return await error_guard(lambda:database_call(lambda:store().invalidate(exercise_id,'record_again',expected_pending=body.pending_attempt_id,expected_followup=body.followup_id)))

    @app.post('/api/exercises/{exercise_id}/conversation/ready')
    async def ready(exercise_id:str):return await error_guard(lambda:run(exercise_id,'ready',{}))

    @app.get('/api/exercises/{exercise_id}/conversation')
    async def conversation(exercise_id:str):
        return await listened(exercise_id)

    @app.post('/api/exercises/{exercise_id}/conversation/listened')
    async def listened(exercise_id:str):
        # This stage seeds the exercise with its listening checkpoint. No bypass of
        # an existing question, pending answer or accepted evidence is permitted.
        def read():
            st=store()
            with st.repo.transaction() as db:return st.view(st.read(db,exercise_id,'00000000-0000-0000-0000-000000000000'),db)
        return await error_guard(lambda:database_call(read))

    async def conversation_lesson(ex):
        def read():
            st=store()
            with st.repo.transaction() as db:return st.lesson_view(st.read(db,ex,'00000000-0000-0000-0000-000000000000'))
        return await error_guard(lambda:database_call(read))

    async def lesson_exercise(lesson_id):
        def lookup():
            lesson=proxy.lesson(lesson_id)
            if not lesson:raise HTTPException(404,'Lesson not found')
            if lesson.exercise_id:return lesson.exercise_id
            work=proxy.lesson_work(lesson_id)
            if work:return work[-1]['exercise_id']
            raise HTTPException(409,'Lesson has no exercise in this stage')
        return await error_guard(lambda:database_call(lookup))

    @app.post('/api/lessons/{lesson_id}/advance')
    async def advance(lesson_id:str,body:LessonRevision):
        exercise=await lesson_exercise(lesson_id)
        return await error_guard(lambda:run(exercise,'advance',{'revision':body.revision}))

    @app.post('/api/lessons/{lesson_id}/end')
    async def end(lesson_id:str):
        ex=await lesson_exercise(lesson_id)
        await error_guard(lambda:database_call(store().invalidate,ex,'pause'))
        return await conversation_lesson(ex)

    @app.get('/api/lessons/{lesson_id}')
    async def lesson(lesson_id:str):
        ex=await lesson_exercise(lesson_id)
        await error_guard(lambda:database_call(store().settle,ex))
        return await conversation_lesson(ex)

    from app.onboarding_reset import ResetOnboarding
    @app.post('/api/onboarding/restart')
    async def restart(body:ResetOnboarding):
        def execute():
            st=store();ex=st.repo.current_exercise()
            if not ex:raise Conflict('No active exercise in this stage')
            st.invalidate(ex['id'],'onboarding',expected_revision=body.revision)
            return {'destination':'onboarding'}
        return await error_guard(lambda:database_call(execute))
