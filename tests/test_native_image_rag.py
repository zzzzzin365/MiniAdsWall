"""Deterministic real-Chroma tests of native pixel-vector search, not quality benchmarks."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import chromadb
import httpx
from fastapi import HTTPException
from core.image_embeddings import (MODEL_ID, MODEL_REVISION, METHOD, validate_vector, embed_frames)
from core.multimodal_rag import MediaCorpus, native_chunks, retrieve
from mcp.external_marketing import retrieve_images, visual_records
from api.studio import StudioInput, generate, UnderstandingInput, embed_media
from tests.test_multimodal_rag import corpus
from tests.test_studio import FIXTURE


def vector(x=0):
    return [float(i == x) for i in range(512)]


def visual(raw=b'pixels', frame=0, time=None, dim=0):
    return {'model':MODEL_ID,'revision':MODEL_REVISION,'image_sha256':hashlib.sha256(raw).hexdigest(),
            'frame_index':frame,'time_ms':time,'vector':vector(dim)}


class FakeEncoder:
    def __init__(self): self.image_calls=[];self.text_calls=[]
    def images(self, raws):
        self.image_calls.extend(raws)
        return [vector(0 if raw==b'red-pixels' else 1) for raw in raws]
    def text(self, text):
        self.text_calls.append(text)
        return vector(0 if '红' in text else 1)


class NativeImageRagTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.mode=patch.dict(os.environ,{'STUDIO_MEDIA_RAG_MODE':'native'});self.mode.start();self.addCleanup(self.mode.stop)
        self.client=chromadb.PersistentClient(path=self.tmp.name+'/index',settings=chromadb.Settings(anonymized_telemetry=False))
        self.index=self.client.get_or_create_collection('native_test',embedding_function=None,metadata={'hnsw:space':'cosine'})
        self.model=FakeEncoder()

    def source(self, **kwargs):
        c=corpus(**kwargs)
        from core.multimodal_rag import VisualEmbedding
        c.sources[0].visual_embeddings=[VisualEmbedding.model_validate(visual(frame=0,time=1500 if c.sources[0].kind=='video' else None))]
        # Revalidate assignment inputs; normal API requests are already validated by Pydantic.
        return MediaCorpus.model_validate(c.model_dump())

    def test_pixels_are_indexed_without_any_caption_embedding_and_reused(self):
        c=self.source(subject='故意写成蓝色，不能决定向量匹配')
        result=retrieve(c,'红色',index=self.index,model=self.model)
        self.assertEqual(result['method'],METHOD)
        self.assertEqual(result['retrieved'][0]['score'],1.)
        self.assertEqual(self.model.text_calls,['红色'])
        self.assertEqual(self.model.image_calls,[]) # already encoded at ingestion
        self.assertNotIn('_vector',result['retrieved'][0])
        self.assertEqual(self.index.get(include=['embeddings'])['embeddings'][0].tolist(),vector())
        retrieve(c,'红色',index=self.index,model=self.model)
        self.assertEqual(self.index.count(),1)

    def test_authorized_exact_ids_exclude_cross_brand_environment_revision_and_stale_rows(self):
        old=self.source(); ids={e['id'] for e in retrieve(old,'红',index=self.index,model=self.model)['retrieved']}
        for c in [self.source(brand='other'),self.source(environment='production'),self.source(revision=2)]:
            report=retrieve(c,'红',index=self.index,model=self.model)
            self.assertFalse(ids & {e['id'] for e in report['retrieved']})
        newer=self.source(revision=3);newer.sources[0].visual_embeddings[0].vector=vector(1)
        self.assertEqual(retrieve(newer,'红',index=self.index,model=self.model,min_score=.1)['status'],'no_match')

    def test_legacy_missing_vectors_and_wrong_model_rejected_instead_of_caption_fallback(self):
        with self.assertRaisesRegex(ValueError,'reindex'):
            retrieve(corpus(),'红',index=self.index,model=self.model)
        bad=self.source().model_dump();bad['sources'][0]['visual_embeddings'][0]['revision']='untrusted'
        with self.assertRaises(ValueError): MediaCorpus.model_validate(bad)
        for v in [vector()[:-1],[0.]*512,[float('nan')]+[0.]*511]:
            with self.assertRaises(ValueError):validate_vector(v)

    def test_video_frame_timestamps_and_source_binding_survive(self):
        c=self.source(kind='video');a=retrieve(c,'红',index=self.index,model=self.model)['retrieved'][0]
        self.assertEqual(a['time_ms'],1500);self.assertEqual(a['frame_index'],0)
        self.assertEqual(a['embedding_revision'],MODEL_REVISION)
        self.assertNotEqual(native_chunks(c)[0]['id'],native_chunks(self.source(kind='video',revision=2))[0]['id'])

    def test_external_caption_changes_do_not_change_pixel_similarity(self):
        root=Path(self.tmp.name);(root/'images').mkdir();(root/'images/red.png').write_bytes(b'red-pixels');(root/'images/blue.png').write_bytes(b'blue-pixels')
        source={'id':'public','platform':'twitter','url':'https://x.com/public/status/1','title':'fixture',
                'author':'fixture','summary':'marketing','coverage':'visible','retrieved_at':'2026-10-15',
                'images':[{'label':name,'url':'https://example.com/'+name+'.png','local_path':'images/'+name+'.png',
                           'caption':'相同且误导的描述'} for name in ['red','blue']]}
        file=root/'catalog.json';file.write_text(json.dumps({'synthetic':False,'sources':[source]}))
        report=retrieve_images('红色',catalog=file,index=self.index,model=self.model)
        self.assertEqual(report['retrieved'][0]['images'][0]['label'],'red')
        self.assertEqual(set(self.model.image_calls),{b'red-pixels',b'blue-pixels'})
        self.assertFalse(any('_raw' in x for x in report['retrieved']))
        source['images'][0]['caption']='另外一份错误描述'
        file.write_text(json.dumps({'synthetic':False,'sources':[source]}))
        changed=retrieve_images('红色',catalog=file,index=self.index,model=self.model)
        self.assertEqual(changed['retrieved'][0]['id'],report['retrieved'][0]['id'])
        self.assertEqual(len(self.model.image_calls),2)
        source['images'][0]['local_path']='../escape.png';file.write_text(json.dumps({'synthetic':False,'sources':[source]}))
        with self.assertRaises(ValueError):visual_records(file)

    async def test_native_match_without_seed_is_not_reported_as_seed_understanding(self):
        c=self.source();c.sources[0].understanding_available=False
        report=retrieve(c,'红',index=self.index,model=self.model)
        answer=copy.deepcopy(FIXTURE);answer['source_ids']=[report['retrieved'][0]['id']]
        with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}),patch('api.studio.retrieve_media',return_value=report),patch('api.studio.retrieve_references',return_value={'status':'empty','retrieved':[],'citations':[]}):
            result=await generate(StudioInput(brief={'subject_description':'手串'},media_knowledge=c),'native-test',
                httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)}}]})))
        self.assertEqual(result['understanding']['status'],'visual_indexed')
        self.assertEqual(result['understanding']['source'],'image_embeddings')
        self.assertEqual(result['media_rag']['citations'][0]['image_sha256'],visual()['image_sha256'])

    async def test_embedding_endpoint_has_no_paid_provider_or_key_dependency(self):
        data='data:image/png;base64,'+base64.b64encode(b'red-pixels').decode()
        body=UnderstandingInput(asset_id='x',kind='image',frames=[{'data_url':data}])
        with patch('core.image_embeddings.encoder',return_value=self.model):
            result=await embed_media(body)
        self.assertEqual(result['visual_embeddings'][0]['vector'],vector())
        self.assertEqual(result['method'],METHOD)
        with self.assertRaises(HTTPException):
            await embed_media(UnderstandingInput(asset_id='x',kind='image',frames=[{'data_url':'http://localhost/secret'}]))

    async def test_vectors_are_not_sent_to_answer_model(self):
        captured=[]
        def answer(req):
            data=json.loads(json.loads(req.content)['messages'][1]['content'])
            captured.append(data)
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(FIXTURE)}}]})
        with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}):
            await generate(StudioInput(brief={},asset_understandings=[{'subject':'已理解图片','visual_embeddings':[visual()]}]),
                           'strip-embeddings',httpx.MockTransport(answer))
        self.assertNotIn('visual_embeddings',captured[0]['asset_understandings'][0])
