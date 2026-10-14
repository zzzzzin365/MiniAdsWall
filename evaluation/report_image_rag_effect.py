"""Summarize actual image→retrieval→vision→answer traces and narrow scoring.

Structured field scoring is not a substitute for independent blind human review.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def summarise(rows):
    positive = [row for row in rows if row['answerable']]
    negative = [row for row in rows if not row['answerable']]
    good = [row for row in rows if row['status'] == 'succeeded']
    positive_good = [row for row in positive if row['status'] == 'succeeded']
    def percentile(field):
        return {'p50_ms': float(np.percentile([row[field] for row in rows], 50)),
                'p95_ms': float(np.percentile([row[field] for row in rows], 95))}
    extras = [row for row in good if set(row['answer']['source_ids']) - set(row['gold_source_ids'])]
    strict = [row for row in positive if row['score']['grounded_correct'] and
              set(row['answer']['source_ids']) == set(row['gold_source_ids'])]
    return {'requests': len(rows), 'positive_cases': len(positive), 'negative_cases': len(negative),
            'required_fields_only_rate': sum(row['answer']['status']=='answered' and all(row['score'].get('fields_correct',{}).values())
                                            for row in positive_good) / len(positive) if positive else None,
            'positive_field_and_gold_coverage_rate': sum(row['score']['grounded_correct'] for row in positive) / len(positive) if positive else None,
            'strict_fields_and_exact_sources_rate': len(strict) / len(positive) if positive else None,
            'negative_safe_abstention_rate': sum(row['score']['grounded_correct'] for row in negative) / len(negative) if negative else None,
            'false_abstention_rate': sum(row['answer']['status'] == 'insufficient' for row in positive_good) / len(positive) if positive else None,
            'extra_citation_cases': len(extras), 'failure_count': len(rows) - len(good),
            'vision_calls': sum(row['vision_called'] for row in rows),
            'vision_cache_hits': sum(row['vision_cache_hit'] for row in rows),
            'embedding_cache_hits': sum(row['embedding_cache_hit'] for row in rows),
            'evidence_ready': percentile('evidence_ready_ms'), 'end_to_end': percentile('total_ms'),
            'mean_total_ms': float(np.mean([row['total_ms'] for row in rows]))}


def plot(out, groups):
    from html import escape
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="580" viewBox="0 0 1000 580">',
           '<rect width="1000" height="580" fill="white"/>']
    def label(x, y, text, size=13, color='#343943'):
        svg.append(f'<text x="{x}" y="{y}" font-family="sans-serif" font-size="{size}" fill="{color}">{escape(text)}</text>')
    label(90, 35, 'Actual image RAG: quality and full-flow latency', 22)
    label(90, 61, 'Cold unique queries: 20 per arm. Repeat profile: same six selected queries.', 13)
    max_ms = max(group['end_to_end']['p95_ms'] for arms in groups.values() for group in arms.values())
    maximum = np.ceil(max_ms / 5000) * 5000
    for y in range(0, 101, 20):
        yy = 470 - y * 3.5
        svg.append(f'<path d="M 90 {yy} H 880" stroke="#e3e7ed"/>')
        label(55, yy+4, str(y), 12)
    for x in np.linspace(0, maximum, 6):
        xx = 90 + x / maximum * 790
        svg.append(f'<path d="M {xx} 100 V 470" stroke="#e3e7ed"/>')
        label(xx-10, 494, f'{x/1000:.0f}', 12)
    label(90, 94, 'Required-field + necessary-source pass rate (%)', 13)
    label(370, 528, 'P95 end-to-end latency (seconds)', 14)
    colors = {'A1':'#707070','F':'#2679ab','H':'#db872a','R':'#198464'}
    for profile, arms in groups.items():
        for arm, result in arms.items():
            xx = 90 + result['end_to_end']['p95_ms'] / maximum * 790
            yy = 470 - result['positive_field_and_gold_coverage_rate'] * 350
            if profile == 'cold_unique':
                svg.append(f'<circle cx="{xx}" cy="{yy}" r="6" fill="{colors[arm]}"/>')
            else:
                svg.append(f'<rect x="{xx-5}" y="{yy-5}" width="10" height="10" fill="white" stroke="{colors[arm]}" stroke-width="2"/>')
            label(xx+9, yy-10 if arm in ('R','H') else yy+18, arm+(' cold' if profile=='cold_unique' else ' repeat'), 11, colors[arm])
    svg.append('<path d="M 90 100 V 470 H 880" stroke="#647080" fill="none"/>')
    label(90, 558, 'Four related images; single run per question/arm; not independent blind grading or a production latency claim.', 12)
    svg.append('</svg>')
    (out / 'image-tradeoff.svg').write_text('\n'.join(svg))


def main(out):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['status'] == 'completed'
    cases = json.loads((out / 'cases.json').read_text())['cases']
    by_case = {case['id']: case for case in cases}
    trace = [json.loads(line) for line in (out / 'trace.jsonl').read_text().splitlines()]
    assert len(trace) == 102
    for row in trace:
        row['gold_source_ids'] = by_case[row['case_id']]['gold_source_ids']
    summary = {}
    for profile, arms in [('cold_unique', ['A1','F','H','R']), ('warm_repeat', ['A1','H','R'])]:
        summary[profile] = {arm: summarise([row for row in trace if row['profile']==profile and row['arm']==arm]) for arm in arms}
    receipts = [json.loads(path.read_text()) for path in (out / 'receipts').glob('*.json') if not path.name.endswith('.response.json')]
    calls = {model: {'requests': sum(r['model']==model for r in receipts),
                     'prompt_tokens': sum(r.get('usage',{}).get('prompt_tokens',0) for r in receipts if r['model']==model),
                     'completion_tokens': sum(r.get('usage',{}).get('completion_tokens',0) for r in receipts if r['model']==model)}
             for model in sorted({r['model'] for r in receipts})}
    captions = json.loads((out / 'captions.json').read_text())
    setup_ms = sum(row['setup_ms'] for row in captions)
    changes = []
    for case in [case for case in cases if case['split']=='test']:
        rows = {row['arm']: row for row in trace if row['profile']=='cold_unique' and row['case_id']==case['id']}
        changes.append({'case_id':case['id'],'group':case['group'],
                        'A1_pass':rows['A1']['score']['grounded_correct'],'R_pass':rows['R']['score']['grounded_correct'],
                        'H_pass':rows['H']['score']['grounded_correct'],
                        'R_vision_called':rows['R']['vision_called']})
    save(out / 'case-changes.json', changes)
    result = {'scope':'real-image understanding, caption retrieval, question-conditioned vision, same DeepSeek generator',
              'images':4,'test_cases':20,'groups':summary,'model_calls':calls,'caption_setup_ms':setup_ms,
              'money':None,'independent_human_grade':False,'overall_joint_goal_proven':False,
              'limits':['Field/citation coverage score checks required facts, not every natural-language sentence.',
                        'Strict exact-source metric is a separate diagnostic; alternative-source relevance needs independent review.',
                        'Only four related sources; explicit material names make coverage near-trivial; no vector-recall improvement claim.',
                        'Cold unique requests have no embedding-cache hits. Warm six-query profile has actual visual-evidence reuse.',
                        'Model calls and raw image reads included in online latency; initial caption/index setup separately amortized.',
                        'One run per case/arm; provider variability; not browser/API/SQL authorization/queue/concurrency benchmark.']}
    save(out / 'summary.json', result)
    plot(out, summary)
    text = ['# 真实商家图片：完整多模态 RAG 效果实验', '',
            '日期：2026-10-14。读取当前素材库 4 张真实 JPG，执行原图理解→检索→按需原图重读→DeepSeek 最终回答。没有写回业务数据库、生产索引或修改生产模型配置。', '',
            '## 实际对照', '',
            '- A1：首次 Seed 通用描述入库；标题明确引用查找＋余弦描述检索；DeepSeek 只读描述，查询 embedding 不缓存。',
            '- F：同 A1，增加独立的查询 embedding 缓存。20 道冷查询各不相同，因此本轮冷态不能靠缓存获得收益。',
            '- H：同 F，每题将候选原图送入 Seed 做问题相关理解，再由同一 DeepSeek 回答，包括简单题和缺事实题。',
            '- R：同 F，使用预先固定的问题词规则，只在视觉细节/跨图比较需要时重读；售价、材质、销量、认证题不靠外观推测。对重复问题复用按图片 hash＋模型＋完整问题＋字段＋prompt 绑定的视觉证据，DeepSeek 每次仍新生成答案。', '',
            f'视觉模型固定为 `{manifest["vision_model"]}`，文本模型固定为 `{manifest["text_model"]}`。模型已通过实际图片请求验证，不使用模拟 vision 响应。没有偷偷换模型。', '',
            '## 数据与预注册', '',
            '原图：红色苹果手串掌心展示、木台两款手串、彩色水果手串掌心展示、车内苹果挂饰。素材来源是只读 ads_business_materials＋ads_business_material_images，而非此前为空的工作台私有素材理解表。每张图记录完整素材 ID、路径、文件 SHA-256；每次视觉调用前校验原文件未变。', '',
            '本助手在看完原图后、生成测试答案前固定 4 道协议检查题和 20 道正式题：4 简单主体题、8 视觉细节题、4 跨图题、4 不能由图片确认的信息题。没有独立人工盲评，也没有拿 4 张相关图片伪装 120 题大实验。题目、接受词形、真假字段、路由规则和模型在 protocol/cases/manifest 中冻结；不根据答案调规则。', '',
            '所有组保持相同问题、初始描述、字段 schema、业务事实边界、文本模型和候选来源。明确素材名称引用对所有组一视同仁；因此本轮不把高来源覆盖率当向量召回提升。', '',
            '## 冷查询正式结果', '',
            '| 组 | 可回答字段＋必要来源通过 | 来源精确的严格通过 | 缺事实题正确拒答 | Seed重读次数 | 证据就绪P95(s) | 完整流程P50(s) | 完整流程P95(s) |',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for arm, data in summary['cold_unique'].items():
        text.append(f'| {arm} | {data["positive_field_and_gold_coverage_rate"]:.2%} ({round(data["positive_field_and_gold_coverage_rate"]*16)}/16) | {data["strict_fields_and_exact_sources_rate"]:.2%} | {data["negative_safe_abstention_rate"]:.2%} (4题) | {data["vision_calls"]} | {data["evidence_ready"]["p95_ms"]/1000:.2f} | {data["end_to_end"]["p50_ms"]/1000:.2f} | {data["end_to_end"]["p95_ms"]/1000:.2f} |')
    text.extend(['', '“字段＋必要来源通过”只要求预标注的关键字段正确、必要引用齐全，不自动证明每句话有依据。模型有时附带其他图片引用，因此额外报告要求引用集合等于目标来源的严格诊断，不能把引用 ID 合法当引用相关。失败请求计错误并保留日志。', '',
                 '## 重复问题工作负载', '',
                 '正式冷查询之后，对预先固定的 6 个问题再跑 A1/H/R；不复用最终回答，全部重新调用 DeepSeek。缓存命中只复用此前 R 的 Seed 视觉证据；这是重复问题情境，不是新问题。', '',
                 '| 组 | 字段＋必要来源通过 | Seed新增重读 | 视觉证据缓存命中 | 完整流程P50(s) | 完整流程P95(s) |',
                 '|---|---:|---:|---:|---:|---:|'])
    for arm, data in summary['warm_repeat'].items():
        text.append(f'| {arm} | {data["positive_field_and_gold_coverage_rate"]:.2%} (6题) | {data["vision_calls"]} | {data["vision_cache_hits"]} | {data["end_to_end"]["p50_ms"]/1000:.2f} | {data["end_to_end"]["p95_ms"]/1000:.2f} |')
    text.extend(['', '6 次响应的 P95 对单次 API 抖动很敏感，不能称为稳定线上 P95 改善。没有后台提前理解测试问题，也没有把首次缓存填充费用隐藏成免费；冷态 Seed 收据和 warm 引用分别记录。', '',
                 '## 可核对的差异', '',
                 '| 测试题 | A1通过 | H通过 | R通过 | R重读 |', '|---|---|---|---|---|'])
    for case in changes:
        text.append(f'| {case["case_id"]} | {case["A1_pass"]} | {case["H_pass"]} | {case["R_pass"]} | {case["R_vision_called"]} |')
    text.extend(['', '## 耗时和付费请求', '',
                 f'共 {len(receipts)} 个真实模型请求，初始 4 张图的通用理解累计 {setup_ms/1000:.2f} 秒；该离线建库工作由各组共同使用，未混进在线每题耗时。索引建库和 embedding 预热亦为共同准备工作，首次整体运行的墙钟需包含它们。', '',
                 '| 模型 | 请求 | 输入tokens（含提供方报告的图像输入） | 输出tokens |', '|---|---:|---:|---:|'])
    for model, usage in calls.items():
        text.append(f'| {model} | {usage["requests"]} | {usage["prompt_tokens"]} | {usage["completion_tokens"]} |')
    text.extend(['', '实际金额未查询账单，因此 money=null，不能说免费。每次提交前独占写收据、保存 payload hash、请求 ID、usage 和返回状态；超时或不确定提交不自动重复请求。未记录认证头、密钥或图像 base64。', '',
                 '## 可得结论与限制', '',
                 '本轮确实跑了原图视觉模型和最终文本回答，可以评价这些图片问答的完整链路。它没有跑视频、音频、PDF，也不是视觉向量检索或完整 PageIndex/TrioRAG/CANOPY 复现。', '',
                 '原图重读增加可见细节，也增加冷查询延迟；按需读取避免给简单题/不可核验事实题逐个付出视觉模型成本。重复场景可用视觉证据缓存减少重复理解，但新问题不能套用该命中速度。是否“质量＋速度双提升”必须分别对 A1 与 H 比较，不能质量对 A1、速度对 H 拼成一个虚假的联合结论。', '',
                 '只有 4 个相关素材、每题每组一次请求，不能支持可靠总体显著性或生产 P95。视觉判断题中肯定回答型样例较多，后续应补反事实、模糊图、无目标细节与更多来源，进行独立人工盲评。source ids 合法、字段匹配与整段事实支持是不同指标。', '',
                 '图表：![完整流程质量与延迟](image-tradeoff.svg)', '',
                 '复查：assets.json、cases.json、protocol.json、captions.json、corpus.json、trace.jsonl、receipts/、case-changes.json、summary.json。运行脚本不会替换生产素材库的 RAG；仅保存隔离实验产物。'])
    text.extend(['', '## 字段评分与元数据绑定修复', '',
                 '另列只检查关键字段、不要求完整引用的诊断：'+json.dumps({arm:data['required_fields_only_rate'] for arm,data in summary['cold_unique'].items()},ensure_ascii=False), '',
                 '包装比较题的预标注必要来源要求 gift+car，但只引用 car 已能支持“哪份有包装”的正面结论；基线因此在字段正确与字段＋来源指标间存在差异。保留预注册规则，注明此题引用金标准存在需要独立审阅的歧义，不事后改 gold 抬分。'])
    if (out / 'binding-fix-validation.json').exists():
        fix=json.loads((out/'binding-fix-validation.json').read_text())
        text.extend(['', '原始 H/R 的 detail-gift-surface 题读取了正确像素，但 reader 只收到 source_id，没收到素材标题映射。Seed 因不能把标题对应到 source_id 而声明不足，影响下游答案。这是实验代码缺陷，不是手掌无法识别。', '',
                     '修复请求，加入 source_name_map 和每张图的 material_name；新增绑定测试。原执行代码保存为 run_source_v1.py，hash 匹配原始 manifest。修复后仅该题单列验证，H/R 均正确回答手掌且引用 gift。4 个补测请求计入总请求/usage，主对照成绩保留原样，不能把补测拼入新的100%成绩。完整重跑需新建目录，不同代码版本禁止混用旧收据。', '',
                     '单列验证：'+json.dumps([{'arm':x['arm'],'pass':x['score']['grounded_correct']} for x in fix],ensure_ascii=False)])
    (out / 'report.md').write_text('\n'.join(text)+'\n')
    manifest.update(report_completed=True, provider_source_sha256=hashlib.sha256(Path('evaluation/image_rag_provider.py').read_bytes()).hexdigest(),
                    report_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    current_runner_sha256=hashlib.sha256(Path('evaluation/run_image_rag_effect.py').read_bytes()).hexdigest(),
                    binding_repair_separate_from_primary=(out/'binding-fix-validation.json').exists())
    save(out / 'manifest.json', manifest)
    print(json.dumps({'report':str(out/'report.md'),'groups':summary,'calls':calls},ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    main(Path(parser.parse_args().output))
