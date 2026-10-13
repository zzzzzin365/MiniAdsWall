import json
import os
import unittest
from unittest.mock import patch
import httpx
from api.generation import GenerationInput, generate

class DeliveryAdvice(unittest.IsolatedAsyncioTestCase):
 async def test_deepseek_only_and_no_unfounded_bid(self):
  original=httpx.AsyncClient
  requests=[]
  def handler(req):
   data=json.loads(req.content);requests.append(data)
   return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'targetUsers':['潜在客户'],'interests':['产品'],'recommendedObjective':'有效咨询','bidSuggestion':10,'reason':'缺少预算与转化数据'})}}]})
  with patch.dict(os.environ,{'ARK_API_KEY':'test-only'}),patch('api.generation.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
   result=await generate('strategy',GenerationInput(adDescription='推广牙刷',industry='日用品'),'test')
  self.assertEqual(requests[0]['model'],'deepseek-v4-1-flash-260910')
  self.assertIsNone(result.bidSuggestion)
  self.assertEqual(result.recommendedObjective,'有效咨询')
  self.assertNotIn('titles',result.model_dump())
if __name__=='__main__':unittest.main()
