"""Local guard-aware compatibility layer for routes not yet atomically specialized.

These routes use short fenced transactions, and quarantine any unknown outcome.
This is deliberately not a production activation mechanism or a background queue.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid5,UUID
from dataclasses import dataclass
from sqlalchemy import select,text,update,func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from starlette.responses import JSONResponse
from app.storage.repository import SQLProgressRepository
from app.storage import schema as s
from app.upload_idempotency import NAMESPACE
from experiments.durable_claims.store import slots,assert_local,Conflict
from experiments.durable_claims.service import database_call
from .schema import operations

@dataclass
class Context:
    owner:str
    identifier:str
    fence:int
    provider_started:bool=False
    wrote:bool=False

writer=ContextVar('audli_stage_writer',default=None)
legacy_lease=ContextVar('audli_stage_legacy_lease',default=False)


def advisory(db,owner):
    if legacy_lease.get():return
    key=int.from_bytes(sha256(('audli-account:'+owner).encode()).digest()[:8],'big',signed=True)
    db.execute(text("SET LOCAL lock_timeout='1000ms'"))
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),dict(key=key))


def fence(db,context):
    db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==context.owner).with_for_update()).one()
    slot=db.execute(select(slots).where(slots.c.owner==context.owner).with_for_update()).mappings().one()
    op=db.execute(select(operations).where(operations.c.owner==context.owner,operations.c.id==context.identifier)).mappings().one()
    now=db.execute(select(func.clock_timestamp())).scalar_one()
    if slot['active']!=context.identifier or slot['fence']!=context.fence or op['fence']!=context.fence or op['status']!='running' or op['expires']<=now:
        raise Conflict('Stale fenced transaction')
    db.execute(text("SELECT set_config('audli.claim_owner',:owner,true),set_config('audli.claim_id',:id,true),set_config('audli.claim_fence',:fence,true)"),
        dict(owner=context.owner,id=context.identifier,fence=str(context.fence)))


def install_transactions(engine):
    """Only this disposable engine changes behavior; copied ContextVar follows offloads."""
    assert_local(engine)
    from sqlalchemy import event
    if getattr(engine,'_audli_claim_hook',False):return
    engine._audli_claim_hook=True
    @event.listens_for(engine,'before_cursor_execute')
    def track(db,cursor,statement,parameters,context,many):
        active=writer.get()
        if active and statement.lstrip().split(None,1)[0].upper() in ('INSERT','UPDATE','DELETE'):
            active.wrote=True
    @event.listens_for(engine,'begin')
    def hook(db):
        context=writer.get()
        if context and not db.get_execution_options().get('audli_read_only'):fence(db,context)


class BridgeStore:
    def __init__(self,repo):
        assert_local(repo.engine);self.repo=repo;self.owner=repo.learner_id

    def checkpoint(self,db):
        value=db.execute(text("""SELECT jsonb_build_object(
          'profile',(SELECT data FROM learner_profiles WHERE user_id=CAST(:owner AS uuid)),
          'lesson',(SELECT data FROM lesson_lifecycles WHERE user_id=CAST(:owner AS uuid) ORDER BY started_at DESC,id DESC LIMIT 1),
          'exercise',(SELECT id FROM exercises WHERE user_id=CAST(:owner AS uuid) ORDER BY created_at DESC,id DESC LIMIT 1),
          'conversations',(SELECT jsonb_agg(c.data ORDER BY c.exercise_id) FROM conversations c JOIN exercises x ON x.id=c.exercise_id WHERE x.user_id=CAST(:owner AS uuid)))"""),dict(owner=self.owner)).scalar_one()
        return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def claim(self,key,digest,input=None):
        identifier=str(uuid5(NAMESPACE,self.owner+'|request|'+key))
        with self.repo.transaction() as db:
            advisory(db,self.owner)
            db.execute(select(s.profiles.c.user_id).where(s.profiles.c.user_id==self.owner).with_for_update()).one()
            db.execute(insert(slots).values(owner=self.owner,fence=0).on_conflict_do_nothing())
            slot=db.execute(select(slots).where(slots.c.owner==self.owner).with_for_update()).mappings().one()
            op=db.execute(select(operations).where(operations.c.owner==self.owner,operations.c.id==identifier)).mappings().first()
            now=db.execute(select(func.clock_timestamp())).scalar_one()
            ref=dict(operation_id=identifier,kind='request')
            if op:
                if op['fingerprint']!=digest:raise Conflict('Operation input changed')
                if op['status']=='complete':
                    if op['snapshot']['checkpoint']!=self.checkpoint(db):raise Conflict('Replay checkpoint changed')
                    receipt=op['snapshot']['receipt']
                    if receipt.get('stream'):raise Conflict('Use the explicit audio replay action')
                    return 'complete',receipt,None
                return 'processing' if op['status']=='running' and op['expires']>now else 'uncertain',ref,None
            if slot['active']:raise Conflict('Account processing; no automatic claim takeover')
            fence_value=slot['fence']+1
            db.execute(operations.insert().values(owner=self.owner,id=identifier,exercise=self.owner,kind='request',fingerprint=digest,
                fence=fence_value,status='running',expires=now+timedelta(seconds=60),snapshot={},input=input or {}))
            db.execute(update(slots).where(slots.c.owner==self.owner).values(active=identifier,fence=fence_value))
            return 'owned',ref,Context(self.owner,identifier,fence_value)

    def reject(self,context):
        with self.repo.transaction() as db:
            fence(db,context)
            db.execute(update(operations).where(operations.c.owner==self.owner,operations.c.id==context.identifier).values(status='invalidated'))
            db.execute(update(slots).where(slots.c.owner==self.owner).values(active=None))

    def finish(self,context,receipt):
        with self.repo.transaction() as db:
            fence(db,context)
            db.execute(update(operations).where(operations.c.owner==self.owner,operations.c.id==context.identifier).values(status='complete',snapshot={'receipt':receipt,'checkpoint':self.checkpoint(db)}))
            db.execute(update(slots).where(slots.c.owner==self.owner).values(active=None))

    def status(self,identifier):
        with self.repo.read() as db:
            op=db.execute(select(operations).where(operations.c.owner==self.owner,operations.c.id==identifier,operations.c.kind=='request')).mappings().first()
            if not op:return None
            now=db.execute(select(func.clock_timestamp())).scalar_one()
            result=dict(operation_id=identifier,kind='request',status='processing' if op['status']=='running' and op['expires']>now else 'uncertain')
            if op['status']=='complete' and not op['snapshot']['receipt'].get('stream') and op['snapshot']['checkpoint']==self.checkpoint(db):result.update(status='complete',result=op['snapshot']['receipt']['body'])
            elif op['status']=='invalidated':result['status']='invalidated'
            return result


class Bridge:
    """All remaining mutations are admitted before handler/provider execution.

    The response is buffered only for small JSON; streams keep their original
    transport behavior. Errors/disconnects/crashes leave an explicit quarantine.
    No automatic expiry takeover of a possibly partially committed legacy route.
    """
    def __init__(self,app,proxy,settings,migrated,mode):
        self.app,self.proxy,self.settings,self.migrated,self.mode=app,proxy,settings,migrated,mode

    async def __call__(self,scope,receive,send):
        path=scope.get('path','');method=scope.get('method','GET')
        if scope['type']!='http' or not path.startswith('/api/') or path=='/api/telemetry/turn':
            return await self.app(scope,receive,send)
        response_started=False
        response_finished=False
        async def emit(message):
            nonlocal response_started,response_finished
            if message['type']=='http.response.start':response_started=True
            if message['type']=='http.response.body' and not message.get('more_body'):response_finished=True
            await send(message)
        legacy=legacy_lease.set(self.mode=='bridge' and method not in ('GET','HEAD','OPTIONS'))
        try:
            if method=='GET' and (path.startswith('/api/lessons/') or path=='/api/eligibility'):
                from .lifecycle import settle
                candidate=path.removeprefix('/api/lessons/')
                try:lesson_id=str(UUID(candidate))
                except ValueError:lesson_id=None
                await database_call(settle,self.proxy.current.get(),self.settings,lesson_id)
            provider_get=method=='GET' and (path.endswith('/audio') or '/tutor-voice/preview/' in path)
            if (method in ('GET','HEAD','OPTIONS') and not provider_get) or self.migrated.fullmatch(path):
                return await self.app(scope,receive,send)
            if method=='DELETE':
                return await JSONResponse({'detail':'Account deletion requires reviewed claim drain and retention handling.'},status_code=409)(scope,receive,send)
            headers=dict(scope['headers']);key=headers.get(b'x-audli-upload-id') or headers.get(b'x-audli-operation-id')
            if provider_get and not key:
                from uuid import uuid4
                key=str(uuid4()).encode()
            if not key or len(key)>64:
                return await JSONResponse({'detail':'Stable operation identity required.'},status_code=428)(scope,receive,send)
            # Bounded memory only; never persist request bodies or audio.
            data=bytearray()
            while True:
                message=await receive()
                if message['type']=='http.disconnect':return
                data.extend(message.get('body',b''))
                if len(data)>self.settings.max_audio_bytes+65536:
                    return await JSONResponse({'detail':'Recording too large.'},status_code=413)(scope,receive,send)
                if not message.get('more_body'):break
            body=bytes(data);digest=sha256(body).hexdigest()
            # Canonicalize multipart boundaries; file bytes and fields remain bound.
            if b'multipart/form-data' in headers.get(b'content-type',b''):
                from email.parser import BytesParser
                from email.policy import default
                parsed=BytesParser(policy=default).parsebytes(b'Content-Type: '+headers[b'content-type']+b'\r\nMIME-Version: 1.0\r\n\r\n'+body)
                if not parsed.is_multipart():raise Conflict('Malformed recording')
                parts=[(p.get_param('name',header='content-disposition'),p.get_filename(),p.get_content_type(),sha256(p.get_payload(decode=True) or b'').hexdigest()) for p in parsed.iter_parts()]
                digest=sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()
            store=BridgeStore(self.proxy.current.get())
            state,value,context=await database_call(store.claim,method+'|'+path+'|'+key.decode('ascii'),digest,{'path':path,'method':method})
            if state=='complete':return await JSONResponse(value['body'],status_code=value['status'])(scope,receive,send)
            if state!='owned':return await JSONResponse({**value,'processing':True},status_code=202)(scope,receive,send)
            delivered=False
            async def replay():
                nonlocal delivered
                if not delivered:delivered=True;return {'type':'http.request','body':body,'more_body':False}
                return await receive()
            start=None;chunks=[];stream=False
            async def capture(message):
                nonlocal start,stream
                if message['type']=='http.response.start':
                    start=message
                    content=dict(message.get('headers',[])).get(b'content-type',b'')
                    stream=b'application/json' not in content
                    if stream:await emit(message)
                    return
                if stream:return await emit(message)
                chunks.append(message.get('body',b''))
                if sum(map(len,chunks))>1024*1024:raise Conflict('JSON acknowledgment too large')
            token=writer.set(context)
            try:
                import asyncio
                async with asyncio.timeout(55):
                    await self.app(scope,replay,capture)
                if start and start['status']<400:
                    receipt={'status':start['status'],'body':json.loads(b''.join(chunks)) if not stream else {'audio_cached':True},'stream':stream}
                    await database_call(store.finish,context,receipt)
                elif start and not context.wrote and not context.provider_started:
                    await database_call(store.reject,context)
                if not stream and start:
                    await emit(start);await emit({'type':'http.response.body','body':b''.join(chunks)})
            finally:writer.reset(token)
        except (Conflict,UnicodeError,TimeoutError,DBAPIError):
            if response_started:
                if not response_finished:await emit({'type':'http.response.body','body':b''})
                return
            await JSONResponse({'detail':'Operation remains unconfirmed. Keep your recording and check saved progress.'},status_code=409)(scope,receive,send)
        finally:legacy_lease.reset(legacy)
