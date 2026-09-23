"""Bounded model admission. Redis mode fails closed; no local fallback."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from redis.asyncio import Redis


class ModelBusy(RuntimeError):
    pass


# Redis TIME avoids clock skew; FIFO order and expiration use separate sets.
ADMIT = """
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
local old=redis.call('GET',KEYS[4])
if old and old~=ARGV[6] then return -3 end
redis.call('SET',KEYS[4],ARGV[6])
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
local expired=redis.call('ZRANGEBYSCORE',KEYS[3],'-inf',now)
for _,id in ipairs(expired) do redis.call('ZREM',KEYS[2],id) end
redis.call('ZREMRANGEBYSCORE',KEYS[3],'-inf',now)
if not redis.call('ZSCORE',KEYS[2],ARGV[1]) then
 if ARGV[5]=='poll' then return -2 end
 if redis.call('ZCARD',KEYS[1])<tonumber(ARGV[2]) and redis.call('ZCARD',KEYS[2])==0 then
  redis.call('ZADD',KEYS[1],now+tonumber(ARGV[4]),ARGV[1]); return 1
 end
 if redis.call('ZCARD',KEYS[2])>=tonumber(ARGV[3]) then return -1 end
 local seq=redis.call('INCR',KEYS[5])
 redis.call('ZADD',KEYS[2],seq,ARGV[1]); redis.call('ZADD',KEYS[3],now+tonumber(ARGV[7]),ARGV[1])
end
local first=redis.call('ZRANGE',KEYS[2],0,0)
if first[1]==ARGV[1] and redis.call('ZCARD',KEYS[1])<tonumber(ARGV[2]) then
 redis.call('ZREM',KEYS[2],ARGV[1]); redis.call('ZREM',KEYS[3],ARGV[1])
 redis.call('ZADD',KEYS[1],now+tonumber(ARGV[4]),ARGV[1]); return 1
end
return 0
"""
RELEASE = "for i=1,3 do redis.call('ZREM',KEYS[i],ARGV[1]) end; return 1"


class RedisGate:
    def __init__(self, redis, namespace='models', limit=4, queue=32, wait=5., execution=45., grace=5.):
        if limit < 1 or queue < 0 or min(wait, execution, grace) <= 0:
            raise ValueError('Invalid model gate limits')
        self.redis, self.limit, self.queue = redis, limit, queue
        self.wait, self.execution = wait, execution
        self.lease_ms = int((execution + grace) * 1000)
        self.keys = [f'gate:{{{namespace}}}:{s}' for s in ['active', 'queue', 'deadlines', 'config', 'sequence']]
        self.fingerprint = f'{limit}:{queue}:{wait}:{execution}:{grace}'

    @asynccontextmanager
    async def slot(self):
        ticket = uuid.uuid4().hex
        try:
            async with asyncio.timeout(self.wait):
                mode = 'new'
                while True:
                    try:
                        result = await self.redis.eval(ADMIT, 5, *self.keys, ticket, self.limit, self.queue,
                                                       self.lease_ms, mode, self.fingerprint, int(self.wait*1000))
                    except Exception as exc:
                        raise ModelBusy('模型调度服务不可用') from exc
                    if result == 1:
                        break
                    if result < 0:
                        raise ModelBusy({-1: '模型等待队列已满', -2: '模型排队超时', -3: '模型调度配置不一致'}[result])
                    mode = 'poll'
                    await asyncio.sleep(.05)
            async with asyncio.timeout(self.execution):
                yield
        except TimeoutError as exc:
            raise ModelBusy('模型排队或执行超时') from exc
        finally:
            # Lost Redis connectivity leaves a bounded lease, never permanent ownership.
            try:
                await asyncio.shield(self.redis.eval(RELEASE, 3, *self.keys[:3], ticket))
            except Exception:
                pass


class LocalGate:
    def __init__(self, limit=4, queue=32, wait=5., execution=45.):
        if limit < 1 or queue < 0 or min(wait, execution) <= 0:
            raise ValueError('Invalid model gate limits')
        self.semaphore = asyncio.Semaphore(limit)
        self.capacity, self.admitted = limit + queue, 0
        self.wait, self.execution = wait, execution

    @asynccontextmanager
    async def slot(self):
        if self.admitted >= self.capacity:
            raise ModelBusy('模型等待队列已满')
        self.admitted += 1
        acquired = False
        try:
            async with asyncio.timeout(self.wait):
                await self.semaphore.acquire()
                acquired = True
            async with asyncio.timeout(self.execution):
                yield
        except TimeoutError as exc:
            raise ModelBusy('模型排队或执行超时') from exc
        finally:
            if acquired:
                self.semaphore.release()
            self.admitted -= 1


_gates = WeakKeyDictionary()


def get_model_gate():
    loop = asyncio.get_running_loop()
    if loop not in _gates:
        kwargs = dict(limit=int(os.getenv('MODEL_MAX_CONCURRENCY', '4')),
                      queue=int(os.getenv('MODEL_MAX_QUEUE', '32')),
                      wait=float(os.getenv('MODEL_QUEUE_WAIT_SECONDS', '5')),
                      execution=float(os.getenv('MODEL_EXECUTION_SECONDS', '45')))
        mode = os.getenv('MODEL_GATE_MODE', 'local')
        if mode == 'redis':
            redis = Redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=1, socket_timeout=1)
            gate = RedisGate(redis, namespace=os.getenv('MODEL_GATE_NAMESPACE', 'shared-provider'), **kwargs)
        elif mode == 'local':
            gate = LocalGate(**kwargs)
        else:
            raise ValueError('MODEL_GATE_MODE must be local or redis')
        _gates[loop] = gate
    return _gates[loop]


async def close_model_gate():
    gate = _gates.pop(asyncio.get_running_loop(), None)
    if isinstance(gate, RedisGate):
        await gate.redis.aclose()
