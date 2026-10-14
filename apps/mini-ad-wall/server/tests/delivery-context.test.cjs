const {test} = require('node:test');
const assert = require('node:assert/strict');
const {buildDeliveryContext,validateSettings} = require('../dist/services/delivery-context');
const fixture = require('../../client/src/data/merchant-demo.json');
test('ledger-backed ads are launched and use window metrics rather than lifetime clicks',()=>{
 const row=fixture.ads[0];
 const result=buildDeliveryContext({id:row.id,price:.7,material_id:row.material_id,videos:[],images:['image'],clicks:999999});
 assert.equal(result.status,'launched');assert.equal(result.currentBid,.7);
 assert.equal(result.performance.clicks,row.daily.reduce((n,d)=>n+d.click_costs_minor.length,0));
 assert.equal(result.defaults.dailyBudget,row.daily_budget_minor/100);
 assert.equal(result.performance.daily.length,row.daily.length);
 assert.equal(result.performance.daily.reduce((sum,day)=>sum+day.clicks,0),result.performance.clicks);
 assert.equal(result.defaults.maxCpc,row.max_cpc_minor/100);
 assert.equal(result.material.image_count,1);
 assert.equal(result.performance.material_changed,false);
 assert.equal(buildDeliveryContext({id:row.id,price:.7,material_id:'changed'},'3').performance.material_changed,true);
});
test('new and copied ads have no invented delivery history or budget',()=>{
 const result=buildDeliveryContext(null);
 assert.equal(result.status,'new');assert.equal(result.performance,null);
 assert.equal(result.defaults.dailyBudget,null);assert.equal(result.defaults.maxCpc,null);
 assert.equal(buildDeliveryContext({id:'new',price:1}).status,'new');
});
test('bid plan validates monetary limits, goal and integer duration',()=>{
 const good={goal:'clicks',dailyBudget:30,days:3,maxCpc:.8};
 assert.deepEqual(validateSettings(good),good);
 for(const bad of [{days:1.5},{days:0},{dailyBudget:Infinity},{maxCpc:NaN},{maxCpc:101},{goal:'unknown'},{unitProfit:-1}])assert.throws(()=>validateSettings({...good,...bad}));
});
