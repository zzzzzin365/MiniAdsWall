"""Read-only audit of old E3/E4; write new reports without changing old scores."""
import argparse
import json
from pathlib import Path
import random

import numpy as np

from evaluation.image_rag_provider import digest, write
from evaluation.visual_observation_v3 import (
    ROOT, DEFAULT, OLD, answer_contract, answer_errors, checked_route,
)


def load_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def timing(rows):
    result = {'n': len(rows)}
    for field in ['total_ms', 'retrieval_ms', 'reader_ms', 'route_ms', 'vision_ms', 'answer_ms', 'generation_ms']:
        values = [r[field] for r in rows if isinstance(r.get(field), (float, int))]
        if values:
            result[field] = {'n': len(values), 'p50_s': float(np.percentile(values, 50) / 1000),
                             'p95_s': float(np.percentile(values, 95) / 1000)}
    result['actual_vision_calls'] = sum(r.get('vision_called', False) for r in rows)
    return result


def audit(out):
    out.mkdir(parents=True, exist_ok=True)
    e3_path = ROOT / 'docs/validation/multimodal-rag-ab/20261014-images-v1/trace.jsonl'
    e3 = [r for r in load_rows(e3_path) if r.get('profile') == 'cold_unique' and r['arm'] in ['H', 'R']]
    stats = {}
    for group, categories in [('all', None), ('requires_visual_detail', ['visual', 'cross']),
                              ('caption_or_business_fact', ['simple', 'missing'])]:
        stats[group] = {arm: timing([r for r in e3 if r['arm'] == arm and
                                     (categories is None or r['group'] in categories)]) for arm in ['H', 'R']}
    raw = load_rows(OLD / 'trace.jsonl')
    tests = [r for r in raw if r['split'] == 'test' and r['episode'] == 'main']
    arms = {cid: {r['arm'] for r in tests if r['case_id'] == cid} for cid in {r['case_id'] for r in tests}}
    matched = {cid for cid, seen in arms.items() if seen == {'A', 'B', 'C0', 'C1'}}
    gold = {c['case_id']: c for c in json.loads((OLD / 'cases-gold.json').read_text())}
    old_scores = {(r['case_id'], r['arm']): r for r in json.loads((OLD / 'scores.json').read_text())
                  if r['split'] == 'test' and r['episode'] == 'main'}
    diagnostics = []
    for r in tests:
        if r['case_id'] not in matched or r['arm'] not in ['B', 'C1'] or not gold[r['case_id']]['gold']['answerable']:
            continue
        errors = answer_errors(r.get('answer'), answer_contract(r['question']), r['asset_id'])
        item = {'case_id': r['case_id'], 'arm': r['arm'], 'question': r['question'],
                'original_pass': old_scores[r['case_id'], r['arm']]['field_and_source_pass'],
                'new_contract_format_errors': errors,
                'historical_boolean_in_nonboolean_field': (
                    not isinstance(gold[r['case_id']]['gold']['expected'], bool) and
                    isinstance((r.get('answer') or {}).get('value'), bool)),
                'answer': r.get('answer'), 'manual_semantic_review': 'pending'}
        if r.get('route'):
            guarded = checked_route(r['route'], answer_contract(r['question']), r['evidence_before'])
            item.update(old_route=r['route']['decision'], guarded_route=guarded['decision'],
                        guard=guarded.get('guard'))
        diagnostics.append(item)
    summary = {'status': 'offline_historical_diagnostic_not_new_model_results',
               'e3': stats, 'e4_matched_cases': len(matched),
               'e4_answerable_per_arm': len([d for d in diagnostics if d['arm'] == 'B']),
               'e4_original_pass_counts': {arm: sum(d['original_pass'] for d in diagnostics if d['arm'] == arm)
                                            for arm in ['B', 'C1']},
               'e4_format_error_counts': {arm: sum(d['historical_boolean_in_nonboolean_field'] for d in diagnostics if d['arm'] == arm)
                                           for arm in ['B', 'C1']},
               'e4_visible_false_abstention_route_count': sum(d.get('guard') == 'missing_visible_fact_requires_read'
                                                              and not d['original_pass']
                                                              for d in diagnostics),
               'e4_route_overrides_including_previously_passed': sum(d.get('guard') == 'missing_visible_fact_requires_read'
                                                                      for d in diagnostics),
               'router_seconds_in_24_c1_requests': sum(r.get('route_ms', 0) for r in tests
                                                        if r['case_id'] in matched and r['arm'] == 'C1') / 1000,
               'old_results_unchanged': True,
               'input_hashes': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in
                                [e3_path, OLD / 'trace.jsonl', OLD / 'scores.json', OLD / 'report.md']},
               'independent_semantic_review_completed': False}
    write(out / 'historical-audit.json', summary)
    write(out / 'historical-failure-review.json', [d for d in diagnostics if not d['original_pass']])
    # Anonymous review queue; no post-hoc semantic rescore silently replaces primary results.
    queue = []
    mapping = []
    rng = random.Random(20261015)
    shuffled = [r for r in tests if r['case_id'] in matched]; rng.shuffle(shuffled)
    for index, r in enumerate(shuffled, 1):
        anonymous_id = 'review-' + str(index).zfill(3)
        queue.append({'review_id': anonymous_id, 'case_id': r['case_id'], 'asset_id': r['asset_id'],
                      'question': r['question'], 'response': r.get('answer'),
                      'expected': gold[r['case_id']]['gold']['expected'],
                      'support_region': gold[r['case_id']]['gold']['support_region'],
                      'body_supported': None, 'value_correct': None, 'extra_false_claim': None,
                      'citation_supported': None, 'reviewer': None})
        mapping.append({'review_id': anonymous_id, 'arm': r['arm']})
    write(out / 'semantic-review-blinded.json', queue)
    write(out / 'semantic-review-mapping.json', mapping)
    return summary


def report_run(folder):
    """Keep format, field, source and unreviewed natural-language metrics separate."""
    trace = load_rows(folder / 'trace.jsonl')
    protocol = json.loads((folder / 'protocol.json').read_text())
    gold = {c['case_id']: c['gold'] for c in json.loads((folder / 'cases-gold.json').read_text())}
    observed = {}
    for row in trace:
        observed.setdefault((row['repeat'], row['case_id']), set()).add(row['arm'])
    matched = {key for key, arms in observed.items() if arms == set(protocol['arms'])}
    paired_trace = [r for r in trace if (r['repeat'], r['case_id']) in matched]
    grouped = {}
    for split in ['development', 'test']:
        for arm in protocol['arms']:
            rows = [r for r in paired_trace if r['arm'] == arm and r['split'] == split]
            if not rows: continue
            positive = [r for r in rows if gold[r['case_id']]['answerable']]
            negative = [r for r in rows if not gold[r['case_id']]['answerable']]
            def passes(row):
                if row.get('status') != 'succeeded': return False
                a = row.get('answer') or {}; expected = gold[row['case_id']]['expected']
                if expected is None: return a.get('status') == 'insufficient' and a.get('value') is None
                value = a.get('value')
                correct = (value is expected if isinstance(expected, bool) else isinstance(value, str) and
                           any(alias.lower() in value.lower() for alias in expected))
                return a.get('status') == 'answered' and correct and row['asset_id'] in a.get('source_ids', [])
            grouped[split + ':' + arm] = {
                **timing(rows), 'answerable_n': len(positive), 'not_provable_n': len(negative),
                'field_source_pass_rate': sum(passes(r) for r in positive) / len(positive) if positive else None,
                'not_provable_pass_rate': sum(passes(r) for r in negative) / len(negative) if negative else None,
                'format_pass_rate': sum(r.get('answer') is not None and not r.get('format_errors', []) for r in rows) / len(rows),
                'body_correctness': 'pending_independent_review',
                'by_timing_group': {group: timing([r for r in rows if r['timing_group'] == group])
                                    for group in ['requires_visual_detail', 'caption_or_business_fact']}}
    receipts = [json.loads(p.read_text()) for p in (folder / 'receipts').glob('*.json')
                if not p.name.endswith('.response.json')]
    initial = json.loads((folder / 'initial-observations.json').read_text())
    assets = {r['asset_id'] for r in trace}
    inherited = {aid: {'elapsed_ms': initial[aid]['receipt']['elapsed_ms'],
                       'usage': initial[aid]['receipt'].get('usage')} for aid in assets}
    result = {'status': json.loads((folder / 'manifest.json').read_text())['status'],
              'coverage': {'attempted_rows': len(trace), 'paired_cases_including_repeats': len(matched),
                           'unpaired_rows': len(trace) - len(paired_trace),
                           'failures_retained': sum(r['status'] != 'succeeded' for r in trace),
                           'comparison': 'all arms attempted on identical repeat/case; failures included'},
              'groups': grouped, 'requests': len(receipts), 'money': None,
              'usage': {k: sum((r.get('usage') or {}).get(k, 0) for r in receipts)
                        for k in ['prompt_tokens', 'completion_tokens']},
              'reused_initial_cost_per_reuse_arm': inherited,
              'repeats_are_not_more_independent_images': True, 'independent_review': False}
    write(folder / 'summary-v3.json', result)
    return result


def main(out):
    historical = audit(out)
    runs = {name: report_run(out / name) for name in ['e3', 'e4'] if (out / name / 'manifest.json').exists()}
    availability = json.loads((out / 'availability.json').read_text()) if (out / 'availability.json').exists() else {}
    live_rows = sum(len(load_rows(out / name / 'trace.jsonl')) for name in ['e3', 'e4'])
    execution_status = ('- Seed mini 用量上限阻止本轮真实复测；仅尝试1次检查请求，无自动重试，无新增效果成绩。'
                        if not live_rows else f'- 新运行已记录{live_rows}题次；分组结果见e3/summary-v3.json和e4/summary-v3.json，失败保留。')
    report = [
        '# E3 / E4 修复与复测准备', '',
        '日期：2026-10-15。仅隔离实验代码和产物；历史成绩与业务代码未改。', '',
        '## 执行状态', '',
        f'- 模型检查：{availability.get("status", "not_checked")}；HTTP {availability.get("http_status")}；{availability.get("provider_error_code", "")}。',
        execution_status,
        '- E3：12道固定公开图片题，两组、3轮，每轮重新请求；按需要视觉细节/描述或业务事实分组，记录完整、读图和生成P50/P95。',
        '- E4：6张旧公开图片、36题，A/B/C0/C1；30道测试、6道开发；旧图仅用于修复回归，不算独立确认。',
        '- 预算上限：检查1次＋E3最多179次＋E4最多270次，总上限450次；无自动重试或额外付费评分模型。',
        '- 56张扩展图/27来源家族哈希通过，保留原家族分组；近似图独立复核、答案和支撑区域标注尚未完成，未运行质量实验。', '',
        '## 修复', '',
        '- 可见颜色/位置/文字缺失不能not_provable，强制read；只有请求核验商品或效果事实才允许此拒答。',
        '- reuse要求合法观察ID且没有声明缺项；每轮最多补读一次。',
        '- 请求明确文字题/是非题的字段类型；所有组使用同一校验，保留原答案，不用正文偷偷改写字段。',
        '- 单独报告字段+来源通过率、格式通过率；正文正确性留给匿名独立审查。错误和失败保留分母。', '',
        '## 历史离线复核（不是新增模型结果）', '',
        '- E3：按需组保留了最慢的视觉请求，减少次数不保证P95下降；单次样本不足以归因供应商波动。',
        f'- E4：20道可回答配对题，B原通过{historical["e4_original_pass_counts"]["B"]}道、C1原通过{historical["e4_original_pass_counts"]["C1"]}道。',
        f'- C1失败中有{historical["e4_format_error_counts"]["C1"]}道字段类型错误，另有{historical["e4_visible_false_abstention_route_count"]}道可见问题被路由拒答；B也有{historical["e4_format_error_counts"]["B"]}道字段类型错误。',
        f'- 新补读保护在旧轨迹上会改写{historical["e4_route_overrides_including_previously_passed"]}次路由，其中有原本已通过题；可能增加读图成本，必须真实复测，不能声称修复后一定更快。',
        '- 原90%/75%保持不变；不能把字段类型错误直接算作视觉理解错误，也不事后更换评分来宣布优化达标。', '',
        '## 后续运行命令', '',
        '恢复模型额度后，在项目根目录运行：', '', '```bash',
        '.venv/bin/python -m evaluation.visual_observation_v3 run --experiment e3',
        '.venv/bin/python -m evaluation.visual_observation_v3 run --experiment e4',
        '.venv/bin/python -m evaluation.report_visual_observation_v3', '```', '',
        'E3后续使用公开图，回答模型为Seed mini；与历史商家图+DeepSeek实验不直接混算。',
        '初始观察来自相同图片/hash/Seed模型的旧收据，C0/C1各自完整计入准备成本，实际新请求与继承成本分开。',
        '运行后报告按原分母展示，独立语义审查和配对显著性验收仍需另做。', '',
        '文件：historical-audit.json、historical-failure-review.json、semantic-review-blinded.json、expansion-review.json、e3/、e4/。',
    ]
    (out / 'report.md').write_text('\n'.join(report) + '\n')
    print(json.dumps({'report': str(out / 'report.md'), 'historical': historical['e4_original_pass_counts'],
                      'new_runs': {name: r['status'] for name, r in runs.items()}}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    main(parser.parse_args().output)
