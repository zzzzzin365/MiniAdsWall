"""Isolated real-reference retrieval pilot and synthetic HNSW tradeoff.

Not the planned private-media A1/F/H/R experiment. No business writes.
Gold source IDs are authored before execution from the archived catalogue.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import chromadb
import hnswlib
import httpx
from dotenv import dotenv_values
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from mcp.external_marketing import records
from api.studio import TEXT_MODEL


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def cases():
    groups = [
        ('zhihu-650576876', ['如何让达人内容与后续客户承接配合？', '有没有从保险服饰和本地服务总结创意的方法？', '用户看完广告后怎么通过私信和账号内容承接？']),
        ('zhihu-703076279', ['展示广告如何安排利益点、界面截图和按钮？', '哪份资料同时介绍搜索、户外和交通广告？', '找深紫背景配橙色试用按钮的展示广告参考']),
        ('zhihu-1932929206834140237', ['广告可以拆成哪些素材组件？', '找用汽车案例讲图片视频文案和落地页组合的资料', '哪张图左边是蔚来广告右边是广告组件拆解？']),
        ('xhs-69d858be000000001a0323a1', ['多邻国怎样把猫头鹰角色与用户情绪关联？', '找两只绿色猫头鹰配红色大标题的封面', '品牌IP如何通过玩梗和系列故事鼓励用户参与？']),
        ('xhs-693992bb000000001e030480', ['广告主建计划之前如何准备预算和用户画像？', '哪里介绍投放时间地域以及竞价参数的准备？', '奶茶门店预算教学例子在哪份投放资料里？']),
        ('x-2098850243760808268', ['营销知识库怎样连接品牌语气、证据和反馈？', '找黑底的文件组织人工审阅反馈回写流程图', '历史营销报告怎么与视觉参考和品牌手册关联？']),
        ('x-2087282914728046825', ['如何从开头痛点受众差异形式变体检查广告创意？', '找米白橙色四列课程卡片宣传图', '给创意简报建立六项评估检查表用什么资料？']),
    ]
    out = []
    for sid, questions in groups:
        for q in questions:
            out.append({'id': f'q{len(out):02}', 'query': q, 'gold_sources': [sid],
                        'family': sid, 'answerable': True})
    for q, ids in [
        ('同时找绿色猫头鹰封面和紫色橙按钮展示广告', [groups[3][0], groups[1][0]]),
        ('给我广告组件拆解与创意六维检查表两份资料', [groups[2][0], groups[6][0]]),
        ('同时参考广告主投放准备和品牌知识库反馈组织', [groups[4][0], groups[5][0]])]:
        out.append({'id':f'q{len(out):02}', 'query':q, 'gold_sources':ids, 'family':'cross-source', 'answerable':True})
    for q in ['当前商品真实销量是多少？', '这条视频的口播逐字稿是什么？', '本项目2026年十月真实ROAS是多少？',
              '汽车广告里的优惠今天是否还有效？', '私有课程完整教学正文是什么？', '这款牙刷抗菌率检测报告是什么？',
              '我们的广告预算最优出价精确是多少？']:
        out.append({'id':f'q{len(out):02}', 'query':q, 'gold_sources':[], 'family':'missing', 'answerable':False})
    return out


def main(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    rng = random.Random(20261014)
    qs = cases()
    save(out/'cases.json', qs)
    catalog = (ROOT/'data/external-marketing/catalog.json').read_bytes()
    save(out/'catalog.json', json.loads(catalog))
    entries = records()
    allowed = {e['id']:e for e in entries}
    client = chromadb.PersistentClient(path=str(ROOT/'data/chroma'), settings=chromadb.Settings(anonymized_telemetry=False))
    original = client.get_collection('knowledge_base_cosine_v1')
    rows = original.get(include=['documents','metadatas','embeddings'])
    vectors = np.asarray(rows['embeddings'], dtype=np.float32)
    ef = DefaultEmbeddingFunction()
    snapshot = chromadb.PersistentClient(path=str(out/'isolated-index'), settings=chromadb.Settings(anonymized_telemetry=False))
    index = snapshot.create_collection('pilot', embedding_function=ef, metadata={'hnsw:space':'cosine'})
    index.add(ids=rows['ids'], embeddings=vectors.tolist(), documents=rows['documents'], metadatas=rows['metadatas'])
    save(out/'corpus.json', {k:v for k,v in rows.items() if k != 'embeddings'})
    np.savez(out/'document-vectors.npz', ids=rows['ids'], vectors=vectors)
    manifest = {'status':'running', 'scope':'public-caption-retrieval-pilot', 'catalog_sha256':digest(catalog),
        'script_sha256':digest(Path(__file__).read_bytes()), 'vectors_sha256':digest(vectors.tobytes()),
        'cases_sha256':digest((out/'cases.json').read_bytes()), 'model':TEXT_MODEL, 'seed':20261014,
        'platform':platform.platform(), 'chroma_version':chromadb.__version__, 'real_sources':len({e['source_id'] for e in entries}),
        'public_chunks':len(entries), 'total_copied_chunks':len(rows['ids']), 'private_media_chunks':client.get_collection('studio_media_caption_cosine_v1').count(),
        'limitations':['Questions authored from archived source summaries; single author, no blind independent annotation.',
        '31-question convenience pilot, no calibration/held-out split; no final-answer or private-video quality measurement.',
        'Query rewrite is text-only and not Seed multimodal expansion; not A1/F/H/R production experiment.']}
    save(out/'manifest.json',manifest)
    rewrites = {}
    if args.live_rewrite:
        env = dotenv_values(ROOT/'.env')
        key = os.getenv('ARK_API_KEY') or env.get('ARK_API_KEY')
        if not key: raise RuntimeError('ARK_API_KEY unavailable')
        url = (os.getenv('ARK_BASE_URL') or env.get('ARK_BASE_URL') or 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')+'/chat/completions'
        prompt = '把每个检索问题改写成适合语义向量检索的简短中文查询。保留实体、颜色、构图、时间和缺失事实，不添加事实或猜测答案。仅返回JSON {"queries":[{"id":"...","query":"..."}]}。'
        payload = {'model':TEXT_MODEL, 'messages':[{'role':'system','content':prompt},
            {'role':'user','content':json.dumps([{'id':q['id'],'query':q['query']} for q in qs],ensure_ascii=False)}],
            'max_tokens':3500,'temperature':0,'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}}
        receipt = {'status':'submitting','request_id':digest((str(out)+'rewrite').encode()),'input_sha256':digest(json.dumps(payload,sort_keys=True).encode())}
        save(out/'rewrite-receipt.json',receipt)
        start=time.perf_counter()
        try:
            with httpx.Client(timeout=90) as http:
                response=http.post(url,headers={'Authorization':'Bearer '+key,'X-Request-ID':receipt['request_id']},json=payload)
            receipt.update(elapsed_ms=(time.perf_counter()-start)*1000,http_status=response.status_code)
            response.raise_for_status()
            data=response.json()
            save(out/'rewrite-response.json',data)
            receipt.update(status='succeeded', usage=data.get('usage'),provider_id=data.get('id'),money=None)
            if data['choices'][0].get('finish_reason')=='length': raise ValueError('rewrite truncated')
            parsed=json.loads(data['choices'][0]['message']['content'])['queries']
            rewrites={x['id']:x['query'] for x in parsed}
            if set(rewrites)!={x['id'] for x in qs} or any(not isinstance(v,str) or not v.strip() for v in rewrites.values()):
                raise ValueError('rewrite ID mismatch')
        except Exception as exc:
            receipt.update(status='failed',error_type=type(exc).__name__,elapsed_ms=(time.perf_counter()-start)*1000)
            rewrites={}
        save(out/'rewrite-receipt.json',receipt)
        print('rewrite',receipt['status'],flush=True)
    texts=list(dict.fromkeys([q['query'] for q in qs]+list(rewrites.values())))
    ef([texts[0]]) # warmup excluded from measured requests
    cache={q:ef([q])[0] for q in texts}
    np.savez(out/'query-vectors.npz',queries=texts,vectors=np.asarray([cache[q] for q in texts],dtype=np.float32))
    save(out/'rewrites.json',rewrites)
    def search(text, cached=True):
        embedding=cache[text] if cached else ef([text])[0]
        raw=index.query(query_embeddings=[embedding],n_results=3,
            where={'external_chunk_id':{'$in':list(allowed)}},include=['distances'])
        return [i for i,d in zip(raw['ids'][0],raw['distances'][0]) if 1-float(d)>=.25]
    trace=[]
    for q in qs:
        baseline=search(q['query'])
        merged={i:1/(60+r) for r,i in enumerate(baseline,1)}
        expanded=search(rewrites[q['id']]) if rewrites else []
        for r,i in enumerate(expanded,1):merged[i]=merged.get(i,0)+1/(60+r)
        fused=sorted(merged,key=lambda i:(-merged[i],i))[:3]
        trace.append({**q,'baseline':baseline,'expanded':expanded,'fused':fused if rewrites else None})
    save(out/'retrieval-trace.json',trace)
    quality={}
    for arm in ['baseline']+(['fused'] if rewrites else []):
        positives=[x for x in trace if x['answerable']]
        def found(x):return {allowed[i]['source_id'] for i in x[arm]}
        quality[arm]={'semantic_source_recall_at3':float(np.mean([len(found(x)&set(x['gold_sources']))/len(x['gold_sources']) for x in positives])),
            'complete_sources_at3':float(np.mean([set(x['gold_sources'])<=found(x) for x in positives])),
            'positive_cases':len(positives),'negative_cases':len(qs)-len(positives),
            'unanswerable_candidate_return_rate':float(np.mean([bool(x[arm]) for x in trace if not x['answerable']]))}
    latency=[]
    work=[(q,repeat) for q in qs for repeat in range(5)]
    rng.shuffle(work)
    for q,repeat in work:
        arms=['uncached_embedding','cached_embedding'];rng.shuffle(arms)
        answers={}
        for arm in arms:
            t=time.perf_counter();answers[arm]=search(q['query'],arm=='cached_embedding')
            latency.append({'id':q['id'],'repeat':repeat,'arm':arm,'ms':(time.perf_counter()-t)*1000})
        if answers[arms[0]]!=answers[arms[1]]:raise AssertionError('cache candidate mismatch')
    save(out/'latency.json',latency)
    speed={arm:{'n':len(v),'p50_ms':float(np.percentile(v,50)),'p95_ms':float(np.percentile(v,95))}
        for arm in ['uncached_embedding','cached_embedding']
        for v in [[x['ms'] for x in latency if x['arm']==arm]]}
    result={'quality':quality,'latency':speed,'cache_workload':'100% hit upper bound versus no query embedding cache; not online traffic prediction',
            'rewrite_note':'one batch call, offline frozen rewrites; not measured per-question end-to-end routing latency',
            'money':None,'final_answer_quality':None,'planned_joint_goal_proven':False}
    save(out/'summary.json',result)
    print('real retrieval',json.dumps(result,ensure_ascii=False),flush=True)
    if args.synthetic:
        synthetic(out,args.synthetic_count)
    manifest['status']='completed_pilot';save(out/'manifest.json',manifest)


def synthetic(out,count):
    rng=np.random.default_rng(20261014)
    raw=ROOT/'data/benchmarks/synthetic-36m-384-f32/shard-0000.f32'
    base=np.array(np.memmap(raw,dtype=np.float32,mode='r',shape=(1_000_000,384))[:count])
    q=np.vstack([base[rng.choice(count,100,replace=False)]+rng.normal(0,.04,(100,384)),rng.normal(0,1,(100,384))]).astype('float32')
    q/=np.linalg.norm(q,axis=1,keepdims=True)
    np.savez(out/'synthetic-queries.npz',queries=q)
    ann=hnswlib.Index(space='cosine',dim=384)
    ann.init_index(max_elements=count,ef_construction=100,M=16,random_seed=20261014)
    ann.set_num_threads(4)
    t=time.perf_counter();ann.add_items(base,np.arange(count));build=time.perf_counter()-t
    ann.save_index(str(out/'synthetic-hnsw.bin'))
    exact=[];exact_times=[]
    for query in q:
        t=time.perf_counter();scores=base@query
        top=np.argpartition(-scores,4)[:5];top=top[np.argsort(-scores[top])]
        exact.append(top.tolist());exact_times.append((time.perf_counter()-t)*1000)
    runs=[]
    for ef in [10,64,256]:
        ann.set_ef(ef);ann.knn_query(q[:5],k=5)
        for repeat in range(3):
            order=rng.permutation(len(q))
            for i in order:
                t=time.perf_counter();ids,dist=ann.knn_query(q[i:i+1],k=5,num_threads=1)
                runs.append({'ef':ef,'repeat':repeat,'query_id':int(i),'ms':(time.perf_counter()-t)*1000,
                    'ids':ids[0].tolist(),'recall':len(set(ids[0])&set(exact[i]))/5})
    save(out/'synthetic-trace.json',runs)
    summary={'synthetic':True,'vectors':count,'full_parent_vectors':36_000_000,'backend':'hnswlib direct; not Chroma API',
        'document_vectors_sha256':digest(base.tobytes()),'query_vectors_sha256':digest(q.tobytes()),'queries':200,
        'query_types':['100 noisy source-vector queries','100 independent random queries'],'build_seconds':build,
        'hnsw_index_bytes':(out/'synthetic-hnsw.bin').stat().st_size,
        'exact_p50_ms':float(np.percentile(exact_times,50)),'exact_p95_ms':float(np.percentile(exact_times,95)),
        'arms':{str(ef):{'neighbor_recall_at5':float(np.mean([r['recall'] for r in runs if r['ef']==ef])),
            'p50_ms':float(np.percentile([r['ms'] for r in runs if r['ef']==ef],50)),
            'p95_ms':float(np.percentile([r['ms'] for r in runs if r['ef']==ef],95))} for ef in [10,64,256]},
        'limitations':['100k-prefix capacity pilot, not 36M HNSW benchmark','random-vector neighbor recall, not semantic recall',
                        'single process, no service concurrency test; warm RAM index','ef arms run in fixed order; speed differences exploratory']}
    save(out/'synthetic-exact-gold.json',exact)
    save(out/'synthetic-summary.json',summary)
    print('synthetic',json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True)
    p.add_argument('--live-rewrite',action='store_true');p.add_argument('--synthetic',action='store_true')
    p.add_argument('--synthetic-count',type=int,default=100000)
    main(p.parse_args())
