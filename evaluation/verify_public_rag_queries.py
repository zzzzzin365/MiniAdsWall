"""Freeze new author-labelled queries, then verify a previously selected hybrid.

New query wording, shared public sources: NOT independent source-family holdout.
No private data, online calls or test-driven selection of retrieval weights.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
import numpy as np
from evaluation.run_public_rag_tradeoff import tokens, save, sha


def freeze(out):
    corpus = json.loads((out / 'corpus.json').read_text())
    groups = []
    generic = [
        ('广告排序规则', ['两条广告报价相同，哪条可能排在更前面？', '为什么仅仅提高报价不一定能解决广告位置问题？']),
        ('出价优化原则', ['点击已经不错，调整价格时应怎样避免一下子加得太多？', '报价很贵却没人点，下一步应该检查什么？']),
        ('素材与 A/B 测试', ['同时换封面、标题和预算之后，怎样判断是哪项改动起作用？', '只有一条视频时能否立即判定哪种创意更有效？']),
        ('广告文案优化', ['标题除了提产品名字，还应该交代哪些信息？', '怎样从购买者的烦恼和使用场景组织不同文案？']),
        ('数据看板解读', ['互动总数很高能否证明购买转化也很好？', '看广告报价和名次时还应联合观察什么数据？']),
        ('广告运营风险边界', ['没有曝光和购买成本，能否保证这次投放赚钱？', '制作推广文案时能否为了点击率加入没有依据的承诺？']),
    ]
    for title, questions in generic:
        ids = [i for i, m in zip(corpus['ids'], corpus['metadatas']) if m['title'] == title and m.get('source') == 'default_ads']
        assert ids
        for q in questions:
            groups.append({'query': q, 'gold': [ids], 'scope': 'general', 'family': title, 'answerable': True})
    external = [
        ('zhihu-650576876', ['吸引人看完达人视频后，客户接下来应由哪些入口承接？', '保险或门店的广告内容如何与后续账号运营配合？']),
        ('zhihu-703076279', ['哪份案例讨论把试用入口做成明显的橙色按钮？', '我想比较车站或车身上的广告与线上展示广告，参考哪份资料？']),
        ('zhihu-1932929206834140237', ['一条完整推广广告，除了图片还包括哪些组成部分？', '找汽车广告旁边配有组成要素说明的示意图出处']),
        ('xhs-69d858be000000001a0323a1', ['哪个案例把语言学习吉祥物做成绿色角色的故事？', '用品牌角色讲故事并引导大家玩梗，哪份资料能作参考？']),
        ('xhs-693992bb000000001e030480', ['创建投放前，需要提前确定受众和花钱范围，参考什么教程？', '哪份资料使用饮品店来解释投放计划准备？']),
        ('x-2098850243760808268', ['找把人工审核意见再写回品牌营销资料的组织方案', '过去的营销报告和品牌表达风格怎样放进同一套资料流程？']),
        ('x-2087282914728046825', ['找能够逐项检查广告受众和形式变化的简报方法', '哪份参考有浅色底搭配暖橙色的四列课程展示？']),
    ]
    by_source = {}
    for i, meta in zip(corpus['ids'], corpus['metadatas']):
        if meta.get('source') == 'external_marketing_v1':
            by_source.setdefault(meta['source_url'], []).append(i)
    catalog = json.loads((ROOT / 'data/external-marketing/catalog.json').read_text())
    sources = {s['id']: by_source[s['url']] for s in catalog['sources']}
    for family, questions in external:
        for q in questions:
            groups.append({'query': q, 'gold': [sources[family]], 'scope': 'external', 'family': family, 'answerable': True})
    for q, source_ids in [
        ('同时需要吉祥物系列故事和客户看完内容后的承接方法', ['xhs-69d858be000000001a0323a1', 'zhihu-650576876']),
        ('想对照广告组成要素示意图和创意简报检查表', ['zhihu-1932929206834140237', 'x-2087282914728046825']),
        ('找投放前预算准备与人工审核反馈回写两方面的资料', ['xhs-693992bb000000001e030480', 'x-2098850243760808268']),
        ('需要线上展示广告按钮布局和绿色品牌角色封面两种案例', ['zhihu-703076279', 'xhs-69d858be000000001a0323a1']),
    ]:
        groups.append({'query': q, 'gold': [sources[i] for i in source_ids], 'scope': 'external', 'family': 'cross-source', 'answerable': True})
    for q in ['我家商品今天真实售出了多少件？', '把没有收录的短视频声音转成逐字稿',
              '给出品牌后台今天的成交收入', '公开预览里没有显示的课程付费正文是什么？',
              '广告案例里的促销现在仍在执行吗？', '告诉我这个月实际投放投入产出比',
              '证明这款商品具备某项未提供报告的功效', '算出本店当前保证盈利的最优广告报价']:
        groups.append({'query': q, 'gold': [], 'scope': 'negative', 'family': 'missing', 'answerable': False})
    rows = [{'id': f'fresh-{i:02}', **row} for i, row in enumerate(groups)]
    path = out / 'fresh-cases.json'
    if path.exists():
        raise FileExistsError(path)
    save(path, {'cases': rows, 'label_method': 'single author derives gold from archived public summaries, no model judge',
                'scope': 'new query wording on existing sources; weight previously frozen, no new tuning',
                'corpus_sha256': sha((out / 'corpus.json').read_bytes())})
    print(f'Frozen {len(rows)} fresh queries before evaluation.', flush=True)


def evaluate(out):
    labeled = json.loads((out / 'fresh-cases.json').read_text())
    assert sha((out / 'corpus.json').read_bytes()) == labeled['corpus_sha256']
    corpus = json.loads((out / 'corpus.json').read_text())
    ids, docs, metas = (corpus[k] for k in ('ids', 'documents', 'metadatas'))
    weight = json.loads((out / 'frozen-selection.json').read_text())['selected_weight']
    index = chromadb.PersistentClient(path=str(out / 'isolated-index'), settings=chromadb.Settings(anonymized_telemetry=False)).get_collection('public_tradeoff')
    cases = labeled['cases']
    embeddings = DefaultEmbeddingFunction()([row['query'] for row in cases])
    counts = [Counter(tokens(d)) + Counter({k: 4 * v for k, v in Counter(tokens(m['title'])).items()}) for d, m in zip(docs, metas)]
    df = Counter(k for row in counts for k in row)
    idf = {k: math.log((len(ids) + 1) / (n + 1)) + 1 for k, n in df.items()}
    vectors = [{k: (1 + math.log(n)) * idf[k] for k, n in row.items()} for row in counts]
    norms = [math.sqrt(sum(v * v for v in row.values())) or 1 for row in vectors]
    traces = []
    for case, embedding in zip(cases, embeddings):
        dense = index.query(query_embeddings=[embedding], n_results=10)['ids'][0]
        counts_q = Counter(tokens(case['query']))
        qvec = {k: (1 + math.log(n)) * idf[k] for k, n in counts_q.items() if k in idf}
        norm = math.sqrt(sum(v * v for v in qvec.values())) or 1
        similarity = [sum(v * row.get(k, 0) for k, v in qvec.items()) / (norm * dn) for row, dn in zip(vectors, norms)]
        fused = {i: (1 - weight) / (60 + rank) for rank, i in enumerate(dense, 1)}
        for rank, j in enumerate(np.argsort(-np.asarray(similarity), kind='stable')[:10], 1):
            if similarity[j] > 0:
                fused[ids[j]] = fused.get(ids[j], 0) + weight / (60 + rank)
        rankings = {'baseline': dense[:5], 'optimized': sorted(fused, key=lambda i: (-fused[i], i))[:5]}
        metric = {arm: {'recall': sum(bool(set(group) & set(hits)) for group in case['gold']) / len(case['gold']) if case['gold'] else None,
                        'complete': int(all(set(group) & set(hits) for group in case['gold'])) if case['gold'] else None}
                  for arm, hits in rankings.items()}
        traces.append({**case, 'rankings': rankings, 'metrics': metric})
    result = {}
    for arm in ('baseline', 'optimized'):
        result[arm] = {}
        for scope in ('all', 'general', 'external'):
            selected = [row for row in traces if row['answerable'] and (scope == 'all' or row['scope'] == scope)]
            result[arm][scope] = {'n': len(selected), 'recall_at5': float(np.mean([row['metrics'][arm]['recall'] for row in selected])),
                                'complete_at5': float(np.mean([row['metrics'][arm]['complete'] for row in selected]))}
    summary = {'cases': len(cases), 'positive_cases': 30, 'negative_cases': 8, 'weight': weight, 'quality': result,
               'recall_delta': result['optimized']['all']['recall_at5'] - result['baseline']['all']['recall_at5'],
               'limits': ['Same sources as development data, new author-labelled query formulations only.',
                          'No independent human blind annotation, no answer generation or negative-question refusal scoring.',
                          'External gold allows same-source image/article alternatives; not fine-grained image-region correctness.']}
    save(out / 'fresh-trace.json', traces)
    save(out / 'fresh-summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', choices=('freeze', 'evaluate'), required=True)
    args = parser.parse_args()
    (freeze if args.phase == 'freeze' else evaluate)(Path(args.output))
