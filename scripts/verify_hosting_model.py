"""One real-provider smoke task with synthetic ads and a temporary repository.
Writes metrics, not credentials. The test database is not a MySQL capacity benchmark.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from hosting.repository import Repository
from hosting.objects import Objects
from hosting.pipeline import Pipeline
from hosting.worker import Worker

async def main():
    load_dotenv()
    os.environ['MODEL_GATE_MODE']='local'
    os.environ['MODEL_EXECUTION_SECONDS']='25'
    os.environ['HOSTING_RAG_ENABLED']='false'
    model=os.getenv('OPENROUTER_MODEL','')
    if not model.endswith(':free') and model!='openrouter/free':
        raise SystemExit('This smoke script only runs the configured free OpenRouter model.')
    # Do not let a second provider configuration override the model under test.
    os.environ['ANTHROPIC_API_KEY']=os.environ['OPENROUTER_API_KEY']
    os.environ['ANTHROPIC_BASE_URL']='https://openrouter.ai/api'
    os.environ['ANTHROPIC_MODEL']=model
    with tempfile.TemporaryDirectory() as temp:
        repo=Repository('sqlite://',Objects(temp),'model-smoke-secret-32-characters!!',test=True); repo.migrate()
        identity=repo.provision('model-smoke'); session=repo.create_session(identity['user_id'],identity['workspace_id'],'synthetic task')
        repo.create_run(identity['user_id'],session['id'],'model-smoke-task-1234',{'message':'分析当前广告表现，给出三个优化动作','ads':[
            {'id':'synthetic-1','title':'合成测试广告','price':2,'clicks':3,'videos':[]}]})
        pipeline=Pipeline(repo)
        try:
            run=repo.claim('smoke-worker'); await Worker(repo,pipeline).execute(run)
            state=repo.get_run(identity['user_id'],run['id'])
            trace=json.loads(repo.objects.get(state['trace_ref'])) if state['trace_ref'] else {}
            report={'kind':'real model, synthetic input, SQLite test repository','model':model,'status':state['status'],
                    'error_code':state['error_code'],'trace':trace}
            path=Path('docs/hosting-model-smoke.json'); path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally: await pipeline.close(); repo.engine.dispose()
if __name__=='__main__': asyncio.run(main())
