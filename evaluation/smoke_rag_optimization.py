"""Six authored local retrieval smoke cases. Isolated DB, real CPU embeddings, no LLM."""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation.run_rag_optimization import evaluate

QUESTIONS = [
    ('广告排序规则','Pricing + (Pricing x Clicks x 0.42)','广告排序公式是什么？'),
    ('素材与 A/B 测试','一次只改变一个核心变量','A/B 测试每次能改变几个变量？'),
    ('数据看板解读','总点击量能反映整体互动规模，但不能单独代表转化效果','总点击量能证明转化好吗？'),
    ('广告运营风险边界','不能承诺点击率、转化率、收益或排名一定提升','能保证加价后收益提升吗？'),
    ('出价优化原则','高出价但点击低的广告应先优化素材和标题','出价高却没人点击怎么办？'),
    ('广告文案优化','不要使用无法证明的绝对化承诺','广告文案能写无法证明的承诺吗？')]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    target=Path(args.output)
    if target.exists():
        raise FileExistsError('refusing_to_overwrite_previous_evaluation')
    import chromadb
    from chromadb.config import Settings
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    from mcp.knowledge_base import KnowledgeBase
    from core.rag.service import RetrievalService
    with tempfile.TemporaryDirectory(prefix='miniads-rag-v2-') as tmp:
        client=chromadb.PersistentClient(path=tmp+'/vectors',settings=Settings(anonymized_telemetry=False))
        kb=SimpleNamespace(_client=client,_embedding_function=ONNXMiniLM_L6_V2(preferred_providers=['CPUExecutionProvider']))
        service=RetrievalService(kb,tmp+'/sources.sqlite')
        docs=KnowledgeBase.__new__(KnowledgeBase)._default_docs()
        receipt=service.build(docs)
        stored=service.index.documents(receipt['generation_id'])
        cases=[]
        for i,(title,excerpt,query) in enumerate(QUESTIONS):
            doc=next(d for d in stored if d['title']==title)
            start=doc['content'].index(excerpt)
            cases.append({'id':f'smoke-{i+1}','family':title,'split':'smoke','query':query,
                'gold':[{'doc_id':doc['doc_id'],'revision':doc['revision'],'start':start,'end':start+len(excerpt)}]})
        report={'kind':'real_local_embedding_retrieval_smoke',
            'scope':'Six authored smoke cases; not frozen release evaluation. No answer generation or paid calls.',
            'embedding':'ONNX all-MiniLM-L6-v2 CPU','receipt':receipt,
            'manifest':service.index.manifest(receipt['generation_id']),'cases':cases,'groups':{}}
        for mode in ('vector','hybrid'):
            report['groups'][mode]=asyncio.run(evaluate(service,cases,mode,receipt['generation_id']))
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'documents':len(docs),'chunks':receipt['chunks'],'groups':{
            m:{'attempts':len(rows),'failed':sum(r['status']!='succeeded' for r in rows),
               'coverage':[r.get('evidence_span_coverage_at8') for r in rows]} for m,rows in report['groups'].items()}}))


if __name__=='__main__':
    main()
