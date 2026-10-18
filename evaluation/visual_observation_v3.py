"""Isolated E3/E4 follow-up. No production imports, writes, or model fallbacks."""
import argparse
import copy
import json
import random
import re
import time
from pathlib import Path

from evaluation.image_rag_provider import Provider, VISION_MODEL, digest, write
from evaluation.run_visual_observation import (
    ROOT, ANSWER, READER, ROUTER, annotated_cases, image_content, validate_observations,
)

VERSION = 'e3-e4-fix-v3'
OLD = ROOT / 'docs/validation/visual-observation-ab/20261015-seedmini-v2'
DEFAULT = ROOT / 'docs/validation/visual-observation-ab/20261015-e3-e4-fix-v3'
ROUTE_V3 = ROUTER + '''\nnot_provable只允许verification_scope=verified_business_fact。
visible问题中的颜色、位置、图中文字，即使已有观察没记录，也应read，不能not_provable。
reuse必须列出支持本题全部所问内容的观察ID；缺任何一项都read。'''
ANSWER_V3 = ANSWER + '''\n严格遵守answer_contract：value_type=text时value必须为非空字符串，
左右/颜色/数量/图类型/文字题不能填true或false；value_type=boolean时只能填JSON布尔值。
insufficient时value必须为null。answer正文与value必须一致。'''
READER_V3 = READER + '''\n观察的value、state必须一致。颜色、位置、文字读到时用observed；
explicitly_absent只表示检查了指定区域、确认所问对象或文字不存在，不能表示未记录或不确定。'''


def answer_contract(question):
    """Output shape and requested evidence scope, inferred without expected answers."""
    boolean = '是否' in question or question.rstrip('？?').endswith('吗')
    visible_quote = bool(re.search(r'写着|写了|标注|标价|价格标签|文字|图中.*(?:写|标)', question))
    business = bool(re.search(r'真实|实际|今天|当前|已核验', question)) and bool(
        re.search(r'销量|售价|成交价|转化率|ROAS|多少钱|认证|材质|功效|收益', question, re.I))
    return {'value_type': 'boolean' if boolean else 'text',
            'verification_scope': 'verified_business_fact' if business and not visible_quote else 'visible'}


def checked_route(route, contract, observations):
    """Enforce the uncertainty boundary; never infer a visual answer from a gap."""
    if not isinstance(route, dict) or route.get('decision') not in ('reuse', 'read', 'not_provable'):
        raise ValueError('invalid_route')
    ids = route.get('evidence_ids')
    allowed = {o['observation_id'] for o in observations}
    if not isinstance(ids, list) or any(not isinstance(i, str) or i not in allowed for i in ids):
        raise ValueError('unknown_route_evidence')
    result = copy.deepcopy(route)
    result['original_decision'] = route['decision']
    if contract['verification_scope'] == 'verified_business_fact':
        result.update(decision='not_provable', guard='image_cannot_verify_business_fact')
    elif route['decision'] == 'not_provable':
        result.update(decision='read', guard='missing_visible_fact_requires_read')
    elif route['decision'] == 'reuse' and (not ids or route.get('missing')):
        result.update(decision='read', guard='reuse_requires_evidence_and_no_missing_fields')
    return result


def answer_errors(result, contract, asset_id):
    if not isinstance(result, dict):
        return ['answer_not_object']
    errors = []
    if result.get('status') not in ('answered', 'insufficient'):
        errors.append('invalid_status')
    if 'value' not in result:
        errors.append('missing_value')
    if not isinstance(result.get('answer'), str) or not result['answer'].strip():
        errors.append('invalid_answer_text')
    ids = result.get('source_ids')
    valid_ids = isinstance(ids, list) and all(isinstance(i, str) and i == asset_id for i in ids)
    if not valid_ids:
        errors.append('invalid_source_ids')
    if result.get('status') == 'insufficient':
        if result.get('value') is not None:
            errors.append('insufficient_requires_null')
    elif result.get('status') == 'answered':
        value = result.get('value')
        if contract['value_type'] == 'boolean':
            if type(value) is not bool:
                errors.append('expected_boolean_value')
        elif not isinstance(value, str) or not value.strip():
            errors.append('expected_text_value')
        if valid_ids and not ids:
            errors.append('answered_requires_source')
    return errors


def e3_requires_read(question):
    if answer_contract(question)['verification_scope'] == 'verified_business_fact':
        return False
    return bool(re.search(r'哪里|位置|哪边|左|右|完整|写了|写的|箭头|标签|创可贴|署名|几|是否', question))


def frozen(path, value):
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError('frozen_inputs_changed: ' + path.name)
    write(path, value)


def prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    assets = json.loads((ROOT / 'evaluation/visual_observation_v1/assets.json').read_text())['assets']
    by_id = {a['asset_id']: a for a in assets}
    initial = json.loads((OLD / 'initial-observations.json').read_text())
    old_assets = {a['asset_id']: a for a in json.loads((OLD / 'assets.json').read_text())}
    for a in assets:
        if digest(Path(a['path']).read_bytes()) != a['sha256'] or old_assets[a['asset_id']]['sha256'] != a['sha256']:
            raise ValueError('changed_image')
        if initial[a['asset_id']]['receipt']['model'] != VISION_MODEL:
            raise ValueError('initial_model_mismatch')
    cases = annotated_cases()
    for c in cases:
        c['split'] = by_id[c['asset_id']]['split']
        c['answer_contract'] = answer_contract(c['question'])
    public = [{k: v for k, v in c.items() if k != 'gold'} for c in cases]
    # Timing follow-up: fixed public-image cases. No private-image rerun.
    eligible = [c for c in public if c['split'] == 'test']
    e3_cases = []
    for requires_read in (False, True):
        group = [c for c in eligible if e3_requires_read(c['question']) == requires_read]
        random.Random(20261015).shuffle(group)
        e3_cases.extend(group[:6])
    if len(e3_cases) != 12:
        raise ValueError('insufficient_e3_strata')
    for experiment, requests, repeats, arms, budget in [
        ('e3', e3_cases, 3, ['H', 'R'], 179),
        ('e4', public, 1, ['A', 'B', 'C0', 'C1'], 270),
    ]:
        folder = out / experiment
        folder.mkdir(exist_ok=True)
        protocol = {'version': VERSION, 'experiment': experiment, 'model': VISION_MODEL,
                    'seed': 20261015, 'repeats': repeats, 'arms': arms, 'max_calls': budget,
                    'online_concurrency': 1, 'independent_human_review': False,
                    'scope': 'public_external_images_only', 'private_materials_allowed': False,
                    'repeats_are_new_model_calls_not_more_independent_images': True,
                    'initial_cost': 'inherited_same_model_observations_charged_in_each_reuse_arm_total',
                    'answer_repair_calls': 0, 'final_answer_cache': False,
                    'max_supplemental_reads': 1, 'reset_state_between_repeats': True,
                    'interpretation': 'known-image regression; not independent confirmation',
                    'timing_groups': ['requires_visual_detail', 'caption_or_business_fact'],
                    'initial_source': str(OLD / 'initial-observations.json')}
        for name, data in [('protocol.json', protocol), ('requests.json', requests),
                           ('assets.json', assets), ('cases-gold.json', cases),
                           ('initial-observations.json', initial)]:
            frozen(folder / name, data)
        sources = [Path(__file__), ROOT / 'evaluation/image_rag_provider.py',
                   ROOT / 'evaluation/run_visual_observation.py',
                   ROOT / 'evaluation/report_visual_observation_v3.py']
        manifest = {'status': 'prepared_not_run', 'version': VERSION,
                    'models': {'vision': VISION_MODEL, 'router': VISION_MODEL, 'answer': VISION_MODEL},
                    'source_hashes': {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sources},
                    'input_hashes': {name: digest((folder / name).read_bytes()) for name in
                                     ['protocol.json', 'requests.json', 'assets.json',
                                      'cases-gold.json', 'initial-observations.json']},
                    'prompts': {k: digest(v.encode()) for k, v in [('answer', ANSWER_V3),
                                 ('reader', READER_V3), ('router', ROUTE_V3)]},
                    'production_mutated': False, 'gold_sent_to_model': False}
        if not (folder / 'manifest.json').exists():
            write(folder / 'manifest.json', manifest)
    # Expanded corpus is a separate pending-review dataset, never fabricated QA gold.
    expansion = json.loads((ROOT / 'evaluation/visual_observation_bracelets_v1/assets.json').read_text())
    for a in expansion['assets']:
        if digest(Path(a['path']).read_bytes()) != a['sha256']:
            raise ValueError('changed_expansion_image')
    expansion_questions = ['画面中心主要展示的对象是什么？', '画面主要背景是什么颜色？',
                           '画面中是否出现真人的手或手腕？', '这张推广图对应商品的真实销量是多少？']
    frozen(out / 'expansion-review.json', {
        'status': 'needs_independent_near_duplicate_review_and_gold',
        'images': len(expansion['assets']), 'families': len({a['family_id'] for a in expansion['assets']}),
        'family_leakage': bool({a['family_id'] for a in expansion['assets'] if a['split'] == 'test'} &
                               {a['family_id'] for a in expansion['assets'] if a['split'] == 'development'}),
        'annotator_instruction': '看原图标注可见事实；不使用风格/受众标签作事实答案；争议和模糊处标unclear。',
        'assets': [{'asset_id': a['asset_id'], 'family_id': a['family_id'], 'split': a['split'],
                    'path': a['path'], 'sha256': a['sha256'], 'duplicate_review': 'pending',
                    'questions': [{'question': q, 'answer_contract': answer_contract(q),
                                   'expected': None, 'answerable': None, 'support_region': None,
                                   'annotation_status': 'pending'} for q in expansion_questions],
                    'reviewer': None} for a in expansion['assets']]})
    print(json.dumps({'status': 'prepared', 'e3_cases': len(e3_cases), 'e3_repeats': 3,
                      'e4_cases': len(public), 'expanded_images': len(expansion['assets'])}))


def run(out, experiment):
    folder = out / experiment
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for name, sha in manifest['source_hashes'].items():
        if digest((ROOT / name).read_bytes()) != sha:
            raise ValueError('source_changed_use_new_run_directory')
    for name, sha in manifest['input_hashes'].items():
        if digest((folder / name).read_bytes()) != sha:
            raise ValueError('input_changed_use_new_run_directory')
    # Runtime intentionally never reads cases-gold.json or future expansion labels.
    requests = json.loads((folder / 'requests.json').read_text())
    protocol = json.loads((folder / 'protocol.json').read_text())
    assets = {a['asset_id']: a for a in json.loads((folder / 'assets.json').read_text())}
    initial = json.loads((folder / 'initial-observations.json').read_text())
    trace_path = folder / 'trace.jsonl'
    if trace_path.exists():
        raise ValueError('run_already_attempted_do_not_silently_retry')
    manifest.update(status='running')
    write(manifest_path, manifest)
    provider = Provider(folder, protocol['max_calls'])
    try:
        for repeat in range(protocol['repeats']):
            rng = random.Random(protocol['seed'] + repeat)
            state = {aid: copy.deepcopy(v['observations']) for aid, v in initial.items()}
            items = list(requests)
            rng.shuffle(items)
            for c in items:
                aid = c['asset_id']; asset = assets[aid]
                arms = list(protocol['arms']); rng.shuffle(arms)
                for arm in arms:
                    start = time.perf_counter()
                    row = {'case_id': c['case_id'], 'asset_id': aid, 'family_id': asset['family_id'],
                           'split': c['split'], 'question': c['question'], 'answer_contract': c['answer_contract'],
                           'repeat': repeat, 'arm': arm, 'status': 'failed', 'vision_called': False,
                           'timing_group': 'requires_visual_detail' if e3_requires_read(c['question']) else 'caption_or_business_fact',
                           'route_ms': 0, 'vision_ms': 0, 'answer_ms': 0, 'receipt_ids': []}
                    prefix = out.name + ':' + experiment + ':' + str(repeat) + ':' + c['case_id'] + ':' + arm
                    try:
                        evidence = ([{'source_id': aid, 'caption': asset['archived_caption']}]
                                    if arm in ('A', 'B', 'H', 'R') else copy.deepcopy(
                                        initial[aid]['observations'] if arm == 'C0' else state[aid]))
                        row['evidence_before'] = copy.deepcopy(evidence)
                        read = arm in ('B', 'H') or (arm == 'R' and e3_requires_read(c['question']))
                        if arm == 'C1':
                            result, receipt, replay = provider.call(prefix + ':route', VISION_MODEL, [
                                {'role': 'system', 'content': ROUTE_V3}, {'role': 'user', 'content': json.dumps(
                                    {'question': c['question'], 'answer_contract': c['answer_contract'],
                                     'source_id': aid, 'observations': evidence}, ensure_ascii=False)}], 550)
                            row['receipt_ids'].append(receipt['request_id']); row['route_ms'] = receipt['elapsed_ms']
                            if replay: raise RuntimeError('unexpected_replay')
                            row['route'] = checked_route(result, c['answer_contract'], evidence)
                            read = row['route']['decision'] == 'read'
                        if read:
                            row['vision_called'] = True
                            result, receipt, replay = provider.call(prefix + ':read', VISION_MODEL, [
                                {'role': 'system', 'content': READER_V3}, {'role': 'user', 'content': image_content(asset,
                                    json.dumps({'source_id': aid, 'name': asset['name'], 'question': c['question'],
                                                'answer_contract': c['answer_contract']}, ensure_ascii=False))}], 1300)
                            row['receipt_ids'].append(receipt['request_id']); row['vision_ms'] = receipt['elapsed_ms']
                            if replay: raise RuntimeError('unexpected_replay')
                            obs = validate_observations(result, aid, receipt['request_id'], 'supplement')
                            evidence.extend(obs); row['new_observations'] = obs
                        row['evidence_after'] = copy.deepcopy(evidence)
                        row['evidence_ready_ms'] = (time.perf_counter() - start) * 1000
                        result, receipt, replay = provider.call(prefix + ':answer', VISION_MODEL, [
                            {'role': 'system', 'content': ANSWER_V3}, {'role': 'user', 'content': json.dumps(
                                {'question': c['question'], 'answer_contract': c['answer_contract'],
                                 'source_name_map': {aid: asset['name']}, 'allowed_source_ids': [aid],
                                 'evidence': evidence}, ensure_ascii=False)}], 450)
                        row['receipt_ids'].append(receipt['request_id']); row['answer_ms'] = receipt['elapsed_ms']
                        if replay: raise RuntimeError('unexpected_replay')
                        row['answer'] = result
                        row['format_errors'] = answer_errors(result, c['answer_contract'], aid)
                        row['status'] = 'format_failed' if row['format_errors'] else 'succeeded'
                    except Exception as exc:
                        row['error_type'] = type(exc).__name__
                        row['error'] = str(exc)
                        if not isinstance(exc, ValueError):
                            manifest.update(status='stopped_provider_or_uncertain_submission', error_type=type(exc).__name__)
                            raise
                    finally:
                        row['total_ms'] = (time.perf_counter() - start) * 1000
                        with trace_path.open('a') as stream:
                            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                    if arm == 'C1': state[aid].extend(row.get('new_observations', []))
                    print(experiment, repeat, c['case_id'], arm, row['status'], round(row['total_ms'] / 1000, 2), flush=True)
        manifest['status'] = 'completed'
    finally:
        provider.close()
        write(manifest_path, manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--output', type=Path, default=DEFAULT)
    parser.add_argument('--experiment', choices=['e3', 'e4'])
    args = parser.parse_args()
    if args.action == 'prepare': prepare(args.output)
    elif args.experiment: run(args.output, args.experiment)
    else: parser.error('run requires --experiment')
