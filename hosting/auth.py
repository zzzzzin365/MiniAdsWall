"""Opaque Redis login sessions and distributed HTTP/SSE limits. No memory fallback."""
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from .repository import Conflict

RATE = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],120) end; return n"
CONNECTION = """
local t=redis.call('TIME'); local now=tonumber(t[1])
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
if not redis.call('ZSCORE',KEYS[1],ARGV[1]) and redis.call('ZCARD',KEYS[1])>=5 then return 0 end
redis.call('ZADD',KEYS[1],now+45,ARGV[1]); redis.call('EXPIRE',KEYS[1],60); return 1
"""
class Auth:
    def __init__(self,redis): self.redis=redis
    def key(self,token): return 'auth:session:'+hashlib.sha256(token.encode()).hexdigest()
    async def issue(self,user):
        token=secrets.token_urlsafe(32)
        await self.redis.set(self.key(token),json.dumps({'user_id':user,'expires_at':time.time()+7*86400}),ex=86400)
        return token
    async def resolve(self,token):
        if not token or len(token)>200: raise Conflict('login_required',401)
        raw=await self.redis.get(self.key(token))
        if not raw: raise Conflict('login_expired',401)
        session=json.loads(raw); remaining=int(session['expires_at']-time.time())
        if remaining<=0: raise Conflict('login_expired',401)
        await self.redis.expire(self.key(token),min(86400,remaining))
        return int(session['user_id'])
    async def logout(self,token): await self.redis.delete(self.key(token))
    async def limit(self,user):
        n=await self.redis.eval(RATE,1,f'ratelimit:{user}:{int(time.time()//60)}')
        if n>10: raise Conflict('run_rate_limit',429)
    async def connect(self,user,ticket):
        if not await self.redis.eval(CONNECTION,1,f'sse:{user}',ticket): raise Conflict('sse_limit',429)
    async def disconnect(self,user,ticket): await self.redis.zrem(f'sse:{user}',ticket)
