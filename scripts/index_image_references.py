"""Index the curated raw reference images, then run Chinese image-search smoke checks.
No paid LLM/vision requests, no private merchant data, no old experiment results overwritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.image_embeddings import encoder, open_visual_index, MODEL_ID, MODEL_REVISION, DIMENSION
from mcp.external_marketing import retrieve_images, visual_records, CATALOG


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--receipt',default=str(ROOT/'docs/validation/native-image-rag/20261015-v1/smoke.json'))
    parser.add_argument('--query',action='append')
    args=parser.parse_args()
    os.environ['STUDIO_MEDIA_RAG_MODE']='native'
    started=time.perf_counter();model=encoder();load_ms=1000*(time.perf_counter()-started)
    index=open_visual_index('external_image_chinese_clip_v1');before=index.count()
    assets=visual_records();queries=args.query or ['紫色背景橙色按钮的广告','汽车展示图','紫色卡通猫头鹰']
    results=[]
    for query in queries:
        t=time.perf_counter();report=retrieve_images(query,index=index,model=model)
        results.append({'query':query,'elapsed_ms':round(1000*(time.perf_counter()-t),2),
                        'retrieved':[{'id':e['id'],'source_id':e['source_id'],'label':e['images'][0]['label'],
                                      'score':e['score'],'image_sha256':e['image_sha256']} for e in report['retrieved']]})
    rows=index.get(ids=[a['id'] for a in assets],include=['embeddings'])
    assert len(rows['ids'])==len(assets)
    assert all(len(v)==DIMENSION for v in rows['embeddings'])
    import numpy as np
    from PIL import Image, ImageOps
    import io
    fresh=model.images([a['_raw'] for a in assets])
    stored=dict(zip(rows['ids'],rows['embeddings']))
    assert all(np.allclose(v,stored[a['id']],atol=1e-6) for a,v in zip(assets,fresh))
    # Check the text adapter against the model's official joint forward path.
    image=ImageOps.exif_transpose(Image.open(io.BytesIO(assets[0]['_raw']))).convert('RGB')
    inputs=model.processor(text=[queries[0]],images=[image],padding=True,return_tensors='pt')
    with model.torch.inference_mode(): joint=model.model(**inputs)
    text_error=float(np.max(np.abs(np.asarray(model.text(queries[0]))-joint.text_embeds[0].cpu().numpy())))
    assert text_error<1e-6
    assert np.allclose(fresh[0],joint.image_embeds[0].cpu().numpy(),atol=1e-6)
    # Exercise the real ASGI routes; no external answer model is called.
    import asyncio
    import httpx
    from fastapi import FastAPI
    from api.studio import router
    from jsonschema import validate
    async def api_checks():
        app=FastAPI();app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local-smoke') as client:
            response=await client.post('/studio/reference-search',json={'query':queries[0]})
            assert response.status_code==200,response.text
            report=response.json();assert report['method']=='chinese-clip-image-v1'
            schema=json.loads((ROOT/'contracts/merchant/studio/v1/result.schema.json').read_text())
            validate(report,{'$defs':schema['$defs'],**schema['properties']['reference_knowledge']})
            encoded=await client.post('/studio/embed',json={'asset_id':'real-external-smoke','kind':'image',
                'frames':[{'data_url':'data:image/jpeg;base64,'+__import__('base64').b64encode(assets[0]['_raw']).decode()}]})
            assert encoded.status_code==200,encoded.text
            body=encoded.json();assert len(body['visual_embeddings'][0]['vector'])==512
            assert body['visual_embeddings'][0]['image_sha256']==assets[0]['image_sha256']
            return {'reference_search_status':response.status_code,'embed_status':encoded.status_code,
                    'reported_method':report['method'],'image_vector_dimension':512,'paid_model_requests':0,
                    'scope':'real ASGI endpoints; auth tested separately in Koa lifecycle'}
    api_result=asyncio.run(api_checks())

    # Strip every image caption. Rankings/scores/IDs must remain unchanged.
    stripped=json.loads(CATALOG.read_text())
    for source in stripped['sources']:
        source['summary']='本项仅用于无图片描述的原图检索核验。'
        for image in source.get('images',[]):
            image['caption']=''
            image['local_path']=str(Path(image['local_path']).relative_to('data/external-marketing'))
    temporary=CATALOG.parent/'.native-no-caption-check.json'
    try:
        temporary.write_text(json.dumps(stripped,ensure_ascii=False))
        for query, original in zip(queries,results):
            check=retrieve_images(query,catalog=temporary,index=index,model=model)
            assert [(e['id'],e['score']) for e in check['retrieved']]==[(e['id'],e['score']) for e in original['retrieved']]
    finally:
        temporary.unlink(missing_ok=True)
    receipt={'status':'passed','date':'2026-10-15','method':'chinese-clip-image-v1','model':MODEL_ID,
             'model_revision':MODEL_REVISION,'dimension':DIMENSION,'model_load_ms':round(load_ms,2),
             'collection':index.name,'before':before,'after':index.count(),'raw_images':len(assets),
             'stored_vectors_verified':len(rows['ids']),'caption_removal_rankings_identical':True,'pixel_reencoding_matches_stored_vectors':True,
             'joint_forward_text_max_error':text_error,'api_checks':api_result,
             'paid_model_requests':0,'scope':'curated external images only','queries':results,
             'limits':'Functional smoke, not a Recall/P95 improvement experiment. First request may include indexing if vectors are absent; model load reported separately.',
             'source_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                              ['core/image_embeddings.py','core/multimodal_rag.py','mcp/external_marketing.py','api/studio.py']}}
    out=Path(args.receipt);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
