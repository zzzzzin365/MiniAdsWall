import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from core.rag_evidence import EvidenceConfig, check_evidence
from evaluation.clef_client import ClefScorer, PassageScoringClient, resolve_clef_config
from evaluation.run_rag_evidence_ab import Pacer, run_arm, usage_summary


CONFIG = {"endpoint": "https://api.cloudflare.com/test", "key": "never-log-this",
          "model": "clef", "deployment": "cloudflare", "input_rate_usd_per_million": .24}
CHUNKS = [{"id": "strange/id:a", "content": "合同单价0.2元", "title": "合同"},
          {"id": "b", "content": "加价提高排序分", "title": "排序"}]


def envelope(answers):
    return {"success": True, "result": {"model": "clef", "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 0}}}


class Inner:
    def __init__(self, sufficiency):
        self.messages, self.calls, self.inputs = self, [], []
        self.sufficiency = sufficiency
        self.closed = False

    async def create(self, **kwargs):
        self.inputs.append(json.loads(kwargs["messages"][0]["content"]))
        self.calls.append({"success": True, "duration_ms": 1, "model": "llm"})
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.sufficiency))])

    async def close(self):
        self.closed = True


class ClefEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_clef_only_replaces_scoring_and_does_not_leak_gold_labels(self):
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=envelope({"passage_0": {"type": "noul", "noul": .9},
                                                    "passage_1": {"type": "noul", "noul": .1}}))
        inner = Inner({"status": "sufficient", "missing_information": [], "evidence_ids": [CHUNKS[0]["id"]]})
        scorer = ClefScorer(CONFIG, Pacer(0), httpx.MockTransport(respond))
        client = PassageScoringClient(inner, scorer)
        result = await check_evidence(client, "llm", "结算单价？", CHUNKS)
        self.assertEqual(result.status, "sufficient")
        self.assertEqual(result.retained_ids, [CHUNKS[0]["id"]])
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(inner.inputs), 1)
        self.assertEqual([c["id"] for c in inner.inputs[0]["retained_chunks"]], [CHUNKS[0]["id"]])
        state = json.loads(requests[0]["state"])
        self.assertEqual(set(state), {"question", "chunks", "business_data"})
        self.assertEqual(set(requests[0]["questions"]), {"passage_0", "passage_1"})
        self.assertAlmostEqual(client.calls[0]["estimated_cost_usd"], .000024)
        self.assertIsNone(client.calls[0]["usage"]["cost"])
        self.assertNotIn(CONFIG["key"], json.dumps(result.to_dict()) + json.dumps(client.calls))
        await client.close()
        self.assertTrue(inner.closed)

    async def test_invalid_or_missing_decisions_are_failures_not_refusals(self):
        for answer in [True, float("nan"), -1, 2, "0.9", None]:
            def respond(request):
                return httpx.Response(200, json=envelope({"passage_0": {"type": "noul", "noul": answer}}))
            inner = Inner({})
            scorer = ClefScorer(CONFIG, Pacer(0), httpx.MockTransport(respond))
            client = PassageScoringClient(inner, scorer)
            result = await check_evidence(client, "llm", "q", CHUNKS[:1])
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(result.error_code, "invalid_response")
            self.assertEqual(inner.inputs, [])
            self.assertFalse(client.calls[0]["success"])
            await client.close()

    async def test_missing_extra_or_wrong_type_answers_rejected(self):
        for answers in [{}, {"unexpected": {"type": "noul", "noul": .9}},
                        {"passage_0": {"type": "score", "noul": .9}}]:
            scorer = ClefScorer(CONFIG, Pacer(0), httpx.MockTransport(
                lambda request: httpx.Response(200, json=envelope(answers))))
            with self.assertRaises(ValueError):
                await scorer.score({"question": "q", "chunks": CHUNKS[:1], "business_data": {}})
            await scorer.close()

    async def test_http_failure_and_timeout_stop_generation(self):
        for timeout in [False, True]:
            async def respond(request):
                if timeout:
                    await asyncio.sleep(.1)
                return httpx.Response(401, json={"secret": CONFIG["key"]})
            inner = Inner({})
            client = PassageScoringClient(inner, ClefScorer(CONFIG, Pacer(0), httpx.MockTransport(respond)))
            result = await check_evidence(client, "llm", "q", CHUNKS, config=EvidenceConfig(True, .7, .01))
            self.assertEqual(result.error_code, "timeout" if timeout else "provider_error")
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(inner.inputs, [])
            self.assertEqual(len(client.calls), 1)
            self.assertNotIn(CONFIG["key"], json.dumps(client.calls))
            await client.close()

    async def test_business_only_uses_llm_without_empty_clef_call(self):
        inner = Inner({"status": "sufficient", "missing_information": [], "evidence_ids": ["business"]})
        scorer = ClefScorer(CONFIG, Pacer(0), httpx.MockTransport(lambda r: self.fail("Unexpected Clef call")))
        client = PassageScoringClient(inner, scorer)
        result = await check_evidence(client, "llm", "数量？", [], {"count": 2})
        self.assertEqual(result.status, "sufficient")
        self.assertEqual(scorer.calls, [])
        await client.close()

    async def test_runner_blocks_and_records_clef_calls(self):
        inner = Inner({"status": "insufficient", "missing_information": ["合同单价"], "evidence_ids": []})
        scorer = ClefScorer(CONFIG, Pacer(0), httpx.MockTransport(lambda r: httpx.Response(200,
            json=envelope({"passage_0": {"type": "noul", "noul": .1}, "passage_1": {"type": "noul", "noul": .1}}))))
        case = {"id": "fixture", "question": "单价？", "chunks": CHUNKS, "business_data": {},
                "reference": "DO NOT SEND", "answerable": False, "gold_chunk_ids": []}
        with patch("evaluation.run_rag_evidence_ab.RecordedClient", return_value=inner), \
             patch("evaluation.clef_client.ClefScorer", return_value=scorer), \
             patch("evaluation.run_rag_evidence_ab.AdsAgent") as generator:
            result = await run_arm(case, True, {"key": "x", "base": None, "model": "llm"},
                                   Pacer(0), None, .7, "runtime-v2", CONFIG)
        generator.assert_not_called()
        self.assertTrue(result["generation_skipped"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["calls"]), 2)
        self.assertEqual(result["calls"][0]["provider"], "cloudflare")
        self.assertNotIn("DO NOT SEND", json.dumps(inner.inputs))
        self.assertGreaterEqual(result["service_ms"], 1)


class ConfigurationTests(unittest.TestCase):
    def test_cloudflare_and_local_config(self):
        with patch("evaluation.clef_client.dotenv_values", return_value={}), \
             patch.dict("os.environ", {"CLOUDFLARE_ACCOUNT_ID": "a" * 32, "CLOUDFLARE_AUTH_TOKEN": "secret"}, clear=True):
            config = resolve_clef_config(Path("."), "clef-flash")
            self.assertTrue(config["endpoint"].endswith("/clef-flash"))
            self.assertEqual(config["input_rate_usd_per_million"], .038)
        with patch("evaluation.clef_client.dotenv_values", return_value={}), \
             patch.dict("os.environ", {"CLEF_BASE_URL": "http://127.0.0.1:8020"}, clear=True):
            self.assertEqual(resolve_clef_config(Path("."))["endpoint"], "http://127.0.0.1:8020/v1/systemone")
        with patch("evaluation.clef_client.dotenv_values", return_value={}), patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                resolve_clef_config(Path("."))

    def test_usage_does_not_label_estimates_as_provider_cost(self):
        summary = usage_summary([{"success": True, "provider": "cloudflare", "stage": "passage_scoring",
            "estimated_cost_usd": .01, "usage": {"prompt_tokens": 100, "completion_tokens": 0, "cost": None}}])
        self.assertIsNone(summary["provider_reported_cost_usd"])
        self.assertEqual(summary["clef_estimated_cost_usd"], .01)


if __name__ == "__main__":
    unittest.main()
