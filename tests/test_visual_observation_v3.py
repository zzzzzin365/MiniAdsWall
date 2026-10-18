"""Boundary regressions found in actual E4 traces; no paid/model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation.visual_observation_v3 import answer_contract, answer_errors, checked_route, frozen, e3_requires_read


class VisualObservationV3Tests(unittest.TestCase):
    def setUp(self):
        self.obs = [{'observation_id': 'allowed', 'state': 'observed', 'value': '流程图'}]

    def test_missing_background_must_read_not_abstain(self):
        contract = answer_contract('整张流程图的背景主要是什么颜色？')
        route = {'decision': 'not_provable', 'evidence_ids': [], 'missing': ['背景颜色']}
        self.assertEqual(checked_route(route, contract, self.obs)['decision'], 'read')
        self.assertEqual(route['decision'], 'not_provable')

    def test_missing_visible_words_must_read(self):
        route = {'decision': 'not_provable', 'evidence_ids': [], 'missing': ['完整说明']}
        contract = answer_contract('右侧视频组件的箭头指向的素材说明完整写了什么？')
        self.assertEqual(checked_route(route, contract, self.obs)['decision'], 'read')

    def test_actual_business_facts_remain_unverifiable(self):
        for question in ['图中这款汽车今天的实际成交价是多少人民币？',
                         '多邻国采用这些方法后的真实年度销量是多少？',
                         '搭建作者这套系统实际花了多少钱？', '这张案例封面对应活动的真实ROAS是多少？']:
            route = {'decision': 'read', 'evidence_ids': []}
            self.assertEqual(checked_route(route, answer_contract(question), self.obs)['decision'], 'not_provable')

    def test_visible_price_text_is_not_actual_price_verification(self):
        self.assertEqual(answer_contract('图中写着的实际售价是多少？')['verification_scope'], 'visible')

    def test_reuse_requires_valid_evidence_and_no_missing_items(self):
        contract = answer_contract('背景是什么颜色？')
        for route in [{'decision': 'reuse', 'evidence_ids': []},
                      {'decision': 'reuse', 'evidence_ids': ['allowed'], 'missing': ['颜色']}]:
            self.assertEqual(checked_route(route, contract, self.obs)['decision'], 'read')
        self.assertEqual(checked_route({'decision': 'reuse', 'evidence_ids': ['allowed'], 'missing': []}, contract, self.obs)['decision'], 'reuse')
        with self.assertRaises(ValueError):
            checked_route({'decision': 'reuse', 'evidence_ids': ['other']}, contract, self.obs)

    def test_correct_prose_does_not_hide_wrong_value_type(self):
        contract = answer_contract('YOU / REVIEW区域位于整体画面的左侧还是右侧？')
        answer = {'status': 'answered', 'value': False, 'answer': 'YOU / REVIEW位于右侧', 'source_ids': ['image']}
        self.assertIn('expected_text_value', answer_errors(answer, contract, 'image'))
        self.assertIs(answer['value'], False)
        answer['value'] = '右侧'
        self.assertEqual(answer_errors(answer, contract, 'image'), [])

    def test_boolean_false_is_valid_but_string_false_is_not(self):
        contract = answer_contract('左边那只猫头鹰是否贴着创可贴？')
        answer = {'status': 'answered', 'value': False, 'answer': '没有', 'source_ids': ['image']}
        self.assertEqual(answer_errors(answer, contract, 'image'), [])
        answer['value'] = 'false'
        self.assertIn('expected_boolean_value', answer_errors(answer, contract, 'image'))

    def test_insufficient_requires_null_and_sources_are_scoped(self):
        c = answer_contract('背景是什么颜色？')
        self.assertIn('insufficient_requires_null', answer_errors(
            {'status': 'insufficient', 'value': '黑色', 'answer': '无法确定', 'source_ids': []}, c, 'image'))
        self.assertIn('invalid_source_ids', answer_errors(
            {'status': 'answered', 'value': '黑色', 'answer': '黑色', 'source_ids': ['other']}, c, 'image'))

    def test_invalid_objects_are_rejected(self):
        self.assertEqual(answer_errors(None, answer_contract('什么颜色？'), 'image'), ['answer_not_object'])
        with self.assertRaises(ValueError):
            checked_route({'decision': 'reuse', 'evidence_ids': [{}]}, answer_contract('什么颜色？'), self.obs)

    def test_inputs_are_frozen_instead_of_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requests.json'
            frozen(path, {'question': '固定问题'})
            before = path.read_bytes()
            with self.assertRaises(ValueError): frozen(path, {'question': '更换问题'})
            self.assertEqual(path.read_bytes(), before)

    def test_timing_rule_is_question_only(self):
        self.assertTrue(e3_requires_read('YOU / REVIEW位于左侧还是右侧？'))
        self.assertFalse(e3_requires_read('真实销量是多少？'))
        self.assertFalse(e3_requires_read('广告的品牌叫什么？'))

    def test_runner_counts_format_failure_and_resets_observations_between_repeats(self):
        from evaluation import visual_observation_v3 as runner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner.prepare(root)
            folder = root / 'e4'
            requests = json.loads((folder / 'requests.json').read_text())
            case = next(c for c in requests if c['case_id'] == 'x-marketing-kb-1')
            (folder / 'requests.json').write_text(json.dumps([case]))
            protocol = json.loads((folder / 'protocol.json').read_text())
            protocol.update(arms=['C1'], repeats=2)
            (folder / 'protocol.json').write_text(json.dumps(protocol))
            manifest = json.loads((folder / 'manifest.json').read_text())
            for name in ['requests.json', 'protocol.json']:
                manifest['input_hashes'][name] = runner.digest((folder / name).read_bytes())
            (folder / 'manifest.json').write_text(json.dumps(manifest))
            route_evidence_sizes = []
            actual_calls = []

            class ControlledProvider:
                def __init__(self, *args): pass
                def close(self): pass
                def call(self, key, model, messages, max_tokens):
                    actual_calls.append(key)
                    content = messages[1]['content']
                    if isinstance(content, str):
                        data = json.loads(content)
                        self.assert_no_gold(data)
                    receipt = {'request_id': key, 'elapsed_ms': 1}
                    if key.endswith(':route'):
                        route_evidence_sizes.append(len(data['observations']))
                        result = {'decision': 'not_provable', 'evidence_ids': [], 'missing': ['背景']}
                    elif key.endswith(':read'):
                        result = {'observations': [{'key': 'background', 'value': '黑色',
                                                   'region': '全图', 'state': 'observed'}]}
                    else:
                        result = {'status': 'answered', 'value': True, 'answer': '黑色',
                                  'source_ids': ['x-marketing-kb']}
                    return result, receipt, False
                @staticmethod
                def assert_no_gold(data):
                    if isinstance(data, dict):
                        assert not {'gold', 'expected', 'support_region', 'answerable'} & set(data)
                        for v in data.values(): ControlledProvider.assert_no_gold(v)
                    elif isinstance(data, list):
                        for v in data: ControlledProvider.assert_no_gold(v)

            with patch.object(runner, 'Provider', ControlledProvider):
                runner.run(root, 'e4')
            rows = [json.loads(s) for s in (folder / 'trace.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(actual_calls), 6)
            self.assertEqual(route_evidence_sizes[0], route_evidence_sizes[1])
            self.assertTrue(all(r['vision_called'] and r['status'] == 'format_failed' for r in rows))
            self.assertTrue(all(r['answer']['value'] is True for r in rows))
            from evaluation.report_visual_observation_v3 import report_run
            report = report_run(folder)
            self.assertEqual(report['coverage']['failures_retained'], 2)
            self.assertEqual(report['groups']['test:C1']['format_pass_rate'], 0)
            self.assertEqual(report['groups']['test:C1']['field_source_pass_rate'], 0)
            self.assertEqual(report['groups']['test:C1']['body_correctness'], 'pending_independent_review')
            with self.assertRaises(ValueError): runner.run(root, 'e4')


if __name__ == '__main__':
    unittest.main()
