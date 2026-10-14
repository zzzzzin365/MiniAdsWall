const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const filename=path.resolve(__dirname,'../../client/src/utils/performance.ts');
const mod=new Module(filename);mod.filename=filename;mod.paths=Module._nodeModulePaths(path.dirname(filename));
mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,esModuleInterop:true}}).outputText,filename);
const {demo,days,buildSample,sum,performanceForAd,materialPerformance,deliveryStatusFor}=mod.exports;
const ads=demo.ads.map(a=>({...a,price:a.max_cpc_minor/100,videos:[],version:2}));
test('merchant ledger enforces account and ad budgets, click limits and unique order attribution',()=>{
 assert.equal(ads.length,20);assert.equal(new Set(ads.map(a=>a.material_id)).size,4);
 for(const day of days){let spending=0,active=0;for(const ad of demo.ads){const row=ad.daily.find(r=>r.day===day);if(!row)continue;active++;const spend=row.click_costs_minor.reduce((a,b)=>a+b,0);spending+=spend;assert(spend<=ad.daily_budget_minor);assert(row.impressions>=row.click_costs_minor.length);assert(row.click_costs_minor.every(v=>Number.isInteger(v)&&v>0&&v<=ad.max_cpc_minor));}assert.equal(active,4);assert(spending<=demo.merchant_daily_budget_minor);}
 assert.equal(new Set(demo.orders.map(o=>o.id)).size,demo.orders.length);
 const clicks=new Set();for(const o of demo.orders){const ad=demo.ads.find(a=>a.id===o.ad_id);assert.equal(o.material_id,ad.material_id);assert.equal(o.amount_minor,ad.unit_price_minor);assert(o.click_index<ad.daily.find(r=>r.day===o.paid_at).click_costs_minor.length);const click=o.ad_id+o.paid_at+o.click_index;assert(!clicks.has(click));clicks.add(click);}
 for(const o of demo.orders){const refunds=demo.refunds.filter(r=>r.order_id===o.id);assert(refunds.reduce((n,r)=>n+r.amount_minor,0)<=o.amount_minor);assert(refunds.every(r=>r.refunded_at>=o.paid_at));}
});
test('all windows, filtered lists and material totals reconcile to the same ledger',()=>{
 for(const range of ['7','3']){const selected=range==='7'?days:days.slice(-3);const rows=buildSample(ads).rows.filter(r=>selected.includes(r.day));const expected=sum(rows);assert.deepEqual(sum(ads.map(a=>performanceForAd(a,range))),expected);assert.deepEqual(materialPerformance([...ads,...ads].map(a=>({id:a.id,video_count:0})),range),expected);for(const material of new Set(ads.map(a=>a.material_id))){const subset=ads.filter(a=>a.material_id===material);assert.deepEqual(materialPerformance(subset.map(a=>({id:a.id,video_count:0})),range),sum(subset.map(a=>performanceForAd(a,range))));}assert.equal(expected.revenue,expected.grossRevenue-expected.refund);}
 const total=sum(ads.map(a=>performanceForAd(a)));assert.equal(total.spend,58782);assert.equal(total.clicks,1043);assert.equal(total.orders,18);assert.equal(total.refunds,1);assert.equal(total.revenue,114300);
});
test('history is fixed across edits, refreshes and ordering; new ads have no invented performance',()=>{
 assert.equal(deliveryStatusFor('new-ad'),'未投放');
 for(const ad of demo.ads)assert.equal(deliveryStatusFor(ad.id),ad.status.startsWith('暂停')?'已投放 · 已暂停':'已投放');
 for(const a of ads)assert.deepEqual(performanceForAd({...a,price:99,clicks:9999,version:99}),performanceForAd(a));
 assert.deepEqual(sum(ads.map(a=>performanceForAd(a))),sum(ads.slice().reverse().map(a=>performanceForAd(a))));
 assert.deepEqual(performanceForAd({id:'new-ad'}),{impressions:0,clicks:0,orders:0,spend:0,revenue:0,grossRevenue:0,refunds:0,refund:0});
 const all=ads.map(a=>performanceForAd(a));assert(all.some(a=>a.spend>0&&a.orders===0));assert(all.some(a=>a.revenue>a.spend));assert(all.some(a=>a.revenue<a.spend));
});
