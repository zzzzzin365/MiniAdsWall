"""Independent worker: python -m hosting.worker. No work runs in an HTTP request."""
import asyncio
from contextlib import suppress
import json
import logging
import os
import signal
import socket
import time
import uuid
from sqlalchemy import select, update, delete
from . import schema as s
from .repository import Conflict, LostLease
from .pipeline import WaitingApproval

log=logging.getLogger(__name__)

class RunContext:
    def __init__(self,repo,run):
        self.repo,self.run=repo,run
        self.text=''; self.pending=''; self.spans=[]; self.lock=asyncio.Lock()
        self.started=time.monotonic(); self.first_delta=None
    async def stage(self,name):
        await asyncio.to_thread(self.repo.event,self.run['id'],self.run['fence_token'],'run.stage',{'stage':name})
    async def delta(self,text):
        if len((self.text+text).encode())>65536: raise Conflict('model_output_limit')
        if self.first_delta is None: self.first_delta=(time.monotonic()-self.started)*1000
        async with self.lock: self.text+=text; self.pending+=text
    async def flush(self):
        async with self.lock:
            while self.pending:
                # <= 1,000 unicode characters keeps JSON-escaped payload bounded.
                chunk,self.pending=self.pending[:1000],self.pending[1000:]
                await asyncio.to_thread(self.repo.event,self.run['id'],self.run['fence_token'],'assistant.delta',{'text':chunk})
    async def approval(self,decision):
        await asyncio.to_thread(self.repo.wait_approval,self.run['id'],self.run['fence_token'],decision)
        raise WaitingApproval()
    async def tool(self,name,args,invoke,timeout,sandbox=False):
        r=self.run; deadline=min(timeout,max(0,r['deadline_at']-time.time()))
        sandbox_id=f"agent-{r['id']}-{r['fence_token']}" if sandbox else None
        tid=await asyncio.to_thread(self.repo.tool_start,r['id'],r['fence_token'],name,args,deadline,sandbox_id)
        started=time.monotonic()
        result={'status':'failed','output':'','error_code':'tool_failed'}
        try:
            async with asyncio.timeout(deadline+ (5 if sandbox else 0)):
                result=await invoke()
        except asyncio.CancelledError:
            result.update(status='cancelled',error_code='run_cancelled'); raise
        except TimeoutError:
            result.update(status='timed_out',error_code='tool_timeout'); raise
        finally:
            self.spans.append({'stage':name,'duration_ms':(time.monotonic()-started)*1000,'status':result['status'],'tool_call_id':str(tid)})
            # The Worker cannot announce run completion until this write/cleanup has happened.
            await asyncio.to_thread(self.repo.tool_finish,r['id'],r['fence_token'],tid,result)
        if result.get('status')!='succeeded':
            if result.get('status')=='timed_out': raise TimeoutError('tool_timeout')
            raise Conflict(result.get('error_code') or 'tool_failed')
        return result

class Worker:
    def __init__(self,repo,pipeline,sandbox=None,redis=None,concurrency=4):
        self.repo,self.pipeline,self.sandbox,self.redis=repo,pipeline,sandbox,redis
        self.id=f'{socket.gethostname()}-{uuid.uuid4().hex[:12]}'
        self.concurrency=concurrency; self.stopping=asyncio.Event(); self.tasks=set()
    async def execute(self,r):
        ctx=RunContext(self.repo,r); work=asyncio.create_task(self.pipeline(ctx))
        status,error='succeeded',None
        async def supervise():
            heartbeat=0; checked=0
            while not work.done():
                if time.monotonic()-checked>=.5:
                    current=await asyncio.to_thread(self.repo.get_run,r['user_id'],r['id'])
                    if current['status']=='stopping': raise asyncio.CancelledError('cancel_requested')
                    checked=time.monotonic()
                if time.time()>=r['deadline_at']: raise TimeoutError('run_timeout')
                if time.monotonic()-heartbeat>=5:
                    await asyncio.to_thread(self.repo.heartbeat,r['id'],r['fence_token']); heartbeat=time.monotonic()
                await ctx.flush()
                await asyncio.sleep(.1)
        monitor=asyncio.create_task(supervise())
        try:
            done,_=await asyncio.wait({work,monitor},return_when=asyncio.FIRST_COMPLETED)
            if monitor in done: await monitor
            answer=await work
            await ctx.flush()
            ctx.text=answer
        except WaitingApproval:
            return
        except asyncio.CancelledError:
            status,error=('interrupted','worker_shutdown') if self.stopping.is_set() else ('cancelled','cancel_requested')
        except TimeoutError:
            status,error='timed_out','deadline_exceeded'
        except Exception as exc:
            status,error='failed',str(exc)[:80] if isinstance(exc,Conflict) else type(exc).__name__
            log.warning('run=%s failed: %s',r['id'],error)
        finally:
            work.cancel(); monitor.cancel()
            await asyncio.gather(work,monitor,return_exceptions=True)
        try:
            if status!='succeeded': await asyncio.to_thread(self.repo.stop,r['id'],r['fence_token'],status)
            if self.sandbox: await self.sandbox.cleanup(r['id'])
            trace={'run_id':str(r['id']),'queue_ms':(r['started_at']-r['created_at'])*1000,
                   'execution_ms':(time.monotonic()-ctx.started)*1000,'first_delta_ms':ctx.first_delta,
                   'tool_calls':sum('tool_call_id' in span for span in ctx.spans),'spans':ctx.spans}
            await asyncio.to_thread(self.repo.finish,r['id'],r['fence_token'],status,ctx.text,error,trace)
        except Exception:
            # Do not release capacity or report success if cleanup or persistence failed.
            log.exception('run=%s cleanup/persistence incomplete; recovery will retry',r['id'])

    async def maintain(self):
        for r in await asyncio.to_thread(self.repo.expired):
            token=await asyncio.to_thread(self.repo.fence_expired,r['id'],r['fence_token'])
            if token is None: continue
            r['fence_token']=token
            if self.sandbox: await self.sandbox.cleanup(r['id'])
            else:
                # Never recover an orphan shell run without its execution service.
                def has_shell():
                    with self.repo.engine.connect() as c:
                        return c.execute(select(s.tools.c.id).where(s.tools.c.run_id==r['id'],s.tools.c.sandbox_id.is_not(None))).first()
                if await asyncio.to_thread(has_shell): continue
            await asyncio.to_thread(self.repo.recover,r['id'],r['fence_token'])
        await asyncio.to_thread(self.expire_waiting)
        if self.redis:
            for item in await asyncio.to_thread(self.outbox_pending):
                try:
                    await self.redis.xadd('worker:wakeup',{'run_id':str(item['run_id'])},maxlen=10000,approximate=False)
                    await asyncio.to_thread(self.outbox_ack,item['id'])
                except Exception: break  # durable queue scan still works

    def expire_waiting(self):
        with self.repo.tx() as c:
            rows=list(c.execute(select(s.runs).where(s.runs.c.status.in_(['queued','waiting_approval'])).limit(1000)).mappings())
            now=time.time()
            for row in rows:
                deadline=row['queue_deadline'] if row['status']=='queued' else row['deadline_at']
                if row['status']=='waiting_approval':
                    expiry=c.execute(select(s.approvals.c.expires_at).where(s.approvals.c.run_id==row['id'],s.approvals.c.status=='pending')).scalar()
                    deadline=min(deadline,expiry) if expiry else deadline
                if deadline<=now: self.repo._finish(c,dict(row),'timed_out','waiting_timeout')
    def outbox_pending(self):
        with self.repo.engine.connect() as c:
            return list(c.execute(select(s.outbox).where(s.outbox.c.published_at.is_(None)).limit(100)).mappings())
    def outbox_ack(self,ident):
        with self.repo.engine.begin() as c:
            c.execute(update(s.outbox).where(s.outbox.c.id==ident).values(published_at=time.time()))
    async def serve(self):
        maintained=0
        try:
            while not self.stopping.is_set():
                self.tasks={t for t in self.tasks if not t.done()}
                try:
                    if time.monotonic()-maintained>5:
                        await self.maintain(); maintained=time.monotonic()
                    if len(self.tasks)<self.concurrency:
                        r=await asyncio.to_thread(self.repo.claim,self.id)
                        if r:
                            self.tasks.add(asyncio.create_task(self.execute(r))); continue
                except Exception: log.exception('Worker dispatch failed; no new tools admitted')
                try: await asyncio.wait_for(self.stopping.wait(),.5)
                except TimeoutError: pass
        finally:
            self.stopping.set()
            for task in self.tasks: task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)

async def main():
    from dotenv import load_dotenv
    load_dotenv()
    from .config import repository
    from .pipeline import Pipeline
    from .sandbox import SandboxClient
    from redis.asyncio import Redis
    from core.model_gate import close_model_gate
    repo=repository(); redis=Redis.from_url(os.environ['REDIS_URL'],socket_timeout=2)
    sandbox=SandboxClient() if os.getenv('HOSTING_EXECUTOR_URL') else None
    pipeline=Pipeline(repo,sandbox,redis)
    worker=Worker(repo,pipeline,sandbox,redis)
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT): loop.add_signal_handler(sig,worker.stopping.set)
    try: await worker.serve()
    finally:
        await pipeline.close(); await redis.aclose(); await close_model_gate(); repo.engine.dispose()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO); asyncio.run(main())
