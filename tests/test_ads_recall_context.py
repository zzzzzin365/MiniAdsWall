import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from hosting.api import RunInput
from hosting.repository import Repository
from hosting.objects import Objects
from mcp.ads_tools import ads_summary_handler, ad_performance_search_handler

class RecallContextTests(unittest.TestCase):
    def test_protocol_limits_and_explicit_missing_summary(self):
        context={'version':1,'scope':'authorized_ads','summary':None,'summary_status':'not_materialized','items':[{'id':'1','price':1,'clicks':5,'video_count':3,'videos':[]}],'selection':{'truncated':True}}
        with patch.dict(os.environ,{'AD_CONTEXT_V1_ENABLED':'true'}):
            self.assertEqual(RunInput(message='hello',ad_context=context).ad_context,context)
            with self.assertRaises(ValueError):RunInput(message='hello',ad_context={**context,'items':[{}]*101})
        result=asyncio.run(ads_summary_handler({}, {'ad_context':context}))
        self.assertIsNone(result['summary']);self.assertEqual(result['summary_status'],'not_materialized')
        selected=asyncio.run(ad_performance_search_handler({}, {'ad_context':context}))
        self.assertEqual(selected[0]['scope'],'selected_items');self.assertEqual(selected[0]['videos'],3)

    def test_retries_preserve_the_first_context_and_changed_intent_conflicts(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'AD_CONTEXT_V1_ENABLED':'true'}):
            repo=Repository('sqlite://',Objects(folder),'s'*32,test=True);repo.migrate()
            try:
                identity=repo.provision('alice');user=identity['user_id'];session=repo.create_session(user,identity['workspace_id'],'context')
                payload={'message':'hello','conditions':[],'ad_context':{'version':1,'items':[{'id':'first'}]}}
                first=repo.create_run(user,session['id'],'context-idempotent-key',payload)
                second=repo.create_run(user,session['id'],'context-idempotent-key',{**payload,'ad_context':{'version':1,'items':[{'id':'later'}]}})
                self.assertEqual(first['id'],second['id']);stored=json.loads(repo.objects.get(first['input_ref']));self.assertEqual(stored['ad_context']['items'][0]['id'],'first')
                with self.assertRaises(Exception):repo.create_run(user,session['id'],'context-idempotent-key',{**payload,'message':'different'})
            finally:repo.engine.dispose()
