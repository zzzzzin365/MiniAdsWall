import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import chromadb
from chromadb.config import Settings
from chromadb.api.types import EmbeddingFunction
from core.rag.index import TextIndex, split_document, tokenize, fuse
from core.rag.service import RetrievalService
from core.rag.citations import validate_and_render
from core.rag_evidence import prepare_chunks
from mcp.tool_manager import MCPToolManager, Tool


class SmallEmbedding(EmbeddingFunction):
    def __call__(self, input):
        return [[float('CPA' in text), float('CTR' in text), 1.] for text in input]


class IndexTests(unittest.TestCase):
    def test_long_sentences_offsets_and_revision_identity(self):
        doc, chunks = split_document({'title':'长句', 'content':'甲'*3600})
        self.assertTrue(all(0 < len(c['content']) <= 400 for c in chunks))
        self.assertTrue(all(doc['content'][c['start_offset']:c['end_offset']] == c['content'] for c in chunks))
        self.assertLess(chunks[1]['start_offset'], chunks[0]['end_offset'])
        self.assertEqual(chunks, split_document(doc)[1])
        changed = split_document({**doc, 'content':doc['content']+'乙'})[1]
        self.assertNotEqual(chunks[0]['id'], changed[0]['id'])
        self.assertEqual(chunks[0]['doc_id'], changed[0]['doc_id'])

    def test_atomic_publish_rollback_and_denial_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = TextIndex(tmp+'/index.sqlite')
            g1, _, c1 = index.stage([{'doc_id':'a','title':'术语','content':'CTR 点击率'}], 'test')
            self.assertIsNone(index.active())
            with self.assertRaises(ValueError): index.publish(g1, ['wrong'])
            self.assertIsNone(index.active())
            index.publish(g1, [c['id'] for c in c1])
            self.assertEqual(index.keyword(g1, 'CTR', 3)[0]['doc_id'], 'a')
            g2, _, c2 = index.stage([{'doc_id':'a','title':'术语','content':'CPA 成本'}], 'test')
            self.assertEqual(index.active(), g1)
            index.publish(g2, [c['id'] for c in c2])
            index.deny('a')
            index.activate(g1)
            self.assertEqual(index.chunks(g1), [])
            self.assertEqual(index.keyword(g2, 'CPA', 2), [])

    def test_tokenizer_and_fusion(self):
        self.assertIn('ad-123', tokenize('广告 AD-123 CPA 3.5'))
        self.assertIn('广告', tokenize('广告点击率'))
        rows = fuse([[{'id':'a'},{'id':'a'},{'id':'b'}],[{'id':'b'}]])
        self.assertEqual(rows[0]['id'], 'b')
        self.assertAlmostEqual(rows[1]['fusion_score'], 1/61)


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = chromadb.PersistentClient(path=self.tmp.name+'/vectors', settings=Settings(anonymized_telemetry=False))
        self.kb = SimpleNamespace(_client=self.client, _embedding_function=SmallEmbedding(), optimized_rag=True)
        self.service = RetrievalService(self.kb, self.tmp.name+'/text.sqlite')
        self.kb.retrieval_service = self.service
        self.docs = [{'doc_id':'a','title':'CPA 规则','content':'CPA 获得订单的成本。'*100, 'source_url':'https://example.com/cpa'},
                     {'doc_id':'b','title':'CTR 规则','content':'CTR 点击数除以曝光数。'}]
        self.receipt = self.service.build(self.docs)
        self.manager = MCPToolManager.__new__(MCPToolManager)
        self.manager._tools = {}
        self.manager.rewrite_query = AsyncMock(return_value=[])
        self.manager._rerank = AsyncMock(side_effect=lambda q, rows, k: rows[:k])
        self.manager.register(Tool('knowledge_search','test', self.handler, {}))
        # Real bound owner is required for manager dispatch.
        self.owner = SimpleNamespace(optimized_rag=True,retrieval_service=self.service)
        self.manager._tools['knowledge_search'].handler = SimpleNamespace(__self__=self.owner)
        self.env = patch.dict(os.environ, {'RAG_V2_REWRITE_ENABLED':'false','RAG_V2_HYBRID_ENABLED':'true',
            'RAG_V2_PARENT_ENABLED':'false','RAG_V2_RERANKER':'none'})
        self.env.start()

    async def handler(self, params, context):
        raise AssertionError('optimized manager must dispatch through shared service')

    async def asyncTearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    async def test_real_vectors_bm25_manager_and_version(self):
        result = await self.manager.search_with_rewrite('knowledge_search','CPA',top_k=3,check_retrieved_evidence=False)
        self.assertTrue(result.success)
        self.assertEqual(result.retrieval['generation_id'], self.receipt['generation_id'])
        self.assertEqual(result.retrieval['retrieval_mode'], 'vector+keyword')
        self.assertEqual(result.data[0]['doc_id'], 'a')
        self.assertTrue(all('parent_text' not in c for c in result.data))
        self.assertEqual(self.service.revalidate(result.data), result.data)
        self.service.index.deny('a')
        result = await self.manager.search_with_rewrite('knowledge_search','CPA',top_k=3,check_retrieved_evidence=False)
        self.assertTrue(all(c['doc_id'] != 'a' for c in result.data))

    async def test_failure_partial_empty_and_unavailable(self):
        with patch.object(self.service, 'vector', side_effect=RuntimeError('offline')):
            result = await self.service.search(self.manager, 'CPA', 2)
            self.assertTrue(result.success)
            self.assertTrue(result.retrieval['partial'])
            self.assertTrue(result.data)
            with patch.object(self.service.index, 'keyword', side_effect=RuntimeError('offline')):
                result = await self.service.search(self.manager, 'CPA', 2)
                self.assertFalse(result.success)
                self.assertEqual(result.error, 'retrieval_unavailable')

    async def test_parent_exact_range_and_revocation_at_output(self):
        with patch.dict(os.environ, {'RAG_V2_PARENT_ENABLED':'true'}):
            result = await self.service.search(self.manager, 'CPA', 3)
        self.assertGreater(len(result.data[0]['content']), 600)
        prepared = prepare_chunks(result.data, 3)
        self.assertEqual(prepared[0]['content'], result.data[0]['content'])
        self.assertEqual(self.service.revalidate(prepared), prepared)
        item = prepared[0]
        raw = json.dumps({'status':'answered','answer_blocks':[{'text':'订单成本','evidence_ids':[item['id']],
            'quote':'CPA'}],'missing_information':[]})
        self.assertIn('参考依据', validate_and_render(raw, prepared, revalidate=self.service.revalidate))
        self.service.index.deny(item['doc_id'])
        with self.assertRaisesRegex(ValueError, 'revoked'):
            validate_and_render(raw, prepared, revalidate=self.service.revalidate)

    async def test_dedicated_reranker_selection_and_failure_flag(self):
        self.service.local_reranker = SimpleNamespace(revision='local-test', rank=lambda q, rows, k: rows[-k:])
        with patch.dict(os.environ, {'RAG_V2_RERANKER':'dedicated'}):
            result = await self.service.search(self.manager,'CPA',1)
            self.assertTrue(result.retrieval['rerank_applied'])
            self.assertEqual(result.retrieval['reranker_revision'],'local-test')
            self.service.local_reranker.rank = lambda *args: (_ for _ in ()).throw(ValueError('bad model'))
            result = await self.service.search(self.manager,'CPA',1)
            self.assertFalse(result.retrieval['rerank_applied'])
            self.assertIn('rerank',result.retrieval['failed_branches'])

    async def test_cancellation_propagates_and_generation_is_pinned(self):
        original = self.service.vector
        async def slow(*args):
            await asyncio.sleep(1)
        self.manager.rewrite_query = slow
        with patch.dict(os.environ, {'RAG_V2_REWRITE_ENABLED':'true'}):
            task = asyncio.create_task(self.service.search(self.manager,'CPA',2))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        seen = []
        def vector(generation, query, k):
            seen.append(generation)
            return original(generation,query,k)
        with patch.object(self.service, 'vector', side_effect=vector):
            result = await self.service.search(self.manager,'CPA',2)
        self.assertEqual(seen,[result.retrieval['generation_id']])

    async def test_no_cross_generation_query_after_publication(self):
        old = self.receipt['generation_id']
        self.service.build([{'doc_id':'new','title':'新规则','content':'全新 CPA'}])
        self.assertNotEqual(old, self.service.index.active())
        self.assertEqual({c['doc_id'] for c in self.service.index.chunks(old)}, {'a','b'})
        result = await self.service.search(self.manager,'CPA', 2)
        self.assertEqual({c['doc_id'] for c in result.data}, {'new'})


class OutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_rerank_invalid_permutation_raises_instead_of_reporting_success(self):
        manager = MCPToolManager.__new__(MCPToolManager)
        manager._model = 'test'
        for answer in ['[0,0]', '[true,1]', '[9,0]', '[0]', '{}']:
            manager._client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(
                return_value=SimpleNamespace(content=[SimpleNamespace(text=answer)]))))
            with self.assertRaises((ValueError,TypeError)):
                await manager._rerank('q',[{'content':'a'},{'content':'b'}],1)

    async def test_quote_unknown_business_and_structure(self):
        source = [{'id':'x','title':'规则','content':'订单成本'}]
        base = {'status':'answered','answer_blocks':[{'text':'答案','evidence_ids':['x'],'quote':'订单'}], 'missing_information':[]}
        self.assertIn('答案', validate_and_render(json.dumps(base),source))
        for block in [{'text':'a','evidence_ids':['bad']}, {'text':'a','evidence_ids':['x'],'quote':'编造'},
                      {'text':'a','evidence_ids':['business'],'tool_record_ids':['invented']}]:
            with self.assertRaises(ValueError):
                validate_and_render(json.dumps({**base,'answer_blocks':[block]}),source,business_records=['ads_summary'])


class EvaluationTests(unittest.TestCase):
    def test_span_coverage_merges_overlap_without_double_counting(self):
        from evaluation.run_rag_optimization import coverage
        gold = [{'doc_id':'a','revision':'r','start':0,'end':20}]
        rows = [{'doc_id':'a','revision':'r','start_offset':0,'end_offset':15},
                {'doc_id':'a','revision':'r','start_offset':10,'end_offset':20}]
        self.assertEqual(coverage(gold,rows),1)
        rows[1]['revision']='old'
        self.assertEqual(coverage(gold,rows),.75)


class CitationHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.test_rag_evidence_runtime import ChatIntegrationTests
        ChatIntegrationTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TextIndex(self.tmp.name+'/text.sqlite')
        generation, _, chunks = self.store.stage([{'doc_id':'a','title':'计费规则','content':'每次计费单价固定为 0.2 元'}], 'test')
        self.store.publish(generation,[c['id'] for c in chunks])
        self.chunks = [{**c,'generation_id':generation} for c in chunks]
        self.service = RetrievalService.__new__(RetrievalService)
        self.service.index = self.store
        self.service.search = AsyncMock(return_value=__import__('mcp.tool_manager',fromlist=['ToolResult']).ToolResult(
            True,self.chunks,'knowledge_search'))
        self.owner = SimpleNamespace(optimized_rag=True,retrieval_service=self.service)
        self.manager._tools = {'knowledge_search':SimpleNamespace(handler=SimpleNamespace(__self__=self.owner))}
        self.mode = patch.dict(os.environ, {'RAG_V2_CITATIONS_ENABLED':'true','AGENT_SYNC_DURABLE_ENABLED':'false'})
        self.mode.start()
        from tests.test_rag_evidence_runtime import Client
        cid = chunks[0]['id']
        self.manager._client = Client([{'scores':[{'id':cid,'support':.9}]},
            {'status':'sufficient','missing_information':[],'evidence_ids':[cid]}])

    def tearDown(self):
        self.mode.stop()
        from tests.test_rag_evidence_runtime import ChatIntegrationTests
        ChatIntegrationTests.tearDown(self)
        self.tmp.cleanup()

    async def chat(self):
        from tests.test_rag_evidence_runtime import ChatIntegrationTests
        return await ChatIntegrationTests.chat(self)

    async def test_actual_http_validates_json_before_memory_write(self):
        self.orchestrator.run.return_value.response = json.dumps({'status':'answered',
            'answer_blocks':[{'text':'单价为 0.2 元','evidence_ids':[self.chunks[0]['id']],'quote':'0.2 元'}],
            'missing_information':[]})
        response = await self.chat()
        self.assertEqual(response.status_code,200)
        self.assertIn('参考依据',response.json()['response'])
        self.assertIn('最终输出必须是一个 JSON',self.orchestrator.run.call_args.args[0].context)

    async def test_actual_http_rejects_unknown_source_and_revocation(self):
        self.orchestrator.run.return_value.response = json.dumps({'status':'answered',
            'answer_blocks':[{'text':'伪造事实','evidence_ids':['invented']}],'missing_information':[]})
        response = await self.chat()
        self.assertNotIn('伪造事实',response.json()['response'])
        self.assertEqual(response.json()['decision_reason'],'rag_citation_validation_failed')

    async def test_actual_http_revocation_after_model_cannot_leak_raw_answer(self):
        async def generate(req):
            self.store.deny('a')
            return SimpleNamespace(response='不能泄漏的原始答案',action_name=None,
                decision=__import__('core.action_decision',fromlist=['DecisionType']).DecisionType.EXECUTE,
                decision_reason='execute',confirmation_id=None,risk_level=self.orchestrator.run.return_value.risk_level,
                confirmed=False,intent=self.orchestrator.run.return_value.intent,
                agent_type=self.orchestrator.run.return_value.agent_type,missing_fields=[],escalated=False)
        self.orchestrator.run.side_effect=generate
        response=await self.chat()
        self.assertEqual(response.status_code,200)
        self.assertNotIn('不能泄漏',response.json()['response'])


from tests import test_rag_evidence_hosting as hosted_fixture


class HostedCitationTests(unittest.IsolatedAsyncioTestCase):
    new_run = hosted_fixture.HostedEvidenceTests.new_run
    execute = hosted_fixture.HostedEvidenceTests.execute
    asyncTearDown = hosted_fixture.HostedEvidenceTests.asyncTearDown

    async def asyncSetUp(self):
        await hosted_fixture.HostedEvidenceTests.asyncSetUp(self)
        self.env.stop()
        self.env = patch.dict(os.environ, {'ANTHROPIC_API_KEY':'test','HOSTING_RAG_ENABLED':'false',
            'RAG_EVIDENCE_ENABLED':'true','RAG_V2_CITATIONS_ENABLED':'true'})
        self.env.start()
        self.pipeline.rag_manager.call.return_value.data = [{**c,'generation_id':'fixture'}
            for c in self.pipeline.rag_manager.call.return_value.data]
        self.pipeline.kb.retrieval_service = SimpleNamespace(revalidate=lambda chunks: chunks)

    async def test_hosted_stream_is_validated_before_delivery(self):
        async def provider(*args, **kwargs):
            self.assertIn('最终输出必须是一个 JSON', kwargs['system'])
            raw=json.dumps({'status':'answered','answer_blocks':[{'text':'已校验回答',
                'evidence_ids':['a'],'quote':'有用规则'}],'missing_information':[]})
            yield raw[:20]
            yield raw[20:]
        state,trace,answer = await self.execute({'status':'sufficient','missing_information':[],
            'evidence_ids':['a']}, provider)
        self.assertEqual(state['status'],'succeeded')
        self.assertIn('已校验回答',answer)
        self.assertNotIn('answer_blocks',answer)
        self.assertTrue(any(s['stage']=='citation_validation' and s['status']=='succeeded' for s in trace['spans']))

    async def test_hosted_invalid_raw_stream_is_never_published(self):
        async def provider(*args, **kwargs):
            yield '不应泄漏'
            yield '的错误原始输出'
        state,trace,answer = await self.execute({'status':'sufficient','missing_information':[],
            'evidence_ids':['a']}, provider)
        self.assertEqual(state['status'],'succeeded')
        self.assertNotIn('不应泄漏',answer)
        self.assertIn('未通过来源校验',answer)
        self.assertTrue(any(s['stage']=='citation_validation' and s['status']=='failed' for s in trace['spans']))


class AdditionalBoundaryTests(unittest.TestCase):
    def test_same_url_sections_have_distinct_ids_and_headings_do_not_mix(self):
        a, ca = split_document({'title':'甲','source_url':'https://example.com/shared','content':'# 第一节\n甲\n## 第二节\n乙'})
        b, _ = split_document({'title':'乙','source_url':'https://example.com/shared','content':'另一节'})
        self.assertNotEqual(a['doc_id'],b['doc_id'])
        self.assertEqual(len(ca),2)
        self.assertNotIn('第二节', ca[0]['parent_text'])
        self.assertIn('第一节 / 第二节', ca[1]['section_path'])
        with self.assertRaises(ValueError):
            split_document({'title':'私有','content':'正文','scope_id':'other-brand'})

    def test_model_receipts_keep_unknown_cost_null(self):
        from core.rag.usage import observed_create, receipts
        async def run():
            rows=[]
            token=receipts.set(rows)
            try:
                client=SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(usage=None))))
                await observed_create(client,stage='rerank',model='test')
                self.assertEqual(rows[0]['status'],'succeeded')
                self.assertIsNone(rows[0]['provider_cost'])
            finally:
                receipts.reset(token)
        asyncio.run(run())
