import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.rag_evidence_gate import EvidenceGate, controlled_abstention
from evaluation.run_rag_evidence_ab import render_context, resolve_config, run_arm, summarize


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = self
        self.inputs = []

    async def create(self, **kwargs):
        self.inputs.append(json.loads(kwargs["messages"][0]["content"]))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(next(self.responses)))])


class EvidenceGateTests(unittest.IsolatedAsyncioTestCase):
    chunks = [{"id": "a", "title": "rule", "content": "useful rule"},
              {"id": "b", "title": "noise", "content": "irrelevant"}]

    async def test_second_judgment_sees_only_filtered_evidence(self):
        client = FakeClient([{"scores": [{"id": "a", "support": .8}, {"id": "b", "support": .1}]},
                             {"status": "sufficient", "missing_information": [], "evidence_ids": ["a"]}])
        result = await EvidenceGate(client, "test").check("question", self.chunks, {})
        self.assertEqual(result.retained_ids, ["a"])
        self.assertEqual([x["id"] for x in client.inputs[1]["retained_chunks"]], ["a"])
        self.assertNotIn("reference", client.inputs[1])
        self.assertNotIn("answerable", client.inputs[1])

    async def test_all_rejected_skips_second_call(self):
        client = FakeClient([{"scores": [{"id": "a", "support": .1}, {"id": "b", "support": .1}]}])
        result = await EvidenceGate(client, "test").check("question", self.chunks, {})
        self.assertEqual(result.status, "insufficient")
        self.assertEqual(len(client.inputs), 1)
        self.assertIn("没有能支持", controlled_abstention(result))

    async def test_business_data_can_answer_without_chunks(self):
        client = FakeClient([{"status": "sufficient", "missing_information": [], "evidence_ids": ["business"]}])
        result = await EvidenceGate(client, "test").check("what value", [], {"value": 2})
        self.assertEqual(result.status, "sufficient")
        with self.assertRaises(ValueError):
            controlled_abstention(result)

    async def test_invalid_scores_fail_instead_of_becoming_refusals(self):
        for scores in [[], [{"id": "a", "support": .8}, {"id": "a", "support": .9}],
                       [{"id": "a", "support": float("nan")}, {"id": "b", "support": .9}],
                       [{"id": "a", "support": True}, {"id": "b", "support": .9}],
                       [{"id": "x", "support": .8}, {"id": "b", "support": .9}]]:
            with self.subTest(scores=scores), self.assertRaises(ValueError):
                await EvidenceGate(FakeClient([{"scores": scores}]), "test").check("q", self.chunks, {})

    async def test_cannot_cite_filtered_or_unknown_evidence(self):
        client = FakeClient([{"scores": [{"id": "a", "support": .8}, {"id": "b", "support": .1}]},
                             {"status": "sufficient", "missing_information": [], "evidence_ids": ["b"]}])
        with self.assertRaises(ValueError):
            await EvidenceGate(client, "test").check("q", self.chunks, {})

    async def test_rejection_skips_generator(self):
        class RunnerClient:
            calls = []
            async def close(self): pass
        class Gate:
            def __init__(self, *args): pass
            async def check(self, *args):
                from evaluation.rag_evidence_gate import EvidenceDecision
                return EvidenceDecision("insufficient", [], [], ["曝光次数"], [])
        with patch("evaluation.run_rag_evidence_ab.RecordedClient", return_value=RunnerClient()), \
             patch("evaluation.run_rag_evidence_ab.EvidenceGate", Gate), \
             patch("evaluation.run_rag_evidence_ab.AdsAgent") as generator:
            result = await run_arm({"id": "x", "question": "CTR?", "chunks": [], "business_data": {}},
                                   True, {"key": "dummy", "base": None, "model": "test"}, None, None, .7)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["generation_skipped"])
        generator.assert_not_called()

    async def test_experimental_baseline_prompt_remains_frozen(self):
        chunks = [{"id": "x", "title": "CTR", "content": "规则" * 400, "score": .8}]
        context = render_context(chunks, {})
        self.assertIn("如果知识库内容不足，再结合通用客服能力说明", context)
        self.assertIn("规则" * 300, context)
        self.assertNotIn("规则" * 301, context)


class ExperimentMetricsTests(unittest.TestCase):
    def test_failed_pair_is_not_counted_as_correct_abstention(self):
        result = summarize([{"answerable": False,
                             "baseline": {"status": "failed", "calls": []},
                             "gated": {"status": "failed", "calls": []},
                             "grading": {"status": "not_graded"}}])
        self.assertEqual(result["graded_pairs"], 0)
        self.assertIsNone(result["arms"]["gated"]["factual_error_rate"])
        self.assertEqual(result["arms"]["gated"]["pipeline_failures"], 1)

    def test_openrouter_key_ignores_inherited_anthropic_base(self):
        with patch("evaluation.run_rag_evidence_ab.dotenv_values", return_value={"OPENROUTER_API_KEY": "dummy", "OPENROUTER_MODEL": "test:free"}), \
             patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "https://unrelated.example"}, clear=True):
            config = resolve_config()
        self.assertEqual(config["base"], "https://openrouter.ai/api")
        self.assertEqual(config["provider"], "openrouter")


if __name__ == "__main__":
    unittest.main()
