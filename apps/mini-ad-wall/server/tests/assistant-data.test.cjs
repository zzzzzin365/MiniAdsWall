const { test } = require('node:test');
const assert = require('node:assert/strict');
const { assistantPerformance } = require('../dist/services/assistant-data');
const fixture = require('../../client/src/data/merchant-demo.json');
const context = { captured_at:'2026-10-14T12:00:00Z', selection:{limit:50,truncated:false},
    items:fixture.ads.map(ad=>({id:ad.id,title:ad.title,material_id:ad.material_id,clicks:999999})) };
test('assistant treats recorded ads as delivered and reconciles metrics with the dashboard ledger', () => {
    const data = assistantPerformance(context,'7');
    assert.equal(data.summary.clicks,1043);
    assert.equal(data.summary.orders,18);
    assert.equal(data.summary.spend_minor,58782);
    assert.equal(data.summary.net_revenue_minor,114300);
    assert.equal(data.summary.net_roas,114300/58782);
    assert.equal(data.source,'ad_performance_ledger');
    assert(data.items.every(item=>item.delivery_status==='delivered'));
    assert.equal(data.account_daily_budget_minor,10000);
    assert.doesNotMatch(JSON.stringify(data),/模拟|synthetic|演示/);
    assert.equal(data.money_unit,'fen');
    assert.equal(data.measured_count,20);
    assert(data.items.every(item=>!item.material_changed));
    assert.equal(assistantPerformance(context,'3').window.length,3);
    assert(assistantPerformance(context,'3').summary.clicks < data.summary.clicks);
});
test('new ads stay unknown, zero exposure has no CTR, and edited materials retain historical attribution', () => {
    const data = assistantPerformance({...context,items:[{id:'new-ad'}, {...context.items[0],material_id:'new-material'}]});
    assert.deepEqual(data.missing_ad_ids,['new-ad']);
    assert.equal(data.unlaunched_ads[0].delivery_status,'not_delivered');
    assert.equal(data.items.length,1);
    assert.equal(data.items[0].material_changed,true);
    const zero = assistantPerformance(context,'3').items.find(item=>item.impressions===0);
    assert(zero);
    assert.equal(zero.ctr,null);
    assert.equal(zero.net_roas,null);
    assert.throws(()=>assistantPerformance(context,'30'),/invalid_performance_range/);
});
