import json
from pathlib import Path
import unittest
from marketing.contracts import valid_contract
from marketing.config import load_marketing, FLAGS

class MarketingContractsTest(unittest.TestCase):
    def test_shared_fixtures(self):
        examples = json.loads((Path(__file__).resolve().parents[1] / 'contracts/marketing/v1/examples.json').read_text())
        for case in examples:
            with self.subTest(case=case['label']):
                self.assertEqual(valid_contract(case['name'], case['value']), case['valid'])

    def test_rollout_defaults_and_unsupported_dependencies(self):
        self.assertFalse(any(load_marketing({})['features'].values()))
        self.assertEqual(load_marketing({FLAGS[0]: 'true'})['environment'], 'synthetic')
        for env in ({FLAGS[0]: 'maybe'}, {FLAGS[1]: 'true'}, {FLAGS[0]: 'true', FLAGS[1]: 'true'}, {'MARKETING_PROTOCOL_VERSION': '2'}, {'MARKETING_ENVIRONMENT': 'wrong'}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                load_marketing(env)
