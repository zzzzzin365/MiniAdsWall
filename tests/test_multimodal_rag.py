import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import chromadb
import httpx
from fastapi import HTTPException

from api.studio import StudioInput, generate
from core.multimodal_rag import MediaCorpus, chunks, retrieve
from tests.test_studio import FIXTURE


class ControlledEmbedding:
    # Freeze vectors to test filtering/ranking rather than provider quality.
    def __call__(self, input):
        return [[1., 0.] if 'toothbrush' in text else [0., 1.] for text in input]


def corpus(brand='brand-a', environment='synthetic', asset='image-a', revision=1,
           subject='toothbrush dental care', kind='image'):
    return MediaCorpus(brand_id=brand, environment=environment, sources=[{
        'asset_id': asset, 'asset_revision': revision, 'analysis_job_id': 'analysis-' + asset,
        'kind': kind, 'subject': subject, 'marketing_summary': subject,
        'observations': [{'description': subject + ' on a table', 'frame_index': 0,
                          'time_ms': 1500 if kind == 'video' else None}],
        'source_claims': ['antibacterial 99% (unverified)'], 'uncertainties': ['effect not verified'],
        'limitations': ['sampled frames only, no audio'] if kind == 'video' else []}])


class MediaRagTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mode = patch.dict(os.environ, {'STUDIO_MEDIA_RAG_MODE': 'caption'})
        mode.start()
        self.addCleanup(mode.stop)
        self.public = patch('api.studio.retrieve_references', return_value={'status':'empty', 'retrieved':[], 'citations':[]})
        self.public.start()
        self.addCleanup(self.public.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.client = chromadb.PersistentClient(path=self.tmp.name,
            settings=chromadb.Settings(anonymized_telemetry=False))
        self.index = self.client.get_or_create_collection('media_test',
            embedding_function=ControlledEmbedding(), metadata={'hnsw:space': 'cosine'})

    def tearDown(self):
        self.tmp.cleanup()

    def test_persistent_recall_is_scoped_by_brand_environment_and_current_version(self):
        a = corpus()
        report = retrieve(a, 'toothbrush', index=self.index)
        self.assertEqual(report['status'], 'retrieved')
        original_ids = {s['id'] for s in report['retrieved']}
        self.assertEqual(self.index.count(), 2)
        retrieve(a, 'toothbrush', index=self.index)
        self.assertEqual(self.index.count(), 2)  # repeated turns do not duplicate vectors
        for other in [corpus(brand='brand-b'), corpus(environment='production')]:
            b = retrieve(other, 'toothbrush', index=self.index)
            self.assertFalse(original_ids & {s['id'] for s in b['retrieved']})
        newer = corpus(revision=2, subject='coffee cup')
        self.assertEqual(retrieve(newer, 'toothbrush', index=self.index)['status'], 'no_match')
        self.assertEqual(retrieve(MediaCorpus(brand_id='brand-a', environment='synthetic', sources=[]),
                                  'toothbrush', index=self.index)['retrieved'], [])
        # Reopen the collection and verify stored vectors and source IDs are reused.
        reopened = self.client.get_collection('media_test', embedding_function=ControlledEmbedding())
        self.assertEqual(original_ids, {e['id'] for e in retrieve(a, 'toothbrush', index=reopened)['retrieved']})

    def test_video_frame_time_and_unverified_claims_survive_retrieval(self):
        report = retrieve(corpus(asset='video-a', kind='video'), 'toothbrush', index=self.index)
        frame = next(e for e in report['retrieved'] if e['frame_index'] == 0)
        self.assertEqual(frame['time_ms'], 1500)
        self.assertEqual(frame['kind'], 'video')
        self.assertIn('unverified', frame['source_claims'][0])
        self.assertIn('no audio', frame['limitations'][0])
        self.assertNotIn(frame['source_claims'][0], frame['excerpt'])

    async def test_retrieved_historical_media_reaches_model_and_validated_citations_return(self):
        seen = []
        a = corpus(asset='historical-video', kind='video')
        def model(req):
            content = json.loads(req.content)
            data = json.loads(content['messages'][1]['content'])
            seen.append(data)
            result = copy.deepcopy(FIXTURE)
            frame = next(e for e in data['retrieved_media'] if e['frame_index'] == 0)
            result['source_ids'] = [frame['id']]
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
        def search(c, q):
            return retrieve(c, q, index=self.index)
        with patch.dict(os.environ, {'ARK_API_KEY': 'test-only'}), patch('api.studio.retrieve_media', search):
            result = await generate(StudioInput(brief={'subject_description': 'toothbrush'},
                message='Find an earlier toothbrush ad', media_knowledge=a), 'test', httpx.MockTransport(model))
        self.assertEqual(len(seen), 1)
        self.assertNotIn('media_knowledge', seen[0])
        self.assertEqual(seen[0]['asset_understandings'], [])  # not an attachment passthrough
        self.assertEqual(result['media_rag']['citations'][0]['asset_id'], 'historical-video')
        self.assertEqual(result['media_rag']['citations'][0]['time_ms'], 1500)
        self.assertEqual(result['understanding']['status'], 'seed_understood')

    async def test_invented_source_rejected_and_retrieval_failure_never_calls_model(self):
        called = []
        def model(req):
            called.append(req)
            result = copy.deepcopy(FIXTURE)
            result['source_ids'] = ['f' * 64]
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
        body = StudioInput(brief={'subject_description': 'toothbrush'}, media_knowledge=corpus())
        with patch.dict(os.environ, {'ARK_API_KEY': 'test-only'}):
            with patch('api.studio.retrieve_media', lambda c, q: retrieve(c, q, index=self.index)):
                with self.assertRaises(HTTPException) as cm:
                    await generate(body, 'test', httpx.MockTransport(model))
                self.assertEqual(cm.exception.status_code, 502)
            called.clear()
            with patch('api.studio.retrieve_media', side_effect=RuntimeError('index down')):
                with self.assertRaises(HTTPException) as cm:
                    await generate(body, 'test', httpx.MockTransport(model))
                self.assertEqual(cm.exception.status_code, 503)
                self.assertEqual(called, [])

    def test_threshold_validation_and_scoped_stable_ids(self):
        self.assertNotEqual(chunks(corpus())[0]['id'], chunks(corpus(brand='brand-b'))[0]['id'])
        for score in [float('nan'), 2, -1]:
            with self.assertRaises(ValueError):
                retrieve(corpus(), 'toothbrush', index=self.index, min_score=score)


if __name__ == '__main__':
    unittest.main()
