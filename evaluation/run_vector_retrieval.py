"""Real Chroma retrieval on built-in corpus; author-labeled synthetic questions."""
import argparse, json, hashlib, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
import chromadb
from chromadb.config import Settings
from mcp.knowledge_base import KnowledgeBase

PAIRS = [
('广告排序分怎么计算？','为什么别人出价低但是排在我前面？'),
('高点击低出价的广告应该怎么调整？','花的钱不少却没人点，要继续加钱吗？'),
('素材A/B测试应该怎样控制变量？','想知道封面和开头哪个有效，能一起改吗？'),
('广告文案应该突出哪些信息？','标题怎么写才能让目标人群愿意点？'),
('广告数据看板应该关注哪些指标？','只看总点击量就能知道投放好不好吗？'),
('广告运营建议有哪些风险边界？','你能保证我增加预算以后一定赚钱吗？'),
('品牌广告和效果广告如何选择评价目标？','我想提升品牌认知，该拿什么评价广告？'),
('CTR怎么算？','展示了一万次，有两百人点进去，点击率多少？'),
('落地页到达率怎么计算？','广告有人点，但打开页面的人很少，怎么查？'),
('CVR转化率的分母是什么？','用户进页面后下单的比例怎么算？'),
('搜索广告和原生广告有什么区别？','搜索结果里的推广和信息流里的推广有什么不同？'),
('DSP和SSP分别为谁服务？','广告主买量和媒体卖流量分别用什么平台？'),
('合约广告怎样做库存预测和流量分配？','已经承诺给客户展示次数，怎么保证交付？'),
('CPM费用怎么算？','每千次展示收十元，两万次展示付多少钱？'),
('固定CPC单价下费用怎么算？','按有效点击收费时，为什么出价不等于实际扣费？'),
('CPA和CPS的结算区别是什么？','按注册收费和按销售收费有什么不同？'),
('CPV如何界定有效观看？','视频刚开始播放就要收钱吗？'),
('媒体方如何选择广告渠道？','我的网站接哪家广告联盟比较合适？'),
('广告负载如何兼顾收入和用户体验？','页面再多塞几个广告就能一直增加收益吗？'),
('CPC收入下降如何分环节定位？','网站广告赚的钱变少了，应该查哪些环节？'),
('广告没有展示应该按什么顺序检查？','广告建好了却一直没人看到，先查哪里？'),
('广告配置变更如何同步业务库和索引？','后台改了广告配置，线上检索却还是旧的怎么办？'),
('预算耗尽后如何停止投放并处理延迟结算？','钱已经花完了，为何还出现后续扣费？'),
('重复扣费或漏扣怎么核查？','同一笔广告费用扣了两遍，应该查什么？'),
('点击量与消耗不一致怎么解释？','页面点击很多，账单却对不上，是为什么？'),
('投放策略调整如何通过灰度实验比较效果？','怎样证明新投放策略比旧策略更赚钱？'),
('广告配置、投放和效果模块怎样区分？','广告保存成功就说明已经投放并产生效果了吗？'),
('投放调整前如何确认报表时效？','报表还没更新，我现在就改预算合适吗？'),
('广告请求失败的性能与可用性影响是什么？','接口老是超时会影响广告展示吗？'),
('分析广告业务数据需要确认什么访问范围？','能把别的品牌的广告数据拿来给我分析吗？'),
]
MULTI = [
('解释CTR和CVR各怎么算，分母有什么区别？',[7,9]),
('CPM和固定CPC分别怎样计算费用？',[13,14]),
('DSP和SSP各服务谁，网站又该怎么选择广告渠道？',[11,17]),
('广告配置保存后没有展示，要检查索引同步和投放链路哪些环节？',[20,21]),
('预算已经耗尽还有扣费，怎样排查延迟结算与重复扣费？',[22,23]),
('同时介绍演示排序公式和素材A/B测试的变量控制原则。',[0,2]),
('调整策略前如何确认报表时效，再用灰度实验评估效果？',[25,27]),
('分别解释品牌广告与效果广告的评价目标，以及页面广告负载的权衡。',[6,18]),
]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--distance',choices=('l2','cosine'),default='cosine')
    parser.add_argument('--output', default=f"docs/evidence/vector-retrieval-results-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    args=parser.parse_args()
    output=Path(args.output)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite {output}')
    started=time.time()
    kb=KnowledgeBase.__new__(KnowledgeBase)
    docs=kb._default_docs()
    assert len(docs)==len(PAIRS)
    # Freeze question labels before running retrieval. IDs match runtime seeding.
    gold_by_doc=[[hashlib.md5(f"default_ads_{d['title']}_{i}".encode()).hexdigest()
                  for i,_ in enumerate(kb._chunk_text(d['content'],500))] for d in docs]
    cases=[]
    for i,pair in enumerate(PAIRS):
        for variant,q in zip(('direct','paraphrase'),pair):
            cases.append(dict(id=f'D{i+1:02d}-{variant}',group=variant,question=q,gold_ids=gold_by_doc[i]))
    for i,(q,indices) in enumerate(MULTI):
        cases.append(dict(id=f'M{i+1:02d}',group='multi_document',question=q,
                          gold_ids=[c for j in indices for c in gold_by_doc[j]]))
    base=Path('docs/evidence'); base.mkdir(exist_ok=True)
    case_path=base/'vector-retrieval-cases-v1.json'
    if case_path.exists():
        assert json.loads(case_path.read_text()) == cases, 'Frozen questions/labels changed; not comparable to v1'
    else:
        case_path.write_text(json.dumps(cases,ensure_ascii=False,indent=2))
    with tempfile.TemporaryDirectory(prefix='miniad-rag-eval-') as tmp:
        kb._client=chromadb.PersistentClient(path=tmp,settings=Settings(anonymized_telemetry=False))
        kb._collection=kb._client.get_or_create_collection(name=f'knowledge_base_{args.distance}_experiment',metadata={'description':'MiniAdsWall Agent RAG 知识库','hnsw:space':args.distance})
        kb._ensure_default_docs()
        count=kb.doc_count
        details=[]
        for case in cases:
            hits=kb.search(case['question'],top_k=min(10,count))
            details.append({**case,'retrieved':hits})
        summary={}
        for group in ('all','direct','paraphrase','multi_document'):
            rows=[r for r in details if group=='all' or r['group']==group]
            summary[group]={}
            for k in (1,3,5,10):
                metrics=[]
                for r in rows:
                    gold=set(r['gold_ids']); ids={x['id'] for x in r['retrieved'][:k]}; found=len(gold & ids)
                    metrics.append((found/len(gold),found/k,int(found>0),int(found==len(gold))))
                summary[group][str(k)]={name:round(sum(m[i] for m in metrics)/len(metrics),4)
                    for i,name in enumerate(('recall','precision','any_hit','all_gold_hit'))}
            summary[group]['questions']=len(rows)
        result={'scope':'Isolated real Chroma retrieval using project default corpus, chunker, seeding and search; no LLM/rewrite/rerank',
                'limitations':['Author-written synthetic labels, not independent annotation','30 small built-in documents; no 10k corpus test','Related alternative chunks may be unlabelled; precision uses only designated gold chunks','No live service or user-imported corpus verified'],
                'chroma_version':chromadb.__version__,'embedding':'Chroma default ONNX all-MiniLM-L6-v2',
                'collection_metadata':kb._collection.metadata,'documents':len(docs),'chunks':count,
                'distance_metric':args.distance,
                'run_at_utc':datetime.now(timezone.utc).isoformat(),
                'source_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ('mcp/knowledge_base.py','mcp/advertising_knowledge.py')},
                'corpus_sha256':hashlib.sha256(json.dumps(docs,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
                'elapsed_seconds':round(time.time()-started,2),'summary':summary,'details':details}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='details'},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
