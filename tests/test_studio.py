import asyncio
import json
import os
import unittest
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from api.studio import StudioInput, generate, route, TEXT_MODEL, VIDEO_MODEL, UnderstandingInput, understand, UNDERSTANDING_MODEL

FIXTURE = {'summary':'根据商家描述形成牙刷创意草稿。','questions':['请补充真实售价。'],
 'concepts':[{'title':'通勤随身','scenario':'上班出门','copy':'为你的通勤准备一支牙刷。','script':'0–5秒：白色牙刷特写。','video_prompt':'白色牙刷静置桌面，镜头缓慢推进，无文字。','assumptions':['通勤场景需要验证']}],
 'delivery':{'objective':'traffic','audience_hypotheses':['通勤人群'],'test_plan':'两版素材小额试投。','rationale':'先验证需求。','missing_data':['预算','渠道']}}

class StudioTest(unittest.IsolatedAsyncioTestCase):
 async def test_fixed_model_and_no_media_submission(self):
  calls=[]
  def handler(req):
   calls.append(json.loads(req.content));return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(FIXTURE,ensure_ascii=False)},'finish_reason':'stop'}]})
  with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}):
   body=StudioInput(brief={'subject_description':'白色牙刷'},has_assets=True,message='先不要生成视频，写一个脚本')
   result=await generate(body,'test',httpx.MockTransport(handler))
  self.assertEqual(calls[0]['model'],TEXT_MODEL)
  self.assertNotIn('image_url',str(calls[0]))
  self.assertIsNone(result['delivery']['bid']['amount_minor'])
  self.assertEqual(result['understanding']['status'],'capability_unavailable')
  self.assertEqual(len(calls),1)
 async def test_visual_analysis_blocked_without_provider_call(self):
  called=[]
  with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}):
   with self.assertRaises(HTTPException) as cm:
    await generate(StudioInput(brief={},has_assets=True,output='analysis'),'test',httpx.MockTransport(lambda r:called.append(r)))
  self.assertEqual(cm.exception.status_code,422);self.assertEqual(called,[])
 async def test_invalid_truncated_and_timeout_do_not_fallback(self):
  for response in [{'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}, {'choices':[{'message':{'content':json.dumps(FIXTURE)},'finish_reason':'length'}]}]:
   count=[]
   def handler(r):count.append(r);return httpx.Response(200,json=response)
   with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}):
    with self.assertRaises(HTTPException): await generate(StudioInput(brief={'idea':'推广'}),'test',httpx.MockTransport(handler))
   self.assertEqual(len(count),1)
 async def test_cpc_cap_is_decimal_and_zero_probability_is_not_a_bid(self):
  for probability, expected in [(0.1,59),(0,None)]:
   with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}):
    result=await generate(StudioInput(brief={'subject_description':'牙刷','bid_basis':{'channel':'试投渠道','contribution_minor':1999,'purchase_probability':probability,'ad_share':0.3}}),'test',httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(FIXTURE)},'finish_reason':'stop'}]})))
   self.assertEqual(result['delivery']['bid']['amount_minor'],expected)
 async def test_seed_image_and_video_frames_with_real_content_parts(self):
  seen=[]
  fixture={'subject':'牙刷','observations':[{'description':'白色手柄','frame_index':0}],'source_claims':['抗菌99%（素材宣称）'],'marketing_summary':'商品特写','uncertainties':['功效未验证']}
  def handler(req):
   seen.append(json.loads(req.content));return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(fixture)},'finish_reason':'stop'}]})
  with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}):
   for kind in ['image','video']:
    result=await understand(UnderstandingInput(asset_id='a1',kind=kind,frames=[{'data_url':'data:image/png;base64,aW1hZ2U=','time_ms':None if kind=='image' else 1500}]),'test',httpx.MockTransport(handler))
    self.assertEqual(result['model'],UNDERSTANDING_MODEL);self.assertEqual(result['observations'][0]['asset_id'],'a1')
    self.assertEqual(bool(result['limitations']),kind=='video')
  self.assertTrue(all(r['model']==UNDERSTANDING_MODEL for r in seen))
  self.assertEqual(seen[0]['messages'][1]['content'][-1]['type'],'image_url')
 async def test_understanding_rejects_urls_and_invalid_frame_references(self):
  with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}):
   with self.assertRaises(HTTPException):await understand(UnderstandingInput(asset_id='a1',kind='image',frames=[{'data_url':'https://127.0.0.1'}]),'test')
   wrong={'subject':'商品','observations':[{'description':'画面','frame_index':7}],'source_claims':[],'marketing_summary':'','uncertainties':[]}
   with self.assertRaises(HTTPException):await understand(UnderstandingInput(asset_id='a1',kind='video',frames=[{'data_url':'data:image/png;base64,aW1hZ2U='}]),'test',httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(wrong)}}]})))
 def test_image_route_and_unknown_model_denied(self):
  self.assertEqual(route('video'),VIDEO_MODEL)
  self.assertEqual(route('text'),TEXT_MODEL)
  self.assertEqual(route('image'),'doubao-seedream-5-0-flash-260915')
  for value in ['seedream','publish']:
   with self.assertRaises(HTTPException): route(value)

if __name__=='__main__':unittest.main()
