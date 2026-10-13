"""Evaluate current chat retrieval before evidence filtering, using frozen labels."""
import argparse, asyncio, hashlib, json, logging, os, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv
from mcp.knowledge_base import KnowledgeBase
from mcp.tool_manager import MCPToolManager, Tool

async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model')
    parser.add_argument('--provider', choices=('legacy','ark'), default='legacy')
    parser.add_argument('--top-k', type=int, default=3)
    parser.add_argument('--case-interval', type=float, default=0)
    parser.add_argument('--concurrency', type=int, default=2)
    parser.add_argument('--output', default='docs/evidence/current-chat-retrieval-results-v2.json')
    args=parser.parse_args()
    if not 1 <= args.top_k <= 20 or args.case_interval < 0 or args.concurrency < 1:
        parser.error('Invalid experiment settings')
    load_dotenv()
    output=Path(args.output)
    if output.exists(): raise FileExistsError(output)
    key=os.getenv('ANTHROPIC_API_KEY') or os.getenv('OPENROUTER_API_KEY','')
    if not key: raise RuntimeError('Model credential unavailable')
    model=args.model or os.getenv('ANTHROPIC_MODEL') or os.getenv('OPENROUTER_MODEL','claude-3-5-sonnet-20241022')
    base_url=(os.getenv('ANTHROPIC_BASE_URL') or None) if os.getenv('ANTHROPIC_API_KEY') else 'https://openrouter.ai/api'
    if args.provider == 'ark':
        key=os.getenv('ARK_API_KEY','')
        if not key: raise RuntimeError('ARK_API_KEY unavailable')
        base_url=os.getenv('ARK_BASE_URL','https://ark.cn-beijing.volces.com/api/v3')
        model=args.model or os.getenv('ARK_MODEL','')
        if not model: raise RuntimeError('ARK_MODEL unavailable')
    cases=json.loads(Path('docs/evidence/vector-retrieval-cases-v1.json').read_text())
    old=json.loads(Path('docs/evidence/vector-retrieval-results-v1.json').read_text())
    logging.basicConfig(level=logging.ERROR)
    started=time.time(); details=[]
    with tempfile.TemporaryDirectory(prefix='miniad-current-retrieval-') as tmp:
        kb=KnowledgeBase.__new__(KnowledgeBase)
        kb._client=chromadb.PersistentClient(path=tmp,settings=Settings(anonymized_telemetry=False))
        kb._open_cosine_collection()
        kb._ensure_default_docs()
        assert hashlib.sha256(json.dumps(kb._default_docs(),ensure_ascii=False,sort_keys=True).encode()).hexdigest()==old['corpus_sha256']
        # Warm the embedding runtime before testing the production 3-second stages.
        kb.search('预热广告知识检索',3)
        sem=asyncio.Semaphore(args.concurrency)
        start_lock=asyncio.Lock()
        last_started=0.0
        async def run(case):
            nonlocal last_started
            async with sem:
                async with start_lock:
                    await asyncio.sleep(max(0,last_started + args.case_interval - time.monotonic()))
                    last_started=time.monotonic()
                manager=MCPToolManager(key,base_url,model)
                trace={'queries':[],'candidate_ids':[],'model_calls':[]}
                original_create=manager._client.messages.create
                class Recorder:
                    def __init__(self): self.messages=self
                    async def create(self,**kwargs):
                        stage='rewrite' if '搜索子查询' in kwargs['messages'][0]['content'] else 'rerank'
                        t=time.monotonic()
                        try:
                            response=await original_create(**kwargs)
                            trace['model_calls'].append({'stage':stage,'status':'response','text':response.content[0].text,'usage':getattr(response,'usage',None),'elapsed':round(time.monotonic()-t,3)})
                            return response
                        except BaseException as exc:
                            trace['model_calls'].append({'stage':stage,'status':type(exc).__name__,
                                'http_status':getattr(getattr(exc,'response',None),'status_code',None),'elapsed':round(time.monotonic()-t,3)})
                            raise
                manager._client=Recorder()
                async def handler(params,context):
                    trace['queries'].append(params['query'])
                    hits=await kb.search_handler(params,context)
                    trace['candidate_ids']+= [x['id'] for x in hits]
                    return hits
                manager.register(Tool(name='knowledge_search',description='检索运营知识',handler=handler,schema={},cache_ttl=300))
                t=time.monotonic(); error=None
                try:
                    result=await asyncio.wait_for(manager.search_with_rewrite('knowledge_search',case['question'],top_k=args.top_k,check_retrieved_evidence=False),9.5)
                    retrieved=result.data if result.success else []
                    success=result.success
                except Exception as exc:
                    retrieved=[]; success=False; error=type(exc).__name__
                await asyncio.shield(original_client_close(manager, original_create))
                details.append({**case,'retrieved':retrieved,'success':success,'error':error,'elapsed':round(time.monotonic()-t,3),'trace':trace})
                output.with_suffix('.progress.json').write_text(json.dumps({'model':model,'top_k':args.top_k,'completed':len(details),'details':details},ensure_ascii=False,indent=2))
                if len(details)%10==0: print(f'Completed {len(details)}/{len(cases)}',flush=True)
        async def original_client_close(manager,method):
            # Bound method belongs to the original governed client; no secrets serialized.
            await method.__self__.close()
        await asyncio.gather(*(run(c) for c in cases))
        order={c['id']:i for i,c in enumerate(cases)}; details.sort(key=lambda c:order[c['id']])
        summary={}
        for group in ('all','direct','paraphrase','multi_document'):
            rows=[r for r in details if group=='all' or r['group']==group]
            totals={'recall':0.,'any_hit':0.,'all_gold_hit':0.,'candidate_recall':0.}
            for r in rows:
                gold=set(r['gold_ids']); n=len(gold & {x['id'] for x in r['retrieved']})
                totals['recall']+=n/len(gold); totals['any_hit']+=n>0; totals['all_gold_hit']+=n==len(gold)
                totals['candidate_recall']+=len(gold & set(r['trace']['candidate_ids']))/len(gold)
            summary[group]={'questions':len(rows),**{k:round(v/len(rows),4) for k,v in totals.items()}}
        report={'run_at_utc':datetime.now(timezone.utc).isoformat(),'scope':f'Current search_with_rewrite, real configured model and Chroma, Top{args.top_k}, 3s stage/9.5s overall limits; before evidence filtering; isolated default corpus',
                'top_k':args.top_k,'case_interval':args.case_interval,'concurrency':args.concurrency,
                'distance_metric':'cosine',
                'model':model,'provider':args.provider,'questions':len(cases),'documents':30,'chunks':kb.doc_count,'corpus_sha256':old['corpus_sha256'],
                'source_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ['mcp/knowledge_base.py','mcp/tool_manager.py','api/main.py']},
                'limitations':old['limitations']+['Synthetic questions, one stochastic model run','No final answer/evidence-gate accuracy tested'],
                'elapsed_seconds':round(time.time()-started,2),'summary':summary,'details':details}
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': asyncio.run(main())
