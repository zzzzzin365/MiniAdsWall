"""Exercise the real RAG preparation -> AdsAgent -> structured delivery endpoint."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from api import main
from api.generation import GenerationInput, generate
from agents.agent_orchestrator import AdsAgent, AgentOrchestrator, AgentType
from mcp.tool_manager import ToolResult


class Client:
    def __init__(self, final):
        self.messages = self
        self.calls = []
        self.outputs = iter([
            {'scores': [{'id': 'marketing', 'support': .95}, {'id': 'irrelevant', 'support': .1}]},
            {'status': 'sufficient', 'missing_information': [], 'evidence_ids': ['marketing']},
            final,
        ])

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(next(self.outputs), ensure_ascii=False))])


class DeliveryAdvice(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.final = {'targetUsers': ['关注口腔护理的消费者'], 'interests': ['口腔护理'],
                      'recommendedObjective': '建议以有效商品点击作为初期推广目标', 'bidSuggestion': .63,
                      'reason': '根据营销漏斗先验证点击，再观察转化',
                      'biddingAdvice': '先确认计费方式，以小预算测试并观察有效点击成本。',
                      'missingInformation': ['预算上限', '计费方式', '转化率'], 'source_ids': ['marketing']}
        self.client = Client(self.final)
        self.manager = SimpleNamespace(_client=self.client, _model='deepseek-v4-1-flash-260910',
            search_with_rewrite=AsyncMock(return_value=ToolResult(tool_name='knowledge_search', success=True,
                data=[{'id': 'marketing', 'title': '营销漏斗', 'content': '先确定推广目标并测量有效点击。', 'source_url': 'https://example.com/marketing'},
                      {'id': 'irrelevant', 'title': '无关内容', 'content': '不应进入生成上下文'}])))
        self.orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        self.orchestrator._pool = {AgentType.ADS: [AdsAgent(self.client, 'deepseek-v4-1-flash-260910')]}
        self.patches = [patch.object(main, '_tool_manager', self.manager),
                        patch.object(main, '_orchestrator', self.orchestrator),
                        patch.dict(os.environ, {'RAG_EVIDENCE_ENABLED': 'true'})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def invoke(self):
        return await generate('strategy', GenerationInput(**self.input()), 'test')

    def input(self):
        return {'adDescription':'推广电动牙刷','industry':'日用品',
            'settings':{'goal':'clicks','dailyBudget':30,'days':7,'maxCpc':.8},
            'delivery':{'status':'launched','billing':'CPC','currentBid':.7,'window':['2026-10-14'],
                'performance':{'impressions':1000,'clicks':100,'orders':0,'spend_minor':4300},
                'material':{'image_count':1,'video_count':0}}}

    async def test_retrieves_filters_and_calls_ads_agent(self):
        result = await self.invoke()
        self.manager.search_with_rewrite.assert_awaited_once()
        args, kwargs = self.manager.search_with_rewrite.call_args
        self.assertEqual(args[0], 'knowledge_search')
        self.assertIn('日用品', args[1])
        self.assertFalse(kwargs['check_retrieved_evidence'])
        model_input = json.loads(self.client.calls[-1]['messages'][0]['content'])
        self.assertIn('先确定推广目标', model_input['knowledge'])
        self.assertNotIn('不应进入生成上下文', model_input['knowledge'])
        self.assertIn('广告投放运营专家', self.client.calls[-1]['system'])
        self.assertEqual(self.client.calls[-1]['model'], 'deepseek-v4-1-flash-260910')
        self.assertTrue(result.knowledgeUsed)
        self.assertEqual([s['id'] for s in result.knowledgeSources], ['marketing'])
        self.assertEqual(result.bidSuggestion,.63)
        self.assertEqual(model_input['brief']['delivery']['performance']['clicks'],100)
        self.assertEqual(result.bidBasis['historical_cpc'],.43)
        self.assertEqual(result.missingInformation,[])
        self.assertIn('100次点击',result.reason)
        self.assertIn('建议¥0.63/点击',result.biddingAdvice)
        self.assertNotIn('需要补齐的数据',result.biddingAdvice)
        self.assertIn('有效商品点击', result.recommendedObjective)
        self.assertNotIn('titles', result.model_dump())

    async def test_rejects_invented_or_duplicate_citations(self):
        for ids in [['invented'], ['marketing', 'marketing']]:
            self.client = Client({**self.final, 'source_ids': ids})
            self.manager._client = self.client
            self.orchestrator._pool[AgentType.ADS][0]._client = self.client
            with self.assertRaises(HTTPException) as caught:
                await self.invoke()
            self.assertEqual(caught.exception.status_code, 502)

    async def test_empty_search_does_not_generate(self):
        self.manager.search_with_rewrite.return_value.data = []
        self.client.outputs = iter([{'status': 'insufficient', 'missing_information': ['营销方法资料'], 'evidence_ids': []}])
        with self.assertRaises(HTTPException):
            await self.invoke()
        self.assertEqual(len(self.client.calls), 1)

    async def test_retrieval_failure_does_not_generate_even_if_gate_disabled(self):
        self.manager.search_with_rewrite.side_effect = RuntimeError('offline')
        with patch.dict(os.environ, {'RAG_EVIDENCE_ENABLED': 'false'}):
            with self.assertRaises(HTTPException) as caught:
                await self.invoke()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.client.calls, [])

    async def test_no_agent_returns_unavailable(self):
        with patch.object(main, '_orchestrator', None):
            with self.assertRaises(HTTPException) as caught:
                await self.invoke()
        self.assertEqual(caught.exception.status_code, 503)
        self.manager.search_with_rewrite.assert_not_awaited()

    async def test_objective_cannot_silently_default(self):
        self.final.pop('recommendedObjective')
        with self.assertRaises(HTTPException) as caught:
            await self.invoke()
        self.assertEqual(caught.exception.status_code, 502)

    async def test_http_strategy_returns_rag_sources(self):
        with patch.dict(os.environ, {'AGENT_SERVICE_TOKEN': 'service-test'}):
            client = TestClient(main.app)  # No lifespan or external infrastructure.
            response = client.post('/generate/strategy',
                json=self.input(),
                headers={'Authorization': 'Bearer service-test', 'X-Request-ID': 'rag-strategy-test'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['knowledgeUsed'])
        self.assertEqual(response.json()['knowledgeSources'][0]['title'], '营销漏斗')
        self.assertEqual(response.headers['x-request-id'], 'rag-strategy-test')


    async def test_new_ad_gets_a_capped_initial_test_bid(self):
        data = self.input()
        data['delivery'] = {'status':'new','billing':'CPC','material':{'image_count':1}}
        self.final['bidSuggestion'] = .4
        result = await generate('strategy', GenerationInput(**data), 'new')
        self.assertEqual(result.bidSuggestion,.4)
        self.assertEqual(result.bidBasis['status'],'new')
        self.assertIsNone(result.bidBasis['historical_cpc'])

    async def test_missing_plan_does_not_call_rag_or_model(self):
        with self.assertRaises(HTTPException) as caught:
            await generate('strategy',GenerationInput(adDescription='商品',industry='日用品'),'missing')
        self.assertEqual(caught.exception.status_code,422)
        self.manager.search_with_rewrite.assert_not_awaited()

    async def test_missing_adjustment_amount_preserves_current_bid(self):
        for amount in [None,0]:
            self.client = Client({**self.final,'bidSuggestion':amount})
            self.manager._client = self.client
            self.orchestrator._pool[AgentType.ADS][0]._client = self.client
            result = await self.invoke()
            self.assertEqual(result.bidSuggestion,.7)
            self.assertEqual(result.bidBasis['amount_source'],'hold_current_bid')

    async def test_amount_is_capped_to_merchant_limit(self):
        self.final['bidSuggestion'] = 1.1
        result = await self.invoke()
        self.assertEqual(result.bidSuggestion,.8)
        self.assertTrue(result.bidBasis['cap_applied'])


class StrategyRetrievalVariants(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_facets_survive_rewrite_timeout(self):
        from mcp.tool_manager import MCPToolManager
        manager = MCPToolManager.__new__(MCPToolManager)
        manager.rewrite_query = AsyncMock(side_effect=RuntimeError('rewrite unavailable'))
        manager.call = AsyncMock(return_value=ToolResult(success=True, data=[], tool_name='knowledge_search'))
        result = await manager.search_with_rewrite('knowledge_search', '行业投放',
            additional_queries=['推广目标', '目标人群', '素材测试'], check_retrieved_evidence=False)
        self.assertTrue(result.success)
        self.assertEqual([call.args[1]['query'] for call in manager.call.await_args_list],
                         ['行业投放', '推广目标', '目标人群', '素材测试'])

    async def test_reranker_receives_passage_content_after_metadata(self):
        from mcp.tool_manager import MCPToolManager
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._model = 'test'
        create = AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text='[1,0]')]))
        manager._client = SimpleNamespace(messages=SimpleNamespace(create=create))
        items = [{'id': 'a' * 250, 'title': '目标', 'content': '应先选择可测目标'},
                 {'id': 'b' * 250, 'title': '测试', 'content': '每次只改变一个变量'}]
        result = await manager._rerank('投放测试', items, 1)
        self.assertEqual(result, [items[1]])
        self.assertIn('每次只改变一个变量', create.call_args.kwargs['messages'][0]['content'])


if __name__ == '__main__':
    unittest.main()
