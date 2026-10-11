const {test}=require('node:test');
const assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const {spawn}=require('node:child_process');
const path=require('node:path');
const {fixture}=require('./helpers/marketing.cjs');
const {ingest,writeEvent}=require('../dist/services/marketing/ingestion');
const {coverageDigest}=require('../dist/services/marketing/spend.service');
const {calculateMetrics,decimalRatio,estimateMetrics}=require('../dist/services/marketing/metrics');
const {intervalCovered,parseReportQuery}=require('../dist/services/marketing/report.service');
const {marketingMigrationStatus,migrateMarketingSchema,requireMarketingSchema}=require('../dist/services/marketing/migrations');
const root=path.resolve(__dirname,'../../../..');

async function worker(f){
    const child=spawn(process.env.MARKETING_TEST_PYTHON||path.join(root,'.venv/bin/python'),['-m','marketing.aggregation.worker','--environment','synthetic','--drain'],{cwd:root,env:{...process.env,MARKETING_WORKER_MYSQL_URL:f.db.env.ADS_MYSQL_URL,MARKETING_WORKER_MYSQL_SOCKET:f.db.env.ADS_MYSQL_SOCKET},stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',d=>output+=d);child.stderr.on('data',d=>output+=d);
    const result=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>child.kill('SIGKILL'),30000);child.once('error',reject);child.once('exit',code=>{clearTimeout(timer);resolve(code)})});
    assert.equal(result,0,output);assert.doesNotMatch(output,/"status": "(?:failed|retry)"/,output);
}
async function fact(f,type,at,options={}){
    const id=randomUUID();return ingest('report_fixture',id,{id,type},async c=>{
        let impression;
        if(type==='click'||type==='impression'){
            impression=randomUUID();
            await c.execute(`INSERT INTO marketing_serving_decisions(impression_id,brand_id,environment,campaign_id,creative_id,creative_version,visitor_session_id,candidate_ref,policy_version,token_expires_at) VALUES (?,'brand_a','synthetic',?,?,1,?,'fixture','fixture',?)`,[impression,f.campaign,f.creative,options.session||id,new Date(Date.now()+60000)]);
        }
        const result=await writeEvent(c,{source:'report_fixture',source_event_id:id,brand_id:'brand_a',environment:'synthetic',type,occurred_at:at.toISOString(),impression_id:impression,click_id:type==='click'?id:undefined,order_id:options.order,product_id:options.order?'product_a':undefined,amount_minor:options.order?options.amount:undefined,payload:{visitor_session_id:options.session||id},validation_status:options.status||'valid'});
        return {status:201,body:result};
    });
}
function query(f,start,end,extra={}){return{brand_id:'brand_a',campaign_id:f.campaign,environment:'synthetic',start:start.toISOString(),end:end.toISOString(),timezone:'Asia/Shanghai',rule_version:'last-click-v1',...extra}}
async function report(f,q,base=f.a,token='test-operator'){return f.call(base,'GET','/reports?'+new URLSearchParams(q),undefined,{token})}
async function cover(f,start,end){
    for(const [name,token]of[['shop',f.sourceToken],['agency',f.secondToken]]){
        const [entries]=await f.db.connection.query('SELECT * FROM marketing_spend_ledger WHERE source=? AND campaign_id=? AND occurred_at>=? AND occurred_at<?',[name+'_synthetic',f.campaign,start,end]);
        const d=coverageDigest(entries);const r=await f.call(f.a,'POST','/spend-coverage',{campaign_id:f.campaign,start:start.toISOString(),end:end.toISOString(),source_marker_id:randomUUID(),entry_count:d.entry_count,amount_minor:Number(d.amount_minor),checksum:d.checksum},{token});assert.equal(r.status,201,JSON.stringify(r));
    }
}

test('business metric formulas: exact minor units, null boundaries, >100%, missing spend and unavailable forecasts',()=>{
    const t={impressions:100,clicks:10,paid_orders:2,cohort_orders:2,refunded_orders:1,revenue_minor:5000,spend_minor:1000};
    const m=calculateMetrics(t,{current:true,spendComplete:true,observationClosed:true});
    assert.deepEqual(Object.fromEntries(Object.entries(m).map(([k,v])=>[k,v.value])),{ctr:'0.100000',cvr:'0.200000',cpa_minor:'500.000000',cpc_minor:'100.000000',cpm_minor:'10000.000000',roas:'5.000000',refund_rate:'0.500000',realized_revenue_per_mille_minor:'50000.000000',ecpm_minor:null,ltv_minor:null});
    assert.equal(decimalRatio(9007199254740991,1,1000),'9007199254740991000.000000');
    assert.equal(decimalRatio(1,6),'0.166667');
    assert.throws(()=>decimalRatio(9007199254740992,1),/精确/);
    const missing=calculateMetrics(t,{current:true,spendComplete:false,observationClosed:false});assert.equal(missing.cpa_minor.value,null);assert.equal(missing.roas.value,null);assert.equal(missing.ctr.value,'0.100000');
    const more=calculateMetrics({...t,impressions:1,cohort_orders:20},{current:true,spendComplete:true,observationClosed:false});assert.equal(more.ctr.value,'10.000000');assert.equal(more.cvr.value,'2.000000');
    const empty=calculateMetrics(Object.fromEntries(Object.keys(t).map(k=>[k,0])),{current:true,spendComplete:true,observationClosed:true});assert.ok(Object.values(empty).every(m=>m.value===null));
});

test('report query and interval reconciliation reject ambiguity, gaps and invalidated overlapping markers',()=>{
    const q=query({campaign:'campaign'},new Date('2026-01-01Z'),new Date('2026-01-02Z'));
    assert.equal(parseReportQuery({...q,creative_id:'creative',creative_version:'1'},'synthetic').creative_version,1);
    for(const value of [{...q,start:q.end},{...q,end:'2026-03-01T00:00:00.000Z'},{...q,creative_id:'creative'},{...q,creative_version:'1'},{...q,timezone:'wrong'},{...q,campaign_id:['one','two']},{...q,environment:'production'}])assert.throws(()=>parseReportQuery(value,'synthetic'));
    const markers=[{starts_at:new Date(0),ends_at:new Date(1000),status:'complete'},{starts_at:new Date(1000),ends_at:new Date(2000),status:'complete'}];
    assert.equal(intervalCovered(100,1900,markers),true);assert.equal(intervalCovered(100,2100,markers),false);
    assert.equal(intervalCovered(100,1900,[...markers,{starts_at:new Date(500),ends_at:new Date(600),status:'invalidated'}]),false);
});

test('forecast metrics distinguish CPC/CPA assumptions and revenue LTV from observed results',()=>{
    const e=estimateMetrics({ecpm:{predicted_ctr:0.1,predicted_cvr:0.2,bid_minor:1000,bid_basis:'CPA'},ltv:{average_order_net_revenue_minor:5000,orders_per_month:2,lifetime_months:12}});
    assert.equal(e.metrics.ecpm_minor.value,'20000.000000');assert.equal(e.metrics.ltv_minor.value,'120000.000000');assert.equal(e.input_source,'user_supplied_assumptions');assert.equal(e.metrics.ecpm_minor.status,'estimate');
    assert.equal(estimateMetrics({ecpm:{predicted_ctr:0.1,bid_minor:1000,bid_basis:'CPC'}}).metrics.ecpm_minor.value,'100000.000000');
    assert.equal(estimateMetrics({ecpm:{predicted_ctr:1e-7,bid_minor:1000000,bid_basis:'CPC'}}).metrics.ecpm_minor.value,'100.000000');
});

test('reports: real MySQL+HTTP+Python metrics, full source coverage, lag, immutable refund snapshots and auth',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        const at=new Date(Date.now()-2*86400000),start=new Date(at.getTime()-1000),end=new Date(at.getTime()+3600000);
        for(let i=0;i<100;i++)await fact(f,'impression',at);
        for(let i=0;i<10;i++)await fact(f,'click',at,{session:'session_'+i});
        for(let i=0;i<2;i++)await fact(f,'order_paid',new Date(at.getTime()+1000),{order:'order_'+i,session:'session_'+i,amount:2500});
        const spend={source_entry_id:randomUUID(),campaign_id:f.campaign,creative_id:f.creative,creative_version:1,occurred_at:at.toISOString(),amount_minor:1000,currency:'CNY'};
        assert.equal((await f.call(f.a,'POST','/spend-entries',spend,{token:f.sourceToken})).status,201);
        const q=query(f,start,end);
        const estimates=await f.call(f.a,'POST','/metrics/estimates',{ecpm:{predicted_ctr:0.1,predicted_cvr:0.2,bid_minor:1000,bid_basis:'CPA'},ltv:{average_order_net_revenue_minor:5000,orders_per_month:2,lifetime_months:12}},{token:'test-operator'});
        assert.equal(estimates.status,200,JSON.stringify(estimates));assert.equal(estimates.body.metrics.ecpm_minor.value,'20000.000000');
        assert.equal((await f.call(f.a,'POST','/metrics/estimates',{ecpm:{predicted_ctr:0.1,bid_minor:1000,bid_basis:'CPA'}},{token:'test-operator'})).status,400);
        assert.equal((await f.call(f.a,'POST','/metrics/estimates',{ltv:{average_order_net_revenue_minor:1,orders_per_month:2,lifetime_months:12}})).status,401);
        const lag=await report(f,q);assert.equal(lag.status,200,JSON.stringify(lag));assert.equal(lag.body.status,'stale');assert.equal(lag.body.metrics.cvr.value,null);assert.equal(lag.body.totals.paid_orders,null);
        await worker(f);
        const partial=await report(f,q);assert.equal(partial.body.metrics.ctr.value,'0.100000');assert.equal(partial.body.metrics.cvr.value,'0.200000');assert.equal(partial.body.metrics.cpa_minor.value,null);assert.deepEqual(partial.body.coverage.missing_sources,['agency','shop']);
        await cover(f,start,end);await worker(f);
        const full=await report(f,q,f.b);assert.equal(full.status,200,JSON.stringify(full));assert.equal(full.body.coverage.status,'complete');assert.equal(full.body.metrics.cpa_minor.value,'500.000000');assert.equal(full.body.metrics.cpc_minor.value,'100.000000');assert.equal(full.body.metrics.cpm_minor.value,'10000.000000');assert.equal(full.body.metrics.roas.value,'5.000000');assert.equal(full.body.metrics.cvr.status,'provisional');
        const old=full.body;
        assert.deepEqual((await f.call(f.a,'GET','/reports/'+old.report_id,undefined,{token:'test-operator'})).body,old);
        await fact(f,'order_refunded',new Date(),{order:'order_0',session:'session_0',amount:1000});await worker(f);
        const refund=await report(f,q);assert.equal(refund.body.metrics.roas.value,'4.000000');assert.equal(refund.body.metrics.refund_rate.value,'0.500000');assert.notEqual(refund.body.report_id,old.report_id);
        assert.deepEqual((await f.call(f.b,'GET','/reports/'+old.report_id,undefined,{token:'test-operator'})).body,old);
        assert.equal((await report(f,q,f.a,'')).status,401);assert.equal((await report(f,{...q,brand_id:'other'})).status,403);
        assert.equal((await report(f,{...q,environment:'production'})).status,403);assert.equal((await report(f,{...q,creative_id:'other',creative_version:1})).status,422);
        await f.db.connection.query("INSERT INTO marketing_brands(id,name,environment) VALUES ('other','其他品牌','synthetic')");
        await f.db.connection.query("INSERT INTO marketing_memberships(principal,brand_id,role) VALUES ('outsider','other','operator')");
        const outsider=await f.start({ADS_OPERATOR_TOKENS:JSON.stringify({outsider:'outsider-token'})});assert.equal((await f.call(outsider,'GET','/reports/'+old.report_id,undefined,{token:'outsider-token'})).status,403);
        // Mutating accepted spend invalidates a previously complete marker.
        assert.equal((await f.call(f.b,'POST','/spend-entries',{...spend,source_entry_id:randomUUID(),amount_minor:100},{token:f.sourceToken})).status,201);await worker(f);
        const invalid=await report(f,q);assert.equal(invalid.body.metrics.roas.value,null);assert.ok(invalid.body.coverage.missing_sources.includes('shop'));
        // Payload is persisted and verified, rather than a random unresolvable ID.
        await f.db.connection.query("UPDATE marketing_report_payloads SET payload=JSON_SET(payload,'$.currency','USD') WHERE report_id=?",[old.report_id]);
        assert.equal((await f.call(f.a,'GET','/reports/'+old.report_id,undefined,{token:'test-operator'})).status,503);
        assert.equal((await marketingMigrationStatus()).version,13);assert.equal((await migrateMarketingSchema()).changed,false);
    }finally{await f.stop()}
});

test('reports: exact half-open windows, click cohorts crossing pay days, zero samples and late correction',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        const at=new Date(Date.now()-3*86400000),start=new Date(at.getTime()-1000),end=new Date(at.getTime()+1000);
        await fact(f,'click',at,{session:'cohort'});await fact(f,'click',end,{session:'boundary'});
        await fact(f,'order_paid',new Date(at.getTime()+86400000),{session:'cohort',order:'cross_period',amount:2000});
        await fact(f,'order_paid',new Date(at.getTime()+86400001),{session:'cohort',order:'cross_period_two',amount:1000});await worker(f);
        let r=await report(f,query(f,start,end));assert.equal(r.body.totals.clicks,1);assert.equal(r.body.totals.paid_orders,0);assert.equal(r.body.totals.cohort_orders,2);assert.equal(r.body.metrics.cvr.value,'2.000000');assert.equal(r.body.metrics.ctr.value,null);assert.ok(r.body.warnings.includes('multiple_orders_per_click'));
        const pay=await report(f,query(f,new Date(at.getTime()+86400000),new Date(at.getTime()+86400002)));assert.equal(pay.body.totals.paid_orders,2);assert.equal(pay.body.totals.clicks,0);assert.equal(pay.body.metrics.cvr.value,null);
        r=await report(f,query(f,new Date(at.getTime()-86400000),start));assert.equal(r.body.totals.impressions,0);assert.equal(r.body.metrics.ctr.value,null);assert.equal(r.body.metrics.roas.value,null);
        // A late, newer click moves both orders away from the original cohort.
        await fact(f,'click',new Date(at.getTime()+5000),{session:'cohort'});await worker(f);
        r=await report(f,query(f,start,end));assert.equal(r.body.metrics.cvr.value,'0.000000');assert.equal(r.body.totals.cohort_orders,0);
        const historicStart=new Date(Date.now()-50*86400000),historicEnd=new Date(Date.now()-49*86400000);
        await cover(f,historicStart,historicEnd);await worker(f);
        const closed=await report(f,query(f,historicStart,historicEnd));assert.equal(closed.body.observation.status,'closed');assert.equal(closed.body.status,'complete');assert.equal(closed.body.metrics.ctr.value,null);
    }finally{await f.stop()}
});

test('report migration 12 -> 13: missing migration, interrupted DDL and recoverable upgrade',async t=>{
    const {database}=require('./helpers/mysql.cjs');const db=await database(t);if(!db)return;
    try{
        await migrateMarketingSchema({target:12});await assert.rejects(requireMarketingSchema(13),/marketing_migration_required/);
        await assert.rejects(migrateMarketingSchema({afterStatement(){throw Error('interrupt')}}),/marketing_migration_step_failed/);
        assert.equal((await marketingMigrationStatus()).state,'failed');await assert.rejects(requireMarketingSchema(13),/marketing_migration_required/);
        await migrateMarketingSchema();assert.equal((await marketingMigrationStatus()).state,'applied');assert.equal((await migrateMarketingSchema()).changed,false);
    }finally{await db.stop()}
});
