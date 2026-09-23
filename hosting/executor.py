"""Privileged, private service. python -m uvicorn hosting.executor:app --port 8003.
Deploy exactly one per Docker host. Its socket is NEVER mounted into tool containers.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import secrets
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from .repository import Conflict, LostLease
from . import schema as s

class Execution(BaseModel):
    run_id:int
    fence_token:int
    argv:list[str]=Field(min_length=1,max_length=100)
    timeout:float=Field(gt=0,le=120)

async def command(*args,timeout=10):
    process=await asyncio.create_subprocess_exec('docker',*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(timeout): stdout,stderr=await process.communicate()
    except BaseException:
        process.kill(); await process.wait(); raise
    if process.returncode: raise RuntimeError('docker_command_failed')
    return stdout

class Executor:
    def __init__(self,repo):
        self.repo=repo; self.active=set(); self.lock=asyncio.Lock()
        self.image=os.environ.get('HOSTING_SANDBOX_IMAGE','miniadswall-sandbox:1')
    async def cleanup(self,rid):
        ids=(await command('ps','-aq','--filter',f'label=miniadswall.run={rid}')).decode().split()
        if ids: await command('rm','-f',*ids)
        # A successful response means Docker has confirmed removal of all run containers.
        if (await command('ps','-aq','--filter',f'label=miniadswall.run={rid}')).strip():
            raise RuntimeError('sandbox_cleanup_incomplete')
    def validate(self,req):
        with self.repo.tx() as c:
            r=self.repo.guard(c,req.run_id,req.fence_token)
            self.repo._session(c,r['user_id'],r['session_id'])
            tool=c.execute(select(s.tools).where(s.tools.c.run_id==req.run_id,s.tools.c.status=='running',s.tools.c.sandbox_id==f'agent-{req.run_id}-{req.fence_token}')).mappings().first()
            if not tool: raise Conflict('tool_not_registered',403)
            supplied=json.loads(tool['args_preview']) if not tool['args_ref'] else json.loads(self.repo.objects.get(tool['args_ref']))
            if supplied.get('argv')!=req.argv: raise Conflict('tool_arguments_changed',403)
            return r
    async def execute(self,req):
        if any(len(arg.encode())>8192 or '\x00' in arg for arg in req.argv): raise Conflict('invalid_argv',400)
        async with self.lock:
            count=(await command('ps','-aq','--filter','label=miniadswall.tool=true')).decode().split()
            if len(count)+len(self.active)>=8: raise Conflict('host_process_limit',429)
            name=f'agent-{req.run_id}-{req.fence_token}'
            if name in self.active: raise Conflict('tool_already_running')
            self.active.add(name)
        process=None
        try:
            r=await asyncio.to_thread(self.validate,req)
            timeout=min(req.timeout,r['deadline_at']-time.time())
            if timeout<=0: raise Conflict('run_deadline')
            snapshot=None
            if r['checkpoint_ref']:
                cp=json.loads(await asyncio.to_thread(self.repo.objects.get,r['checkpoint_ref']))
                if cp.get('workspace_ref'):
                    snapshot=base64.b64encode(await asyncio.to_thread(self.repo.objects.get,cp['workspace_ref'])).decode()
            arguments=['run','--name',name,'--label','miniadswall.tool=true','--label',f'miniadswall.run={req.run_id}',
                       '--label',f'miniadswall.fence={req.fence_token}','--label',f'miniadswall.deadline={time.time()+timeout+4}',
                       '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
                       '--user','1000:1000','--cpus','1','--memory','512m','--memory-swap','512m',
                       '--pids-limit','64','--ulimit','nofile=256:256','--ulimit','core=0:0',
                       '--tmpfs','/workspace:rw,nosuid,nodev,size=1g,uid=1000,gid=1000',
                       '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=128m,uid=1000,gid=1000',
                       '--log-driver','none','-i',self.image]
            process=await asyncio.create_subprocess_exec('docker',*arguments,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            await asyncio.to_thread(self.validate,req)  # container is still blocked on stdin; recheck the fence
            data=json.dumps({'argv':req.argv,'timeout':timeout,'snapshot':snapshot}).encode()
            async def read_bounded(reader,maximum):
                parts=[]; size=0
                while chunk:=await reader.read(65536):
                    size+=len(chunk)
                    if size>maximum: raise RuntimeError('sandbox_protocol_limit')
                    parts.append(chunk)
                return b''.join(parts)
            async def io():
                process.stdin.write(data); await process.stdin.drain(); process.stdin.close()
                out,err=await asyncio.gather(read_bounded(process.stdout,128*1024*1024),read_bounded(process.stderr,65536))
                await process.wait()
                return out,err
            output=asyncio.create_task(io())
            async def watchdog():
                while True:
                    await asyncio.sleep(.5)
                    await asyncio.to_thread(self.validate,req)
            watch=asyncio.create_task(watchdog())
            try:
                async with asyncio.timeout(timeout+7):
                    done,_=await asyncio.wait({output,watch},return_when=asyncio.FIRST_COMPLETED)
                    if watch in done: await watch
                    out,err=await output
                if process.returncode:
                    inspect=json.loads(await command('inspect',name))[0]['State']
                    return {'status':'failed','output':'','stderr':'sandbox terminated','exit_code':process.returncode,
                            'error_code':'sandbox_oom' if inspect.get('OOMKilled') else 'sandbox_failed'}
                result=json.loads(out)
                if 'snapshot' in result:
                    snapshot=base64.b64decode(result.pop('snapshot'),validate=True)
                    ref=await asyncio.to_thread(self.repo.objects.put,snapshot)
                    await asyncio.to_thread(self.repo.checkpoint,req.run_id,req.fence_token,{'workspace_ref':ref})
                return result
            finally:
                output.cancel(); watch.cancel()
                await asyncio.gather(output,watch,return_exceptions=True)
        finally:
            try: await self.cleanup(req.run_id)
            finally:
                if process and process.returncode is None:
                    process.kill(); await process.wait()
                self.active.discard(name)
    async def reap(self):
        while True:
            try:
                ids=(await command('ps','-aq','--filter','label=miniadswall.tool=true')).decode().split()
                for ident in ids:
                    info=json.loads(await command('inspect',ident))[0]; labels=info['Config']['Labels']
                    expired=float(labels['miniadswall.deadline'])<time.time()
                    if not expired:
                        try:
                            def valid():
                                with self.repo.engine.connect() as c:
                                    self.repo.guard(c,int(labels['miniadswall.run']),int(labels['miniadswall.fence']))
                            await asyncio.to_thread(valid)
                        except Exception: expired=True  # DB outage/lease loss: fail closed
                    if expired: await command('rm','-f',ident)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('executor watchdog failed')
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app):
    from .config import repository
    app.state.executor=Executor(repository()); task=asyncio.create_task(app.state.executor.reap())
    try: yield
    finally:
        task.cancel(); await asyncio.gather(task,return_exceptions=True)
        app.state.executor.repo.engine.dispose()
app=FastAPI(lifespan=lifespan)
@app.middleware('http')
async def auth(request:Request,call_next):
    token=os.getenv('HOSTING_EXECUTOR_TOKEN','')
    if not token or not secrets.compare_digest(request.headers.get('authorization','').encode(),f'Bearer {token}'.encode()):
        return JSONResponse({'error':'executor_auth_required'},status_code=401)
    return await call_next(request)
@app.exception_handler(Conflict)
async def conflict(request,exc): return JSONResponse({'error':str(exc)},status_code=exc.status)
@app.post('/execute')
async def execute(body:Execution): return await app.state.executor.execute(body)
@app.post('/runs/{rid}/cleanup')
async def cleanup(rid:int):
    await app.state.executor.cleanup(rid); return {'cleaned':True}
