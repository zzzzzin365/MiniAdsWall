"""Control plane; no Agent/model initialization in HTTP workers."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import secrets
import time
from typing import Literal
from fastapi import FastAPI, Request, Depends, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import RedisError
from .auth import Auth
from .repository import Conflict, TERMINAL, public
from .reliability import load_reliability
from marketing.config import load_marketing

class SessionInput(BaseModel):
    workspace_id: str
    title: str = Field(default='New session',max_length=120)
class RunInput(BaseModel):
    input_protocol_version: Literal[1] = 1
    message: str = Field(min_length=1,max_length=65536)
    ads: list[dict] = Field(default_factory=list,max_length=1000)
    ad_context: dict | None = None
    performance_context: dict | None = None
    conditions: list[dict] = Field(default_factory=list,max_length=16)

    @field_validator('ad_context')
    @classmethod
    def bounded_context(cls,value):
        if value is not None and (len(json.dumps(value,ensure_ascii=False).encode()) > 65536 or value.get('version') != 1 or not isinstance(value.get('items'),list) or len(value['items']) > 100):
            raise ValueError('invalid_ad_context')
        return value

    @field_validator('performance_context')
    @classmethod
    def bounded_performance(cls,value):
        if value is not None and (len(json.dumps(value,ensure_ascii=False).encode()) > 65536
                or value.get('version') != 1 or not isinstance(value.get('items'),list)
                or len(value['items']) > 100
                or value.get('source') not in {'ad_performance_ledger','synthetic_merchant_demo_ledger','metrics_unavailable'}):
            raise ValueError('invalid_performance_context')
        return value

    tool: str | None = None
    argv: list[str] | None = Field(default=None,max_length=100)
    @model_validator(mode='before')
    @classmethod
    def supported_protocol(cls,value):
        if isinstance(value,dict) and (((value.get('ad_context') is not None or value.get('conditions')) and not load_reliability().features['AD_CONTEXT_V1_ENABLED']) or type(value.get('input_protocol_version',1)) is not int):
            raise ValueError('unsupported_input_protocol')
        return value
class ApprovalInput(BaseModel):
    allow: bool
class PreferenceInput(BaseModel):
    field: str = Field(max_length=80)
    values: list[str] = Field(default_factory=list, max_length=20)
    expected_version: int = Field(ge=0)

class CandidateDecision(BaseModel):
    accept: bool
    expected_version: int = Field(ge=0)
class ProcedureInput(BaseModel):
    title: str = Field(max_length=120)
    objective: str = Field(max_length=120)
    conditions: list[str] = Field(min_length=1,max_length=8)
    steps: list[str] = Field(min_length=1,max_length=8)
    limitations: list[str] = Field(min_length=1,max_length=8)
    source_title: str = Field(max_length=120)
    expires_at: float | None = None
class ProcedureAction(BaseModel):
    action: Literal['review','withdraw']
    expected_version: int = Field(ge=1)
class LoginInput(BaseModel):
    subject: str = Field(min_length=1,max_length=190)

async def call(fn,*args,**kwargs): return await asyncio.to_thread(fn,*args,**kwargs)

def create_app(repo=None,redis=None):
    load_reliability()
    load_marketing()
    @asynccontextmanager
    async def lifespan(app):
        if app.state.repo is None:
            from .config import repository
            app.state.repo=repository()
        if app.state.auth is None:
            app.state.auth=Auth(Redis.from_url(os.environ['REDIS_URL'],decode_responses=True,socket_timeout=2,socket_connect_timeout=2))
        yield
        if redis is None: await app.state.auth.redis.aclose()
        if repo is None: app.state.repo.engine.dispose()
    app=FastAPI(title='MiniAdsWall durable Agent',lifespan=lifespan)
    app.state.repo=repo; app.state.auth=Auth(redis) if redis else None

    @app.exception_handler(Conflict)
    async def conflict(request,exc): return JSONResponse({'error':str(exc)},status_code=exc.status,headers={'Retry-After':'5'} if exc.status in (429,503) else {})
    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(RedisError)
    async def unavailable(request,exc): return JSONResponse({'error':'storage_unavailable'},status_code=503)

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.url.path!='/health':
            expected=os.getenv('AGENT_SERVICE_TOKEN','')
            supplied=request.headers.get('authorization','')
            if not expected or not secrets.compare_digest(supplied.encode(),f'Bearer {expected}'.encode()):
                return JSONResponse({'error':'service_auth_required'},status_code=401)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    async def principal(request:Request):
        user=await app.state.auth.resolve(request.headers.get('X-Agent-Session',''))
        # Auth sessions never override disabled DB accounts.
        from . import schema as s
        def enabled():
            with app.state.repo.engine.connect() as c:
                row=app.state.repo._row(c,s.users,user)
                if row['status']!='active': raise Conflict('account_disabled',403)
        await call(enabled)
        return user

    @app.get('/health')
    async def health():
        from sqlalchemy import select, func
        from . import schema as s
        def check():
            with app.state.repo.engine.connect() as c: return c.execute(select(func.max(s.versions.c.version))).scalar_one()
        await call(check); await app.state.auth.redis.ping()
        return {'status':'ready'}

    @app.post('/auth/session')
    async def login(body:LoginInput):
        # Only the trusted Koa gateway or provisioning CLI can set auth_subject.
        identity=await call(app.state.repo.provision,body.subject)
        token=await app.state.auth.issue(identity['user_id'])
        return public(dict(identity,session_token=token))

    @app.delete('/auth/session')
    async def logout(request:Request,user=Depends(principal)):
        await app.state.auth.logout(request.headers['X-Agent-Session']); return {'ok':True}

    @app.put('/workspaces/{wid}/snapshot')
    async def upload_snapshot(wid:int,request:Request,user=Depends(principal)):
        content=bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>32*1024*1024: raise Conflict('snapshot_too_large',413)
        return public(await call(app.state.repo.workspace_snapshot,user,wid,bytes(content)))
    @app.post('/runs/{rid}/workspace/commit')
    async def commit_workspace(rid:int,user=Depends(principal)):
        return public(await call(app.state.repo.commit_workspace,user,rid))

    def memory_scope(user, sid):
        from memory.context_service import Scope
        with app.state.repo.engine.connect() as c:
            session=app.state.repo._session(c,user,sid)
        return Scope(user,session['workspace_id'],sid)

    @app.get('/sessions/{sid}/memory/preferences')
    async def preferences(sid:int,user=Depends(principal)):
        from memory.repository import MemoryRepository
        scope=await call(memory_scope,user,sid)
        return public(await call(MemoryRepository(app.state.repo).list_preferences,scope))

    @app.put('/sessions/{sid}/memory/preferences')
    async def set_preference(sid:int,body:PreferenceInput,user=Depends(principal)):
        from memory.repository import MemoryRepository
        scope=await call(memory_scope,user,sid)
        return public(await call(MemoryRepository(app.state.repo).preference,scope,body.field,body.values,body.expected_version))

    @app.delete('/sessions/{sid}/memory')
    async def clear_memory(sid:int,user=Depends(principal)):
        from memory.repository import MemoryRepository
        scope=await call(memory_scope,user,sid)
        return await call(MemoryRepository(app.state.repo).clear_session,scope)

    @app.get('/sessions/{sid}/memory/candidates')
    async def memory_candidates(sid:int,user=Depends(principal)):
        from memory.repository import MemoryRepository
        return public(await call(MemoryRepository(app.state.repo).candidates,await call(memory_scope,user,sid)))

    @app.post('/sessions/{sid}/memory/candidates/{cid}/decision')
    async def candidate_decision(sid:int,cid:int,body:CandidateDecision,user=Depends(principal)):
        from memory.repository import MemoryRepository
        return public(await call(MemoryRepository(app.state.repo).resolve_candidate,await call(memory_scope,user,sid),cid,body.accept,body.expected_version))

    @app.get('/sessions/{sid}/memory/jobs')
    async def memory_jobs(sid:int,user=Depends(principal)):
        from memory.repository import MemoryRepository
        return public(await call(MemoryRepository(app.state.repo).list_jobs,await call(memory_scope,user,sid)))

    @app.post('/sessions/{sid}/memory/jobs/{jid}/replay')
    async def memory_replay(sid:int,jid:int,user=Depends(principal)):
        from memory.repository import MemoryRepository
        return await call(MemoryRepository(app.state.repo).replay_job,await call(memory_scope,user,sid),jid)

    @app.get('/sessions/{sid}/memory/revisions/{kind}/{ident}')
    async def memory_revisions(sid:int,kind:Literal['preference','summary'],ident:int,user=Depends(principal)):
        from memory.repository import MemoryRepository
        return public(await call(MemoryRepository(app.state.repo).revisions,await call(memory_scope,user,sid),kind,ident))

    @app.delete('/sessions/{sid}/memory/sources/{message_id}')
    async def erase_memory_source(sid:int,message_id:int,user=Depends(principal)):
        from memory.repository import MemoryRepository
        return await call(MemoryRepository(app.state.repo).suppress_source,await call(memory_scope,user,sid),message_id)

    @app.get('/sessions/{sid}/memory/procedures')
    async def procedures(sid:int,user=Depends(principal)):
        from marketing.feedback.procedures import ProcedureRepository
        return public(await call(ProcedureRepository(app.state.repo).list,await call(memory_scope,user,sid)))

    @app.post('/sessions/{sid}/memory/procedures')
    async def new_procedure(sid:int,body:ProcedureInput,user=Depends(principal)):
        from marketing.feedback.procedures import ProcedureRepository
        return public(await call(ProcedureRepository(app.state.repo).save,await call(memory_scope,user,sid),body.model_dump()))

    @app.post('/sessions/{sid}/memory/procedures/{pid}/decision')
    async def review_procedure(sid:int,pid:int,body:ProcedureAction,user=Depends(principal)):
        from marketing.feedback.procedures import ProcedureRepository
        return public(await call(ProcedureRepository(app.state.repo).save,await call(memory_scope,user,sid),{},pid,body.expected_version,body.action))

    @app.post('/sessions',status_code=201)
    async def new_session(body:SessionInput,user=Depends(principal)):
        return public(await call(app.state.repo.create_session,user,body.workspace_id,body.title))
    @app.get('/sessions')
    async def sessions(limit:int=Query(20,ge=1,le=100),cursor:str|None=None,user=Depends(principal)):
        return public(await call(app.state.repo.list_sessions,user,limit,cursor))
    @app.get('/sessions/{sid}/history')
    @app.get('/sessions/{sid}/messages')
    @app.get('/sessions/{sid}/tool-calls')
    async def history(sid:int,request:Request,limit:int=Query(50,ge=1,le=100),cursor:str|None=None,user=Depends(principal)):
        kind={'messages':'message','tool-calls':'tool_call'}.get(request.url.path.rsplit('/',1)[-1])
        return public(await call(app.state.repo.history,user,sid,limit,cursor,kind))
    @app.post('/sessions/{sid}/runs',status_code=202)
    async def new_run(sid:int,body:RunInput,idempotency_key:str=Header(),user=Depends(principal)):
        await app.state.auth.limit(user)
        if body.tool not in (None,'shell','test'): raise Conflict('unknown_tool',400)
        if body.tool and (not body.argv or any(len(arg)>8192 for arg in body.argv)): raise Conflict('invalid_argv',400)
        return public(await call(app.state.repo.create_run,user,sid,idempotency_key,body.model_dump()))
    @app.post('/sessions/{sid}/chat')
    async def synchronous_chat(sid:int,body:RunInput,idempotency_key:str=Header(),user=Depends(principal)):
        from memory.context_service import enabled
        if not enabled('AGENT_SYNC_DURABLE_ENABLED'): raise Conflict('feature_disabled',404)
        if body.tool is not None: raise Conflict('sync_tool_not_supported',400)
        await app.state.auth.limit(user)
        run=await call(app.state.repo.create_run,user,sid,idempotency_key,body.model_dump())
        deadline=time.monotonic()+min(20,max(0,float(os.getenv('AGENT_SYNC_WAIT_SECONDS','20'))))
        while True:
            current=await call(app.state.repo.get_run,user,run['id'])
            if current['status'] in TERMINAL or current['status']=='waiting_approval': break
            if time.monotonic()>=deadline:
                return JSONResponse(public({'run_id':run['id'],'conv_id':sid,'status':current['status'],'response':'任务仍在处理，请使用同一请求标识重试或查看任务状态。','intent':'unknown','agent_type':'general','decision':'clarify','decision_reason':'run_pending','missing_fields':[],'risk_level':'low','confirmed':False,'escalated':False,'latency_ms':(time.time()-run['created_at'])*1000,'knowledge_used':False,'tools_used':[]}),status_code=202)
            await asyncio.sleep(.15)
        from . import schema as schema
        from sqlalchemy import select
        from .objects import message_text
        def final_answer():
            with app.state.repo.engine.connect() as c:
                row=c.execute(select(schema.messages).where(schema.messages.c.run_id==run['id'],schema.messages.c.role=='assistant').order_by(schema.messages.c.seq.desc()).limit(1)).mappings().first()
            return message_text(app.state.repo.objects,row) if row else ''
        approval=await call(_pending_approval,app.state.repo,run['id'])
        spans=[]
        if current.get('trace_ref'):
            trace=json.loads(await call(app.state.repo.objects.get,current['trace_ref']))
            spans=trace.get('spans',[])
        preflight=next((span for span in spans if span.get('stage')=='preflight'),{})
        decision='confirm' if approval else preflight.get('decision','execute' if current['status']=='succeeded' else 'reject')
        selected=next((span.get('agent_type') for span in spans if span.get('stage')=='agent_selected'),None)
        evidence=next((span.get('evidence',{}) for span in spans if span.get('stage')=='rag_evidence'),{})
        if not approval and evidence.get('status') in ('insufficient','unavailable'):decision='clarify'
        return public({'run_id':run['id'],'conv_id':sid,'status':current['status'],
            'response':await call(final_answer),'approval_id':approval,'confirmation_id':str(approval) if approval else None,'error_code':current['error_code'],
            'intent':preflight.get('intent','unknown'),'agent_type':selected or preflight.get('agent_type') or 'general','decision':decision,
            'decision_reason':preflight.get('decision_reason',current['status']),'missing_fields':preflight.get('missing_fields',[]),
            'risk_level':'high' if approval else preflight.get('risk_level','low'),'action_name':preflight.get('action_name'),'confirmed':False,'escalated':False,
            'latency_ms':max(0,(time.time()-run['created_at'])*1000),
            'knowledge_used':any(span.get('stage')=='knowledge_sources' for span in spans),
            'tools_used':[span['stage'] for span in spans if span.get('tool_call_id')]})

    @app.get('/runs/{rid}')
    async def get_run(rid:int,user=Depends(principal)):
        r=await call(app.state.repo.get_run,user,rid)
        approval=await call(_pending_approval,app.state.repo,rid)
        return public(dict(r,approval_id=approval))
    @app.post('/runs/{rid}/cancel',status_code=202)
    async def cancel(rid:int,user=Depends(principal)):
        return public(await call(app.state.repo.cancel,user,rid))
    @app.post('/runs/{rid}/resume',status_code=202)
    async def resume(rid:int,idempotency_key:str=Header(),user=Depends(principal)):
        await app.state.auth.limit(user)
        r=await call(app.state.repo.get_run,user,rid)
        payload=json.loads(await call(app.state.repo.objects.get,r['input_ref']))
        return public(await call(app.state.repo.create_run,user,r['session_id'],idempotency_key,payload,rid))
    @app.post('/approvals/{aid}/decision')
    async def approve(aid:int,body:ApprovalInput,user=Depends(principal)):
        await app.state.auth.limit(user)
        return public(await call(app.state.repo.approve,user,aid,body.allow))

    @app.get('/runs/{rid}/events')
    async def stream(rid:int,request:Request,last_event_id:int=Header(default=0,ge=0),user=Depends(principal)):
        await call(app.state.repo.read_events,user,rid,last_event_id)
        ticket=secrets.token_hex(16); await app.state.auth.connect(user,ticket)
        async def generate():
            after=last_event_id; heartbeat=time.monotonic()
            try:
                while not await request.is_disconnected():
                    r,rows=await call(app.state.repo.read_events,user,rid,after)
                    for e in rows:
                        after=e['event_seq']
                        yield f"id: {after}\nevent: {e['type']}\ndata: {e['payload_preview']}\n\n"
                    if r['status'] in TERMINAL and after>=r['event_seq']: break
                    if time.monotonic()-heartbeat>=15:
                        await principal(request)  # revoked/expired sessions cannot keep streaming
                        await app.state.auth.connect(user,ticket)
                        yield ': heartbeat\n\n'; heartbeat=time.monotonic()
                    if not rows: await asyncio.sleep(.25)
            finally:
                await app.state.auth.disconnect(user,ticket)
        return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})

    @app.get('/runs/{rid}/objects/{key}')
    async def object_content(rid:int,key:str,user=Depends(principal)):
        r=await call(app.state.repo.get_run,user,rid)
        if not await call(_object_allowed,app.state.repo,r,key): raise Conflict('not_found',404)
        return Response(await call(app.state.repo.objects.get,key),media_type='application/octet-stream',headers={'Content-Disposition':'attachment','Cache-Control':'private, no-store'})
    return app

def _pending_approval(repo,rid):
    from sqlalchemy import select
    from . import schema as s
    with repo.engine.connect() as c:
        return c.execute(select(s.approvals.c.id).where(s.approvals.c.run_id==rid,s.approvals.c.status=='pending')).scalar()

def _object_allowed(repo,r,key):
    from sqlalchemy import select, or_
    from . import schema as s
    from memory.extended_schema import suppressed
    with repo.engine.connect() as c:
        if c.execute(select(suppressed.c.message_id).where(suppressed.c.user_id==r['user_id'],suppressed.c.session_id==r['session_id'],suppressed.c.created_at>=r['created_at'])).first(): return False
    if key in (r['trace_ref'],r['checkpoint_ref']): return True
    with repo.engine.connect() as c:
        return bool(c.execute(select(s.messages.c.id).where(s.messages.c.run_id==r['id'],s.messages.c.content_ref==key)).first() or c.execute(select(s.tools.c.id).where(s.tools.c.run_id==r['id'],or_(s.tools.c.output_ref==key,s.tools.c.args_ref==key))).first())

app=create_app()
