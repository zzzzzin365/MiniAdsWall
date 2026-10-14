import unittest
from mcp.ads_tools import ads_summary_handler, ad_performance_search_handler


class HostedPerformanceToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_uses_window_ledger_and_excludes_zero_exposure(self):
        context = {'ads':[{'id':'bad','clicks':999999,'price':100}],
            'performance_context':{'source':'ad_performance_ledger','scope':'selected_authorized_ads',
                'window':['2026-10-14'],'summary':{'clicks':10,'spend_minor':400},
                'items':[{'ad_id':'bad','impressions':100,'clicks':10,'spend_minor':400,'orders':0,'net_roas':0},
                         {'ad_id':'good','impressions':100,'clicks':10,'spend_minor':100,'orders':2,'net_roas':3},
                         {'ad_id':'idle','impressions':0,'clicks':0,'spend_minor':0,'orders':0,'net_roas':None}]}}
        summary = await ads_summary_handler({}, context)
        self.assertEqual(summary['summary']['clicks'],10)
        rows = await ad_performance_search_handler({'top_k':5},context)
        self.assertEqual([r['ad_id'] for r in rows],['bad','good'])
        self.assertIn('没有支付订单',rows[0]['reasons'][0])
        self.assertEqual(rows[0]['window'],['2026-10-14'])
        self.assertEqual(rows[0]['money_unit'],'fen')

    async def test_absent_ledger_keeps_existing_legacy_tool_contract(self):
        context = {'ads':[{'id':'1','title':'ad','clicks':3,'price':2}]}
        summary = await ads_summary_handler({},context)
        self.assertEqual(summary['total_clicks'],3)
        self.assertEqual((await ad_performance_search_handler({'top_k':1},context))[0]['id'],'1')
