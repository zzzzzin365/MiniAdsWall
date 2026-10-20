"""Authenticated compatibility facade over durable runs, never a second executor."""
import hashlib
import os
import secrets
import time
import asyncio
import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse

def resolve_conversation(repo,subject,alias):
    from sqlalchemy import select,insert
    from hosting import schema as hs
    from hosting.repository import uid
    from .sync_schema import aliases
    identity=repo.provision(subject)
    user,workspace=identity['user_id'],identity['workspace_id']
    if alias.isdecimal():
        with repo.engine.connect() as c:session=repo._session(c,user,int(alias))
        return session['id']
    hashed=hashlib.sha256(alias.encode()).hexdigest()
    with repo.tx() as c:
        old=c.execute(select(aliases.c.session_id).where(aliases.c.user_id==user,aliases.c.alias_hash==hashed)).scalar()
        if old:
            repo._session(c,user,old)
            return old
        # Same session quota as the normal control plane; no hidden bypass.
        from sqlalchemy import func
        if c.execute(select(func.count()).select_from(hs.sessions).where(hs.sessions.c.user_id==user)).scalar()>=100:
            from hosting.repository import Conflict
            raise Conflict('session_limit',429)
        sid=uid();now=time.time()
        c.execute(insert(hs.sessions).values(id=sid,user_id=user,workspace_id=workspace,title='同步会话',status='active',
            next_seq=0,version=1,updated_at=now,created_at=now))
        c.execute(insert(aliases).values(id=uid(),user_id=user,alias_hash=hashed,session_id=sid))
        return sid

async def durable_chat(body,request,client=None,repo=None):
    if request is None:raise HTTPException(401,'service_auth_required')
    expected=os.getenv('AGENT_SERVICE_TOKEN','')
    supplied=request.headers.get('Authorization','')
    if not expected or not secrets.compare_digest(supplied.encode(),('Bearer '+expected).encode()):
        raise HTTPException(401,'service_auth_required')
    key=request.headers.get('Idempotency-Key') or request.headers.get('X-Request-ID','')
    if not 16<=len(key)<=100:raise HTTPException(400,'invalid_idempotency_key')
    headers={k:request.headers.get(k,'') for k in ('Authorization','X-Agent-Session')}
    headers['Idempotency-Key']=key
    alias=body.conv_id or 'sync-'+key
    if len(alias)>190:raise HTTPException(400,'invalid_conversation_id')
    owned_repo=False
    async def send(http):
        nonlocal repo,owned_repo
        if not headers['X-Agent-Session']:
            # Only the service-authenticated gateway can assert a principal; the public browser cannot.
            identity=await http.post('/auth/session',json={'subject':body.user_id},headers={'Authorization':supplied})
            if identity.status_code!=200:return JSONResponse(identity.json(),status_code=identity.status_code)
            headers['X-Agent-Session']=identity.json()['session_token']
            if repo is None:
                from hosting.config import repository
                repo=await asyncio.to_thread(repository);owned_repo=True
            try:sid=await asyncio.to_thread(resolve_conversation,repo,body.user_id,alias)
            except Exception as exc:
                from hosting.repository import Conflict
                if isinstance(exc,Conflict):raise HTTPException(exc.status,str(exc)) from exc
                raise
        else:
            if not alias.isdecimal():raise HTTPException(400,'hosted_token_requires_numeric_session')
            sid=int(alias)
        payload={'message':body.message,'ads':body.ads or []}
        if body.ad_context is not None:payload['ad_context']=body.ad_context
        response=await http.post('/sessions/'+str(sid)+'/chat',json=payload,headers=headers)
        value=response.json()
        if response.status_code in (200,202):value['conv_id']=str(sid) if body.conv_id and body.conv_id.isdecimal() else alias
        return JSONResponse(value,status_code=response.status_code)
    try:
        if client is not None:return await send(client)
        base=os.getenv('AGENT_HOSTING_URL','http://127.0.0.1:8002').rstrip('/')
        async with httpx.AsyncClient(base_url=base,timeout=27) as http:return await send(http)
    except httpx.HTTPError as exc:raise HTTPException(503,'durable_chat_unavailable_retry_same_key') from exc
    finally:
        if owned_repo and repo is not None:await asyncio.to_thread(repo.engine.dispose)
