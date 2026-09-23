"""Explicit local UI test fixture; SQLite + deterministic stream, NEVER a production backend.
python scripts/hosting_browser_fixture.py --port 18002 --redis-socket /tmp/.../redis.sock
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hosting.api import create_app
from hosting.repository import Repository
from hosting.objects import Objects
from hosting.worker import Worker
from redis.asyncio import Redis
import uvicorn

async def fixture_pipeline(ctx):
    request=json.loads(await asyncio.to_thread(ctx.repo.objects.get,ctx.run['input_ref']))
    await ctx.stage('preflight')
    async def tool(): return {'status':'succeeded','output':'{"ad_count":3}','exit_code':0}
    await ctx.tool('ads_summary',{},tool,30)
    await ctx.stage('model')
    for text in ['测试模型：','建议先检查素材表现。','正在继续分析。','这段回复用于验证流式传输。']:
        await ctx.delta(text); await asyncio.sleep(.7)
    if '慢' in request['message']: await asyncio.sleep(30)
    return ctx.text

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=18002); parser.add_argument('--redis-socket',required=True)
    args=parser.parse_args()
    os.environ['AGENT_SERVICE_TOKEN']='hosting-e2e-service'
    temp=tempfile.TemporaryDirectory()
    repo=Repository('sqlite://',Objects(temp.name),'fixture-secret-32-characters-long!',test=True); repo.migrate()
    redis=Redis(unix_socket_path=args.redis_socket,decode_responses=True)
    app=create_app(repo,redis)
    @asynccontextmanager
    async def lifespan(app):
        worker=Worker(repo,fixture_pipeline,redis=redis); task=asyncio.create_task(worker.serve())
        try: yield
        finally:
            worker.stopping.set(); await task; await redis.aclose(); repo.engine.dispose(); temp.cleanup()
    app.router.lifespan_context=lifespan
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
if __name__=='__main__': main()
