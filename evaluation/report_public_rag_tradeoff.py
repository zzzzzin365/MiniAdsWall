"""Summarize actual traces, clustered timing intervals and exported figures."""
import argparse
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil

import numpy as np


def svg_plot(out, real, synthetic):
    # Dependency-free export when the plotting runtime is unavailable.
    from html import escape
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="530" viewBox="0 0 1200 530">',
             '<rect width="1200" height="530" fill="white"/>']
    def label(x, y, value, size=12, color='#30343b'):
        parts.append(f'<text x="{x:.2f}" y="{y:.2f}" font-family="sans-serif" font-size="{size}" fill="{color}">{escape(value)}</text>')
    def panel(left, title, xmax, xticks, ylabel):
        label(left, 32, title, 17)
        for tick in range(0, 101, 20):
            y = 410 - tick * 3.2
            parts.append(f'<path d="M {left} {y} H {left+480}" stroke="#e6e9ed"/>')
            label(left-31, y+4, str(tick), 11)
        for tick in xticks:
            x = left + 480 * tick / xmax
            parts.append(f'<path d="M {x} 70 V 410" stroke="#e6e9ed"/>')
            label(x-7, 432, str(tick), 11)
        label(left, 59, ylabel, 11)
        label(left+100, 458, 'P95 retrieval latency (ms)', 13)
        parts.append(f'<path d="M {left} 70 V 410 H {left+480}" stroke="#606975" fill="none"/>')
    panel(65, 'Public retrieval: controlled cache workloads', 500, [0, 100, 200, 300, 400, 500], 'Author-labelled semantic Recall@5 (%)')
    panel(665, 'Synthetic 100k: exact vs ANN', 220, [0, 50, 100, 150, 200], 'Exact-neighbor Recall@5 (%)')
    colors = {'A1':'#666666', 'F':'#277da8', 'H':'#df8b27', 'R':'#188766'}
    for arm in colors:
        y = 410 - real['quality'][arm]['all']['recall_at5'] * 320
        points = [(65+real['speed'][str(r)][arm]['p95_ms']/500*480, y) for r in (0, .5, 1)]
        parts.append(f'<polyline points="{" ".join(f"{x:.2f},{yy:.2f}" for x,yy in points)}" fill="none" stroke="{colors[arm]}"/>')
        for ratio, (x, yy) in zip((0, .5, 1), points):
            parts.append(f'<circle cx="{x:.2f}" cy="{yy:.2f}" r="4" fill="{colors[arm]}"/>')
            if arm in ('F', 'R'):
                label(x+4, yy-8 if arm=='R' else yy+17, f'{arm} {ratio:.0%}', 9, colors[arm])
    for arm, value in synthetic['arms'].items():
        x, y = 665+value['p95_ms']/220*480, 410-value['neighbor_recall_at5']*320
        color = '#ab315d' if arm == 'exact' else '#277da8'
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"/>')
        label(x+5, y+16 if arm=='16384' else y-7, arm if arm=='exact' else 'ef='+arm, 10, color)
    parts.append('<path d="M 665 122 H 1145" stroke="#777777" stroke-dasharray="5 4"/>')
    label(915, 118, '90% diagnostic gate', 10)
    label(65, 490, 'A1 dense / F cached dense / H hybrid / R cached hybrid; percentages are cache hits.', 11)
    label(65, 513, 'Warm local index only. Semantic and synthetic-neighbor recall are separate measurements.', 11)
    parts.append('</svg>')
    (out / 'tradeoff.svg').write_text('\n'.join(parts))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def report(out):
    real = json.loads((out / 'public-summary.json').read_text())
    fresh = json.loads((out / 'fresh-summary.json').read_text())
    synth = json.loads((out / 'synthetic-summary.json').read_text())
    full_root = Path('data/benchmarks/general-rag-binary-v1')
    full = json.loads((full_root / 'summary.json').read_text())
    shutil.copyfile(full_root / 'summary.json', out / 'full-36m-summary.json')
    shutil.copyfile(full_root / 'manifest.json', out / 'full-36m-manifest.json')
    rows = [json.loads(line) for line in (out / 'latency.jsonl').read_text().splitlines()]
    assert len(rows) == real['test_cases'] * 2 * 3 * 4
    assert all(row['status'] == 'success' for row in rows)
    rng = np.random.default_rng(20261014)
    intervals = {}
    for ratio in (0, .5, 1):
        selected = [row for row in rows if row['profile_hit_ratio'] == ratio]
        case_ids = sorted({row['case_id'] for row in selected})
        lookup = {arm: {case: [row['ms'] for row in selected if row['case_id'] == case and row['arm'] == arm]
                        for case in case_ids} for arm in ('A1', 'R')}
        boot = []
        for _ in range(3000):
            sample = rng.integers(0, len(case_ids), len(case_ids))
            times = {arm: [v for j in sample for v in lookup[arm][case_ids[j]]] for arm in lookup}
            boot.append(np.percentile(times['R'], 95) / np.percentile(times['A1'], 95))
        base = real['speed'][str(ratio)]['A1']['p95_ms']
        improved = real['speed'][str(ratio)]['R']['p95_ms']
        intervals[str(ratio)] = {'p95_ratio': improved / base, 'relative_reduction': 1 - improved / base,
                                'p95_ratio_ci95': np.percentile(boot, [2.5, 97.5]).tolist(),
                                'actual_hit_ratio': np.mean([row['hit'] for row in selected if row['arm'] == 'R']),
                                'note': 'paired case-cluster bootstrap, two repeats, warm local index, no online distribution claim'}
    save(out / 'timing-statistics.json', intervals)
    with (out / 'tradeoff.csv').open('w') as stream:
        writer = csv.writer(stream)
        writer.writerow(['dataset', 'arm', 'cache_hit_ratio', 'p95_ms', 'recall_at5', 'recall_kind'])
        for ratio, arms in real['speed'].items():
            for arm, values in arms.items():
                writer.writerow(['public', arm, ratio, values['p95_ms'], real['quality'][arm]['all']['recall_at5'], 'author-labelled semantic'])
        for arm, values in synth['arms'].items():
            writer.writerow(['synthetic100k', arm, '', values['p95_ms'], values['neighbor_recall_at5'], 'exact neighbor agreement'])
    quality_rows = json.loads((out / 'fresh-trace.json').read_text())
    signs = [row['metrics']['optimized']['recall'] - row['metrics']['baseline']['recall']
             for row in quality_rows if row['answerable']]
    case_changes = {'improved': sum(d > 0 for d in signs), 'regressed': sum(d < 0 for d in signs), 'unchanged': sum(d == 0 for d in signs)}
    save(out / 'fresh-case-changes.json', case_changes)
    svg_plot(out, real, synth)

    text = ['# 通用知识、外部案例与合成库：实测报告', '',
            '日期：2026-10-14。未读取商家私有 SQL、素材或私有索引，没有新增付费模型请求。业务 RAG 代码没有部署这些实验优化。', '',
            '## 结论', '',
            f'公开文字/图片描述语料上，新增措辞的 30 道可回答题 Recall@5 从 {fresh["quality"]["baseline"]["all"]["recall_at5"]:.2%} 到 {fresh["quality"]["optimized"]["all"]["recall_at5"]:.2%}，提高 {fresh["recall_delta"]*100:.2f} 个百分点。查询向量缓存能加速重复查询，但对全新查询没有同等收益。随机高维合成库中，快速 ANN/二进制粗筛损失邻居召回；高召回 ANN 又比精确扫描慢。不能据此宣布整个多模态 RAG 质量和速度都已改善。', '',
            '## 语料、问题与固定项', '',
            '- 43 个片段：30 个内置通用/业务公开参考片段，13 个外部文章和图片描述片段。每条 ID、正文与来源经过允许目录校验，只复制公开快照到隔离 Chroma。没有读取整个当前私有知识集合。',
            '- 原 99 题：27 开发，72 测试（65 可回答、7 无 gold）；开发权重 0.75 从前一次开发集选择冻结，没有按新测试结果调整。原题已经使用过，不是独立盲测。',
            '- 另提前冻结 38 道新措辞题（30 可回答、8 缺事实题）。仍共享来源、单人标注，不能称为独立来源测试集。',
            '- 固定原始片段、384D 向量、查询、Top5、RRF k=60；文字检索使用标题加权中文二元词/英文词与向量排名融合。这是标题/正文混合检索，不是训练 STAIR，不是视觉编码器。',
            '- A1=纯向量无缓存；F=缓存纯向量；H=混合无缓存；R=缓存混合。与工作台原方案四组含义不同；不测原图重读、补查、SQL 授权或答案生成。', '',
            '## 公开检索质量', '',
            '| 数据集 | 可回答题 | 基线 Recall@5 | 混合 Recall@5 | 基线完整证据 | 混合完整证据 |',
            '|---|---:|---:|---:|---:|---:|']
    for name, values, a, b in [('既有通用测试', real['quality'], 'A1', 'R'), ('新增措辞', fresh['quality'], 'baseline', 'optimized')]:
        for scope in ('all', 'general', 'external'):
            x, y = values[a][scope], values[b][scope]
            text.append(f'| {name}/{scope} | {x["n"]} | {x["recall_at5"]:.2%} | {y["recall_at5"]:.2%} | {x["complete_at5"]:.2%} | {y["complete_at5"]:.2%} |')
    text.extend(['', f'既有测试集按 20 个关联题家族聚类的召回差值 95% 区间：[{real["recall_delta_ci95"][0]*100:.2f}, {real["recall_delta_ci95"][1]*100:.2f}] 个百分点；该区间不消除题目重复使用和单人标注偏差。新增题改善/恶化/不变：{case_changes}。外部 gold 按来源允许文章与图片描述替代，不代表具体图像区域答案正确。', '',
                 '## 缓存工作负载与速度', '',
                 f'每个命中工作负载 72 题×2 重复，每组 144 请求，总计 {len(rows)} 请求；随机交替组顺序，无候选差异和运行失败。计时含本地 embedding、Chroma 查找和混合排序，不含授权、在线模型、原图读取或最终生成。查询向量预计算耗时 {real["cache_precompute_seconds"]:.2f} 秒，另计缓存保存开销；冷 embedding 进程启动未计入。', '',
                 '| 受控命中比例 | A1 P95(ms) | F P95 | H P95 | R P95 | R/A1 95%区间 |',
                 '|---|---:|---:|---:|---:|---|'])
    for ratio, speed in real['speed'].items():
        interval = intervals[ratio]['p95_ratio_ci95']
        text.append(f'| {float(ratio):.0%} | {speed["A1"]["p95_ms"]:.2f} | {speed["F"]["p95_ms"]:.2f} | {speed["H"]["p95_ms"]:.2f} | {speed["R"]["p95_ms"]:.2f} | [{interval[0]:.3f}, {interval[1]:.3f}] |')
    text.extend(['', '命中比例是预先指定的缓存旁路实验，不是线上真实比例或缓存淘汰策略测试。无缓存组在不同时间块 P95 有波动；不能把所有速度差异归功于优化。区间跨 1 的配置不支持稳定提速结论。零命中时增加混合排序本身没有减少 embedding 工作量。', '',
                 '## 10 万合成向量：质量—速度取舍', '',
                 '同一 10 万条归一化随机向量、同一 200 个查询（100 扰动、100 独立），同一固定 HNSW 索引；每题每组测 2 次，查询及组顺序随机交替。精确 Top5 在本轮重新计算校验。单线程、暖 RAM，非服务并发。', '',
                 '| 搜索 | 邻居 Recall@5 | 单查询 P95(ms) |', '|---|---:|---:|'])
    for arm, values in synth['arms'].items():
        text.append(f'| {arm if arm == "exact" else "ef="+arm} | {values["neighbor_recall_at5"]:.2%} | {values["p95_ms"]:.2f} |')
    text.extend(['', '本样本按邻居召回 ≥90% 筛选时，精确扫描比满足该要求的 ANN 配置更快，因此不选择低召回配置来冒充平衡。高维随机向量不同于真实语义聚簇；不外推为 HNSW 对所有业务都无效。10 万样本也不是 3600 万索引实验。', '',
                 '## 3600 万全量：二进制粗筛/浮点精排', '',
                 f'复用并核验本轮运行完成的全量实验：同一 3600 万 float32 向量、8 个冻结查询，共用精确 Top5。精确批扫描 {full["exact_batch_seconds"]:.2f} 秒；以下为整个 8 查询批次耗时，绝非单请求 P95。二进制额外索引 {full["binary_bytes"]/1e9:.3f} GB，原始 float32 {full["source_float32_bytes"]/1e9:.3f} GB 仍保留，构建另花 {full["binary_build_seconds"]:.2f} 秒。', '',
                 '| 精排候选/查询 | 邻居 Recall@5 | 粗筛+精排批耗时(s) | 批加速比 |', '|---|---:|---:|---:|'])
    for key, values in full['arms'].items():
        text.append(f'| {key} | {values["neighbor_recall_at5"]:.2%} | {values["total_batch_seconds"]:.2f} | {values["exact_vs_binary_batch_speedup"]:.2f}× |')
    text.extend(['', '最大预算仍只有 62.5% 邻居召回，拒绝把该配置当成高质量检索替换。仅 8 查询、固定扫描顺序、OS 页缓存差异、共享粗筛及 Hamming 距离并列选择均有限制；不能给全库单请求百分位或线上 QPS。', '',
                 '## 建议与证据边界', '',
                 '1. 标题/正文混合检索在这批公开语料值得继续验证，优先扩大独立标注和来源覆盖；不是论文模型复现，也未部署到产品。',
                 '2. 缓存用于重复请求，先测真实命中分布、模型版本失效及权限边界；不能把全命中速度推广到所有请求。',
                 '3. 合成库选型先设召回下限再比较速度。当前高质量门槛下保留精确对照，二进制与低搜索宽度不能直接替代业务检索。',
                 '4. 没有生成答案评分、原图视觉输入、音频或视频理解，因此只能讲检索召回与局部检索延迟，不能讲最终答案正确率或全链路多模态双提升。', '',
                 '图表：![质量与速度曲线](tradeoff.svg)', '',
                 '复查文件：manifest.json、corpus.json、cases.json、frozen-selection.json、query-vectors.npz、quality-trace.json、latency.jsonl、timing-statistics.json、fresh-cases.json、fresh-trace.json、synthetic-trace.json、full-36m-summary.json、tradeoff.csv。'])
    (out / 'report.md').write_text('\n'.join(text) + '\n')
    manifest = json.loads((out / 'manifest.json').read_text())
    manifest.update({'report_completed': True, 'chroma_version': importlib.metadata.version('chromadb'),
                     'numpy_version': np.__version__, 'source_files': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [Path('evaluation/run_public_rag_tradeoff.py'), Path('evaluation/verify_public_rag_queries.py'), Path(__file__)]},
                     'artifact_sha256': {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                      for name in ['corpus.json', 'cases.json', 'fresh-cases.json', 'query-vectors.npz', 'frozen-selection.json', 'full-36m-manifest.json']}})
    save(out / 'manifest.json', manifest)
    print(json.dumps({'report': str(out / 'report.md'), 'fresh_changes': case_changes, 'timing_ci': intervals}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report(Path(args.output))
