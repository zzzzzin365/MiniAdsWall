"""Control plane; no Agent/model initialization in HTTP workers."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import secrets
import time
from fastapi import FastAPI, Request, Depends, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import RedisError
from .auth import Auth
from .repository import Conflict, TERMINAL, public

class SessionInput(BaseModel):
    workspace_id: str
    title: str = Field(default='New session',max_length=120)
class RunInput(BaseModel):
    message: str = Field(min_length=1,max_length=65536)
    ads: list[dict] = Field(default_factory=list,max_length=1000)
    tool: str | None = None
    argv: list[str] | None = Field(default=None,max_length=100)
class ApprovalInput(BaseModel):
    allow: bool
class LoginInput(BaseModel):
    subject: str = Field(min_length=1,max_length=190)

async def call(fn,*args,**kwargs): return await asyncio.to_thread(fn,*args,**kwargs)

def create_app(repo=None,redis=None):
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
        from sqlalchemy import select
        from . import schema as s
        def check():
            with app.state.repo.engine.connect() as c: return c.execute(select(s.versions.c.version)).scalar_one()
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
    if key in (r['trace_ref'],r['checkpoint_ref']): return True
    with repo.engine.connect() as c:
        return bool(c.execute(select(s.messages.c.id).where(s.messages.c.run_id==r['id'],s.messages.c.content_ref==key)).first() or c.execute(select(s.tools.c.id).where(s.tools.c.run_id==r['id'],or_(s.tools.c.output_ref==key,s.tools.c.args_ref==key))).first())

app=create_app()
