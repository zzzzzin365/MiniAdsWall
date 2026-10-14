import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import chromadb
import httpx
from fastapi import HTTPException

from api.studio import StudioInput, generate
from mcp.external_marketing import records, retrieve, sync
from tests.test_multimodal_rag import ControlledEmbedding
from tests.test_studio import FIXTURE


class ExternalKnowledgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = Path(self.tmp.name) / 'catalog.json'
        self.catalog.write_text(json.dumps({'synthetic': False, 'sources': [{
            'id':'test-source', 'platform':'twitter', 'url':'https://x.com/test/status/1',
            'title':'toothbrush creative', 'author':'fixture', 'summary':'toothbrush creative CTA',
            'coverage':'preview_only', 'retrieved_at':'2026-10-14', 'images':[{
                'label':'fixture', 'url':'https://pbs.twimg.com/media/test.jpg', 'local_path':'images/test.jpg',
                'caption':'toothbrush on a table'}]}]}))
        client = chromadb.PersistentClient(path=str(Path(self.tmp.name) / 'index'),
            settings=chromadb.Settings(anonymized_telemetry=False))
        self.index = client.get_or_create_collection('external_test', embedding_function=ControlledEmbedding(),
            metadata={'hnsw:space':'cosine'})

    def test_upserts_are_idempotent_and_private_or_stale_rows_are_not_retrieved(self):
        self.index.add(ids=['private'], documents=['toothbrush private brand'],
                       metadatas=[{'source':'private','external_chunk_id':'private'}])
        self.assertEqual(sync(self.index, self.catalog)['inserted'], 2)
        self.assertEqual(sync(self.index, self.catalog)['inserted'], 0)
        self.assertEqual(self.index.count(), 3)
        report = retrieve('toothbrush', index=self.index, catalog=self.catalog)
        self.assertEqual(len(report['retrieved']), 2)
        self.assertTrue(all(r['id'] != 'private' and r['coverage'] == 'preview_only' for r in report['retrieved']))
        self.assertEqual(report['retrieved'][0]['images'][0]['mime'], 'image/jpeg')
        changed = json.loads(self.catalog.read_text())
        changed['sources'][0]['summary'] = 'coffee cup'
        changed['sources'][0]['images'] = []
        self.catalog.write_text(json.dumps(changed))
        self.assertEqual(retrieve('toothbrush',index=self.index,catalog=self.catalog)['status'], 'no_match')

    def test_synthetic_catalog_and_wrong_source_domain_rejected(self):
        for changes in [{'synthetic':True}, {'sources':[{'platform':'twitter','url':'http://127.0.0.1/'}]}]:
            self.catalog.write_text(json.dumps(changes))
            with self.assertRaises(ValueError): records(self.catalog)

    async def test_reference_reaches_generation_and_unknown_citations_fail(self):
        reference = {**records(self.catalog)[0], 'score': .9}
        report = {'status':'retrieved', 'retrieved':[reference], 'citations':[]}
        empty = {'status':'empty','method':'caption-vector-v1','corpus_assets':0,
                 'corpus_truncated':False,'retrieved':[],'citations':[]}
        body = StudioInput(brief={'subject_description':'toothbrush'}, media_knowledge={
            'brand_id':'brand','environment':'synthetic','sources':[]})
        for ident, valid in [(reference['id'], True), ('f' * 64, False)]:
            def model(req):
                data = json.loads(json.loads(req.content)['messages'][1]['content'])
                self.assertEqual(data['reference_knowledge'][0]['coverage'],'preview_only')
                answer = copy.deepcopy(FIXTURE);answer['reference_ids']=[ident]
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)}}]})
            with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}), \
                    patch('api.studio.retrieve_media',return_value=empty), \
                    patch('api.studio.retrieve_references',return_value=copy.deepcopy(report)):
                if valid:
                    result = await generate(body,'test',httpx.MockTransport(model))
                    self.assertEqual(result['reference_knowledge']['citations'][0]['id'],ident)
                else:
                    with self.assertRaises(HTTPException): await generate(body,'test',httpx.MockTransport(model))


if __name__ == '__main__': unittest.main()
