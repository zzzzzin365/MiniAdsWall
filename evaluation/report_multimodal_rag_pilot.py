"""Summarize measured pilot; never fills missing full-experiment metrics."""
import json
from pathlib import Path
import random
import time
import numpy as np
import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from run_multimodal_rag_pilot import synthetic

P=Path('docs/validation/multimodal-rag-ab/20261014-pilot-v1')
def load(name):return json.loads((P/name).read_text())
def save(name,value):(P/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))

trace=load('retrieval-trace.json'); corpus=load('corpus.json'); catalog=load('catalog.json')
by_id={i:m for i,m in zip(corpus['ids'],corpus['metadatas'])}
sources=[s['id'] for s in catalog['sources']]
components=[{sources[0]},{sources[1],sources[3]},{sources[2],sources[6]},{sources[4],sources[5]}]
groups=[]
for c in components:
    group=[]
    for row in trace:
        if row['answerable'] and set(row['gold_sources'])<=c:
            vals=[]
            for arm in ['baseline','fused']:
                found={by_id[i]['source_url'] for i in (row[arm] or [])}
                gold={s['url'] for s in catalog['sources'] if s['id'] in row['gold_sources']}
                vals.append(len(found&gold)/len(gold))
            group.append(vals[1]-vals[0])
    groups.append(group)
rng=np.random.default_rng(20261014)
boot=[np.mean([v for i in rng.integers(0,4,4) for v in groups[i]]) for _ in range(10000)]
stats={'semantic_recall_delta':float(np.mean([v for g in groups for v in g])),
       'family_cluster_bootstrap_ci95':np.percentile(boot,[2.5,97.5]).tolist(),'connected_family_clusters':4,
       'limits':'Only four independent source-family components; CI exploratory, not robust significance evidence.'}
save('statistics.json',stats)

ef=DefaultEmbeddingFunction()
index=chromadb.PersistentClient(path=str(P/'isolated-index'),settings=chromadb.Settings(anonymized_telemetry=False)).get_collection('pilot',embedding_function=ef)
data=np.load(P/'query-vectors.npz');cache=dict(zip(data['queries'].tolist(),data['vectors'].tolist()))
ids=[i for i,m in zip(corpus['ids'],corpus['metadatas']) if m.get('source')=='external_marketing_v1']
work=[(x,r) for x in trace for r in range(5)]
py=random.Random(20261014);py.shuffle(work)
mixed=[]
for ratio in [.25,.5,.75]:
    hits=[True]*round(len(work)*ratio)+[False]*(len(work)-round(len(work)*ratio));py.shuffle(hits)
    for (row,r),hit in zip(work,hits):
        t=time.perf_counter();vec=cache[row['query']] if hit else ef([row['query']])[0]
        index.query(query_embeddings=[vec],n_results=3,where={'external_chunk_id':{'$in':ids}},include=['distances'])
        mixed.append({'ratio':ratio,'id':row['id'],'repeat':r,'hit':hit,'ms':(time.perf_counter()-t)*1000})
save('mixed-cache-trace.json',mixed)
cache_summary={str(r):{'requests':len(v),'actual_hit_ratio':sum(x['hit'] for x in v)/len(v),
    'p50_ms':float(np.percentile([x['ms'] for x in v],50)),'p95_ms':float(np.percentile([x['ms'] for x in v],95))}
    for r in [.25,.5,.75] for v in [[x for x in mixed if x['ratio']==r]]}
save('mixed-cache-summary.json',cache_summary)

s=load('summary.json');syn=load('synthetic-summary.json');receipt=load('rewrite-receipt.json')
lines=['# 多模态 RAG 首轮实测报告','', '日期：2026-10-14。结论：**未证明质量与速度同时提升**。完成的是现有真实外部描述语料的检索小试验，以及合成向量的 HNSW 参数对照；不是计划中的内部素材 A1/F/H/R 全实验。','',
'## 数据与实际执行范围','',
'- 本地业务数据库只读复查：素材记录 0、成功理解任务 0、当前可用素材 0；内部素材 Chroma 索引 0。不是仅由索引为空推断数据库为空。',
'- 真实目录 7 个来源、13 个文章/图片描述片段；复制整个 cosine 集合的 43 条向量到隔离索引，检索时只允许该目录 13 条 ID。6 张归档图片本轮没有直接输入视觉模型。',
'- 31 道题：24 可回答（21 单来源、3 双来源）、7 缺事实题。金标准在运行前基于归档摘要编写，单人标注、无独立盲评和开发/测试拆分；偏向已知小语料，只能作为探索性试验。',
'- 原查询与 DeepSeek 实际生成的改写查询分别 Top3，阈值 0.25，RRF k=60，最终同为 Top3片段，按来源计算语义召回。改写未输入语料或 gold。没有当前附件，所以不是 TrioRAG 的视觉查询路线。',
f'- 在线 DeepSeek 改写请求 1 次，耗时 {receipt["elapsed_ms"]:.1f}ms，usage={json.dumps(receipt.get("usage"),ensure_ascii=False)}。请求 ID、响应、prompt hash 已保存。金额未知，记 null；未调用最终答案生成或自动 judge。',
'', '## 真实语料召回','', '| 方案 | 来源 Recall@3 | 找齐必要来源 | 可回答题 |', '|---|---:|---:|---:|']
for arm,name in [('baseline','原查询'),('fused','原查询 + 模型改写 + RRF')]:
    q=s['quality'][arm];lines.append(f'| {name} | {q["semantic_source_recall_at3"]:.2%} | {q["complete_sources_at3"]:.2%} | 24 |')
lines += ['', f'改写融合的召回差值为 {stats["semantic_recall_delta"]*100:.2f} 个百分点；4 个关联来源家族聚类 bootstrap 区间为 [{stats["family_cluster_bootstrap_ci95"][0]*100:.2f}, {stats["family_cluster_bootstrap_ci95"][1]*100:.2f}] 个百分点。独立家族太少，区间仅作探索。',
'两个方案都为 7 道不可回答题返回候选（100%）。这不是 100% 乱答率：本轮没有生成答案；它说明相似度阈值不能替代事实充足性判断。不能给出答案正确率或引用支持率。',
'', '## 查询 embedding 缓存速度','', '| 工作负载 | 请求数 | P50 ms | P95 ms |','|---|---:|---:|---:|']
for arm,label in [('uncached_embedding','每次计算 embedding'),('cached_embedding','100% embedding 缓存命中')]:
    x=s['latency'][arm];lines.append(f'| {label} | {x["n"]} | {x["p50_ms"]:.2f} | {x["p95_ms"]:.2f} |')
for ratio,x in cache_summary.items():lines.append(f'| 受控约 {float(ratio):.0%} 命中 | {x["requests"]} | {x["p50_ms"]:.2f} | {x["p95_ms"]:.2f} |')
a=s['latency']['uncached_embedding']['p95_ms'];b=s['latency']['cached_embedding']['p95_ms']
lines += ['',f'全命中 P95 降低 {(a-b)/a:.2%}，155 对查询候选/排名完全一致。25/50/75% 命中比例是模拟工作负载，不是实际业务命中率；补充组按比例顺序跑，未做跨比例随机区组，因此只作描述统计。',
'计时包括本地 embedding 和隔离 Chroma 的 query；不包括 Koa 授权、同步检查、查询改写、原图重读、判断器和最终生成，也没有并发压测。缓存没有接入业务代码。全命中结果是速度上限，不能当端到端改善。',
'', '## 合成向量：速度与最近邻召回的取舍','',
f'从既有 3600 万 float32 向量的首分片取前 {syn["vectors"]:,} 条。200 个不同查询：100 个扰动来源向量、100 个独立随机向量；精确扫描 Top5 作 gold。HNSW 单独建库用时 {syn["build_seconds"]:.2f}s，索引 {syn["hnsw_index_bytes"]:,} 字节。固定同一索引，仅调整搜索宽度 ef。',
'', '| 搜索方式 | 最近邻 Recall@5 | P50 ms | P95 ms |','|---|---:|---:|---:|',
f'| 精确扫描 | 100%（gold定义） | {syn["exact_p50_ms"]:.3f} | {syn["exact_p95_ms"]:.3f} |']
for ef,x in syn['arms'].items():lines.append(f'| HNSW ef={ef} | {x["neighbor_recall_at5"]:.2%} | {x["p50_ms"]:.3f} | {x["p95_ms"]:.3f} |')
lines += ['', '搜索更宽时找回更多最近邻，但耗时也增加；本批随机高维向量中最高召回仍很低，不能选择低 ef 的快速结果作为合格质量方案。它证明需要先设质量底线，再选满足底线的最快配置。',
'这里是 RAM 中直接 hnswlib 的 10 万条试验，不是 Chroma 服务或 3600 万 HNSW 容量证明，不是广告语义召回。HNSW查询单线程；精确扫描使用运行时 BLAS/系统配置（请求线程上限4），两者不是等算力后端公平赛。ef 固定顺序、3次/查询，速度比较探索性。',
'', '## 决策与未完成项','',
'1. 不接受当前文本改写融合为召回优化：本批题目点估计下降。先检查逐题丢失的正确来源和同一来源多片段挤占，校准后用独立测试集重新验证，不能回改本批 gold。',
'2. embedding 缓存值得继续验证，但需接入真实版本/授权边界、测真实命中率，再跑证据准备及端到端计时；当前不能宣布总体提速。',
'3. ANN 参数不能只选最快值；本批高维随机向量里现有三个配置都不能满足高召回要求。需独立扩展参数或保留精确检索对照。',
'4. 内部媒体数据为空，尚未完成120题标注、原图重读、按需路由、补查、四组在线质量和速度联合验收。对应指标保留 null，不填估计或模拟成绩。',
'5. 8 个既有 Python 专项测试通过；这验证隔离、版本和引用等功能，不证明生成答案质量。业务服务及原知识集合未实施优化改动。',
'', '## 可复查文件','',
'`manifest.json`、`cases.json`、`catalog.json`、`corpus.json`、向量 npz、`retrieval-trace.json`、`latency.json`、`rewrite-receipt.json`/`rewrite-response.json`、`statistics.json`、`mixed-cache-trace.json`、`synthetic-exact-gold.json`、`synthetic-trace.json` 和各 summary 均位于本目录。',
'', '运行脚本：`evaluation/run_multimodal_rag_pilot.py`；汇总与补充缓存工作负载：`evaluation/report_multimodal_rag_pilot.py`。新建输出目录运行才会发起新的付费改写，默认不调用模型；本轮不要为重画报告重复提交。']
(P/'report.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'statistics':stats,'mixed_cache':cache_summary,'report':str(P/'report.md')},ensure_ascii=False),flush=True)
