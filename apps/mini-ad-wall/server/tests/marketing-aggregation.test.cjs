const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {randomUUID} = require('node:crypto');
const {fixture} = require('./helpers/marketing.cjs');
const {ingest, writeEvent} = require('../dist/services/marketing/ingestion');
const {marketingMigrationStatus, migrateMarketingSchema, requireMarketingSchema} = require('../dist/services/marketing/migrations');
const root = path.resolve(__dirname,'../../../..');
const python = process.env.MARKETING_TEST_PYTHON || path.join(root,'.venv/bin/python');

function child(f, args, code) {
    const p = spawn(python, code ? ['-c',code] : args, {cwd:root, env:{...process.env,
        MARKETING_WORKER_MYSQL_URL:f.db.env.ADS_MYSQL_URL, MARKETING_WORKER_MYSQL_SOCKET:f.db.env.ADS_MYSQL_SOCKET,
        MARKETING_EVENTS_ENABLED:'false'}, stdio:['pipe','pipe','pipe']});
    let output='', errors='';
    p.stdout.on('data',d=>output+=d); p.stderr.on('data',d=>errors+=d);
    const timer=setTimeout(()=>p.kill('SIGKILL'),20000);
    const done=new Promise((resolve,reject)=>{p.once('error',reject);p.once('exit',code=>{clearTimeout(timer);resolve({code,output,errors})})});
    return {p,done,ready:()=>new Promise((resolve,reject)=>{
        if(output.includes('READY'))return resolve();
        const timeout=setTimeout(()=>reject(Error('worker pause timeout '+errors)),10000);
        p.stdout.on('data',()=>{if(output.includes('READY')){clearTimeout(timeout);resolve()}});
    })};
}
async function worker(f, args=['--drain']) {
    const r=await child(f,['-m','marketing.aggregation.worker','--environment','synthetic',...args]).done;
    assert.equal(r.code,0,r.errors+' '+r.output);
    return r.output.trim().split('\n').map(s=>JSON.parse(s));
}
function paused(f) {
    return child(f,null,"import sys,json\nfrom marketing.aggregation.repository import Repository\nfrom marketing.aggregation.worker import run_once\nr=Repository(lease_seconds=120)\nr.require_schema()\ndef pause():\n print('READY',flush=True)\n sys.stdin.readline()\nprint(json.dumps(run_once(r,'synthetic',hooks={'before_publish':pause})),flush=True)");
}
async function metrics(f) {
    const [rows]=await f.db.connection.query(`SELECT m.day,m.campaign_id,m.creative_id,m.creative_version,m.impressions,m.clicks,m.paid_orders,m.refunded_orders,m.revenue_minor,m.spend_minor FROM marketing_metric_heads h JOIN marketing_daily_metrics m USING(environment,campaign_id,creative_id,creative_version,day,rule_version,revision) ORDER BY m.day,m.campaign_id,m.creative_id`);
    return rows.map(r=>Object.fromEntries(Object.entries(r).map(([k,v])=>[k,typeof v==='bigint'?Number(v):v])));
}
async function attribution(f) {
    const [rows]=await f.db.connection.query('SELECT a.* FROM marketing_order_attribution_heads h JOIN marketing_order_attributions a USING(source,order_id,environment,revision) ORDER BY a.source,a.order_id');
    return rows;
}
// Trusted imported fixtures exercise late event-time data without changing raw rows.
async function raw(f, type, at, values={}) {
    const id=randomUUID(), source=type==='click'||type==='impression'?'fixture_browser':'fixture_shop';
    return ingest(source,id,{type,id},async c=>{
        let impression;
        if(type==='click'||type==='impression'){
            impression=randomUUID();
            await c.execute(`INSERT INTO marketing_serving_decisions(impression_id,brand_id,environment,campaign_id,creative_id,creative_version,visitor_session_id,candidate_ref,policy_version,token_expires_at) VALUES (?,'brand_a','synthetic',?,?,1,?,'fixture','fixture',?)`,[impression,f.campaign,values.creative||f.creative,values.session||'session',new Date(Date.now()+60000)]);
        }
        const receipt=await writeEvent(c,{source,source_event_id:id,brand_id:'brand_a',environment:'synthetic',type,occurred_at:at.toISOString(),
            impression_id:impression,click_id:type==='click'?(values.click||id):undefined,order_id:values.order||'imported_order',product_id:'product_a',amount_minor:values.amount??1000,
            payload:{visitor_session_id:values.session||'session'},validation_status:values.status||'valid'});
        return {status:201,body:receipt};
    });
}
async function rebuild(f, start, end, id=randomUUID()) {
    const r=await child(f,['-m','marketing.aggregation.rebuild','--brand','brand_a','--environment','synthetic','--rule','last-click-v1','--start',start,'--end',end,'--actor','test-operator','--request-id',id]).done;
    assert.equal(r.code,0,r.errors+' '+r.output);
    return JSON.parse(r.output).job_id;
}

test('CP04: real HTTP facts, multiple workers, exact refunds/corrections, replay and indexed click cohorts',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        const display=await f.call(f.a,'POST','/serve',{campaign_id:f.campaign});
        assert.equal((await f.call(f.a,'POST','/events/impressions',{event_id:randomUUID(),impression_token:display.body.impression_token,occurred_at:new Date().toISOString()},{cookie:display.cookie})).status,201);
        const clicked=await f.call(f.a,'GET','/click/'+display.body.impression_token,undefined,{cookie:display.cookie});
        const token=new URL(clicked.location).searchParams.get('miniads_click');
        const paid={source_event_id:randomUUID(),type:'order_paid',order_id:'http_order',brand_id:'brand_a',product_id:'product_a',occurred_at:new Date(Date.now()+100).toISOString(),amount_minor:1000,currency:'CNY',click_token:token};
        assert.equal((await f.call(f.a,'POST','/conversions',paid,{token:f.sourceToken})).status,201);
        const spend={source_entry_id:randomUUID(),campaign_id:f.campaign,creative_id:f.creative,creative_version:1,occurred_at:new Date().toISOString(),amount_minor:1000,currency:'CNY'};
        const fee=await f.call(f.a,'POST','/spend-entries',spend,{token:f.sourceToken});assert.equal(fee.status,201);
        const outputs=await Promise.all([worker(f),worker(f)]);
        assert.ok(outputs.flat().some(r=>r.status==='done'),JSON.stringify(outputs));
        let a=await attribution(f);assert.equal(a.length,1);assert.equal(a[0].status,'attributed');assert.equal(Number(a[0].net_revenue_minor),1000);
        assert.equal((await f.call(f.b,'POST','/conversions',{...paid,source_event_id:randomUUID(),type:'order_refunded',amount_minor:300},{token:f.sourceToken})).status,201);
        assert.equal((await f.call(f.b,'POST','/spend-entries',{...spend,source_entry_id:randomUUID(),amount_minor:-200,adjustment_ref:fee.body.receipt_id},{token:f.sourceToken})).status,201);
        const done=await worker(f);assert.ok(done.some(r=>r.status==='done'),JSON.stringify(done));
        const m=await metrics(f);assert.equal(m.length,1);assert.deepEqual(['impressions','clicks','paid_orders','refunded_orders','revenue_minor','spend_minor'].map(k=>Number(m[0][k])),[1,1,1,1,700,800]);
        a=await attribution(f);assert.equal(a[0].revision,2);
        const [links]=await f.db.connection.query('SELECT * FROM marketing_click_orders');assert.equal(links.length,1);assert.equal(Number(links[0].net_revenue_minor),700);
        assert.deepEqual(await worker(f),[{status:'idle'}]);assert.equal((await attribution(f))[0].revision,2);
        const [epoch]=await f.db.connection.query('SELECT epoch FROM marketing_input_epochs');const [count]=await f.db.connection.query('SELECT COUNT(*) AS n FROM marketing_outbox');assert.equal(Number(epoch[0].epoch),Number(count[0].n));
        const status=(await worker(f,['--status']))[0];assert.equal(status.queue.find(r=>r.status==='done').count,6);assert.equal(status.pending_partitions,0);
    }finally{await f.stop()}
});

test('CP04: late click moves attribution, Shanghai pay-day differs, old refunds defer and explicit rebuild reconciles',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        const at=new Date(Date.now()-3*86400000);
        await raw(f,'click',at,{click:'click_a'});
        await raw(f,'order_paid',new Date(at.getTime()+86400000));
        await worker(f);
        assert.equal((await attribution(f))[0].click_id,'click_a');
        await raw(f,'click',new Date(at.getTime()+1000),{click:'click_z'});
        await raw(f,'order_refunded',new Date(),{amount:200});
        const out=await worker(f);assert.ok(out.some(r=>r.status==='done'),JSON.stringify(out));
        const a=(await attribution(f))[0];assert.equal(a.click_id,'click_z');assert.equal(a.revision,2);assert.equal(Number(a.net_revenue_minor),800);
        const m=await metrics(f);assert.equal(m.length,2);assert.equal(Number(m[0].clicks),2);assert.equal(Number(m[0].paid_orders),0);assert.equal(Number(m[1].clicks),0);assert.equal(Number(m[1].paid_orders),1);
        const start=new Date(Date.now()-10*86400000).toISOString().slice(0,10),end=new Date(Date.now()+86400000).toISOString().slice(0,10);
        const job=await rebuild(f,start,end);assert.equal(await rebuild(f,start,end,job),job);
        await worker(f);assert.deepEqual(await metrics(f),m);assert.equal((await attribution(f))[0].revision,2);
        const [jobs]=await f.db.connection.query('SELECT status,result FROM marketing_rebuild_jobs WHERE id=?',[job]);assert.equal(jobs[0].status,'done');const result=typeof jobs[0].result==='string'?JSON.parse(jobs[0].result):jobs[0].result;assert.equal(result.net_revenue_minor,800);
        // Explicitly accepted historical import: automatic repair must not silently
        // rewrite partitions older than 30 days.
        const old=new Date(Date.now()-40*86400000);
        await raw(f,'click',old,{session:'old',click:'old_click'});
        await raw(f,'order_paid',new Date(old.getTime()+1000),{session:'old',order:'old_order'});
        await worker(f);
        let [pending]=await f.db.connection.query('SELECT * FROM marketing_pending_partitions');assert.ok(pending.length>=1);
        const oldStart=new Date(old.getTime()-86400000).toISOString().slice(0,10),oldEnd=new Date(old.getTime()+2*86400000).toISOString().slice(0,10);
        await rebuild(f,oldStart,oldEnd);await worker(f);
        [pending]=await f.db.connection.query('SELECT * FROM marketing_pending_partitions');assert.equal(pending.length,0);
        const original=await metrics(f);
        await raw(f,'order_refunded',new Date(),{session:'old',order:'old_order',amount:400});await worker(f);
        assert.deepEqual(await metrics(f),original);
        [pending]=await f.db.connection.query('SELECT * FROM marketing_pending_partitions');assert.equal(pending.length,1);
        await rebuild(f,oldStart,oldEnd);await worker(f);
        const repaired=await metrics(f);assert.equal(Number(repaired[0].revenue_minor),600);assert.equal(Number(repaired[0].refunded_orders),1);
        [pending]=await f.db.connection.query('SELECT * FROM marketing_pending_partitions');assert.equal(pending.length,0);
        const [history]=await f.db.connection.query('SELECT COUNT(*) AS n FROM marketing_daily_metrics');assert.ok(Number(history[0].n)>repaired.length);
    }finally{await f.stop()}
});

test('CP04: crash before/after commit, expired epoch rejection and concurrent-input rejection',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        await raw(f,'click',new Date());
        const crash=async point=>{const code=`import os\nfrom marketing.aggregation.repository import Repository\nfrom marketing.aggregation.worker import run_once\nr=Repository()\nr.require_schema()\nrun_once(r,'synthetic',hooks={'${point}':lambda:os._exit(86)})`;const r=await child(f,null,code).done;assert.equal(r.code,86,r.errors+' '+r.output)};
        await crash('before_commit');assert.deepEqual(await metrics(f),[]);
        let [running]=await f.db.connection.query("SELECT status,lease_epoch FROM marketing_outbox");assert.equal(running[0].status,'running');
        await f.db.connection.query("UPDATE marketing_outbox SET lease_until=UTC_TIMESTAMP(3)-INTERVAL 1 SECOND WHERE status='running'");
        await crash('after_commit');let m=await metrics(f);assert.equal(Number(m[0].clicks),1);
        assert.deepEqual(await worker(f),[{status:'idle'}]);assert.deepEqual(await metrics(f),m);
        await raw(f,'click',new Date(),{session:'second'});
        const slow=paused(f);await slow.ready();
        await f.db.connection.query("UPDATE marketing_outbox SET lease_until=UTC_TIMESTAMP(3)-INTERVAL 1 SECOND WHERE status='running'");
        // Expired claim cannot publish even if the input snapshot has not changed.
        slow.p.stdin.write('\n');const stale=await slow.done;assert.equal(stale.code,0,stale.errors);assert.match(stale.output,/lease_lost/);assert.deepEqual(await metrics(f),m);
        await worker(f);m=await metrics(f);assert.equal(Number(m[0].clicks),2);
        await raw(f,'click',new Date(),{session:'third'});
        const beforeInput=paused(f);await beforeInput.ready();
        await raw(f,'click',new Date(),{session:'fourth'});
        beforeInput.p.stdin.write('\n');const obsolete=await beforeInput.done;assert.match(obsolete.output,/inputs_changed/);assert.deepEqual(await metrics(f),m);
        await f.db.connection.query("UPDATE marketing_outbox SET next_at=UTC_TIMESTAMP(3) WHERE status='pending'");
        await worker(f);m=await metrics(f);assert.equal(Number(m[0].clicks),4);
        const [facts]=await f.db.connection.query('SELECT COUNT(*) AS n FROM marketing_events');assert.equal(Number(facts[0].n),4);
        // Reclaimed task's epoch increases, no second count is appended.
        [running]=await f.db.connection.query('SELECT MAX(lease_epoch) AS n FROM marketing_outbox');assert.ok(Number(running[0].n)>=2);
        await raw(f,'click',new Date(),{session:'deadline'});
        const deadline=paused(f);await deadline.ready();
        await f.db.connection.query("UPDATE marketing_outbox SET created_at=UTC_TIMESTAMP(3)-INTERVAL 25 HOUR WHERE status='running'");
        deadline.p.stdin.write('\n');const overdue=await deadline.done;assert.match(overdue.output,/lease_lost/);assert.deepEqual(await metrics(f),m);
        const [failed]=await f.db.connection.query("SELECT status FROM marketing_outbox WHERE created_at<UTC_TIMESTAMP(3)-INTERVAL 24 HOUR");assert.equal(failed[0].status,'failed');
    }finally{await f.stop()}
});

test('CP04: bounded retries, unknown tasks, schema upgrade and least-privilege worker process',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        await f.db.connection.query("SET time_zone='+00:00'");
        await raw(f,'order_paid',new Date(),{status:'quarantined'});
        await f.db.connection.query("INSERT INTO marketing_outbox(kind,aggregate_id,brand_id,environment,payload) VALUES ('unknown','bad_kind','brand_a','synthetic','{}'),('marketing.event','never_seen','brand_a','synthetic','{}')");
        await worker(f);
        const status=(await worker(f,['--status']))[0];assert.equal(status.validation.find(x=>x.status==='quarantined').count,1);assert.ok(status.failures.length>=1);
        await f.db.connection.query("INSERT INTO marketing_outbox(kind,aggregate_id,brand_id,environment,payload,attempt) VALUES ('marketing.coverage','exhausted','brand_a','synthetic','{}',5)");
        await worker(f);const [exhausted]=await f.db.connection.query("SELECT status,error_code FROM marketing_outbox WHERE aggregate_id='exhausted'");assert.equal(exhausted[0].status,'failed');assert.equal(exhausted[0].error_code,'retry_budget_exhausted');
        const [legacy]=await f.db.connection.query('SELECT id,price,clicks,version FROM ads_business_ads ORDER BY id');
        await f.db.connection.query("CREATE USER 'marketing_worker'@'localhost' IDENTIFIED BY 'disposable-worker-test-password'");
        const reads=['marketing_schema_migrations','marketing_brands','marketing_campaigns','marketing_serving_decisions','marketing_events','marketing_event_validation','marketing_spend_ledger','marketing_spend_isolation'];
        for(const table of reads)await f.db.connection.query(`GRANT SELECT ON ads_verify.${table} TO 'marketing_worker'@'localhost'`);
        const fs=require('node:fs');
        const grants=fs.readFileSync(path.join(__dirname,'../scripts/marketing-worker-grants.sql'),'utf8').replaceAll('miniadswall.','ads_verify.');
        for(const sql of grants.split('\n').filter(line=>line.startsWith('GRANT ')))await f.db.connection.query(sql);
        await f.db.connection.query("GRANT SELECT,UPDATE ON ads_verify.marketing_outbox TO 'marketing_worker'@'localhost'");
        await raw(f,'click',new Date());
        const limited={...f,db:{...f.db,env:{...f.db.env,ADS_MYSQL_URL:'mysql://marketing_worker:disposable-worker-test-password@localhost/ads_verify'}}};
        const ok=await worker(limited);assert.ok(ok.some(x=>x.status==='done'),JSON.stringify(ok));
        const denied=await child(limited,null,"from marketing.aggregation.repository import Repository\nr=Repository()\nwith r.connection() as c, c.cursor() as q:\n q.execute('UPDATE ads_business_ads SET clicks=clicks+1')").done;assert.notEqual(denied.code,0);assert.match(denied.errors,/denied/);
        const [after]=await f.db.connection.query('SELECT id,price,clicks,version FROM ads_business_ads ORDER BY id');assert.deepEqual(after,legacy);
        // No ready work remains; enqueue one more valid fact and use a tiny bound.
        await raw(f,'click',new Date(),{session:'row_limit'});
        const bounded=await child(f,null,"import json\nfrom marketing.aggregation.repository import Repository\nfrom marketing.aggregation.worker import run_once\nprint(json.dumps(run_once(Repository(max_rows=1),'synthetic')))").done;
        assert.match(bounded.output,/snapshot_row_limit_exceeded/);
        // Admission stops atomically at the per-brand cap, including failed work.
        await f.db.connection.query("INSERT INTO marketing_outbox(kind,aggregate_id,brand_id,environment,payload,status) WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<1000) SELECT 'marketing.coverage',CONCAT('backlog-',n),'brand_a','synthetic','{}','failed' FROM seq");
        for(let i=1;i<10;i++)await f.db.connection.query("INSERT INTO marketing_outbox(kind,aggregate_id,brand_id,environment,payload,status) SELECT kind,CONCAT(aggregate_id,?),brand_id,environment,payload,status FROM marketing_outbox WHERE aggregate_id REGEXP '^backlog-[0-9]+$'",['-'+i]);
        const before=(await f.db.connection.query('SELECT COUNT(*) AS n FROM marketing_events'))[0][0].n;
        const deniedInput={source_event_id:randomUUID(),type:'order_paid',order_id:'blocked',brand_id:'brand_a',product_id:'product_a',occurred_at:new Date().toISOString(),amount_minor:1000,currency:'CNY'};
        assert.equal((await f.call(f.a,'POST','/conversions',deniedInput,{token:f.sourceToken})).status,503);
        const afterCount=(await f.db.connection.query('SELECT COUNT(*) AS n FROM marketing_events'))[0][0].n;assert.equal(afterCount,before);
        assert.equal((await marketingMigrationStatus()).version,13);assert.equal((await migrateMarketingSchema()).changed,false);
    }finally{await f.stop()}
});

test('CP04: bounded claims publish the same inputs once when two independent workers compete',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        for(let i=0;i<26;i++)await raw(f,'click',new Date(),{session:'batch-'+i});
        const first=paused(f);await first.ready();
        const [claims]=await f.db.connection.query("SELECT COUNT(*) AS n FROM marketing_outbox WHERE status='running'");assert.equal(Number(claims[0].n),25);
        const second=await worker(f);assert.ok(second.some(r=>r.status==='done'),JSON.stringify(second));
        first.p.stdin.write('\n');const loser=await first.done;assert.match(loser.output,/publication_changed/);
        assert.equal(Number((await metrics(f))[0].clicks),26);
        await f.db.connection.query("UPDATE marketing_outbox SET next_at=UTC_TIMESTAMP(3) WHERE status='pending'");await worker(f);
        assert.equal(Number((await metrics(f))[0].clicks),26);
        const [history]=await f.db.connection.query('SELECT COUNT(*) AS n FROM marketing_daily_metrics');assert.equal(Number(history[0].n),1);
        const [done]=await f.db.connection.query("SELECT COUNT(*) AS n FROM marketing_outbox WHERE status='done'");assert.equal(Number(done[0].n),26);
    }finally{await f.stop()}
});

test('CP04 E07: paid/refund order, latest validation and full rebuild converge to identical economic results',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        const display=await f.call(f.a,'POST','/serve',{campaign_id:f.campaign});
        const click=await f.call(f.a,'GET','/click/'+display.body.impression_token,undefined,{cookie:display.cookie});
        const token=new URL(click.location).searchParams.get('miniads_click');
        const input={source_event_id:randomUUID(),type:'order_paid',brand_id:'brand_a',product_id:'product_a',occurred_at:new Date(Date.now()+100).toISOString(),amount_minor:1000,currency:'CNY',click_token:token};
        const send=async(body,status)=>{const result=await f.call(f.a,'POST','/conversions',body,{token:f.sourceToken});assert.equal(result.status,status);return result.body};
        await send({...input,order_id:'ordered'},201);
        await worker(f);
        await send({...input,source_event_id:randomUUID(),order_id:'ordered',type:'order_refunded',amount_minor:200},201);
        const pending=await send({...input,source_event_id:randomUUID(),order_id:'unordered',type:'order_refunded',amount_minor:200},202);
        await worker(f);assert.equal((await attribution(f)).find(x=>x.order_id==='unordered').status,'pending');
        await send({...input,source_event_id:randomUUID(),order_id:'unordered'},201);
        await worker(f);
        const a=await attribution(f);assert.equal(a.length,2);assert.ok(a.every(x=>x.status==='attributed'&&Number(x.net_revenue_minor)===800));assert.equal(a[0].click_id,a[1].click_id);
        const [rawRefund]=await f.db.connection.query('SELECT validation_status FROM marketing_events WHERE id=?',[pending.receipt_id]);assert.equal(rawRefund[0].validation_status,'pending');
        const m=await metrics(f);assert.equal(Number(m[0].paid_orders),2);assert.equal(Number(m[0].revenue_minor),1600);assert.equal(Number(m[0].refunded_orders),2);assert.equal(Number(m[0].clicks),1);
        const start=new Date(Date.now()-86400000).toISOString().slice(0,10),end=new Date(Date.now()+2*86400000).toISOString().slice(0,10);
        await rebuild(f,start,end);await worker(f);assert.deepEqual(await metrics(f),m);
        const [links]=await f.db.connection.query('SELECT COUNT(*) AS n,SUM(net_revenue_minor) AS total FROM marketing_click_orders');assert.equal(Number(links[0].n),2);assert.equal(Number(links[0].total),1600);
    }finally{await f.stop()}
});

test('CP04: late attribution across creatives replaces both partitions without doubling orders',async t=>{
    const f=await fixture(t);if(!f)return;
    try{
        const alternate=f.creative+'_alt';
        await f.db.connection.query(`INSERT INTO marketing_creatives(id,version,brand_id,environment,campaign_id,ad_id,title,asset_ref,asset_hash,landing_url,status,state_revision,authorization_ref,authorization_expires_at,reviewer,reviewed_at,created_by) SELECT ?,version,brand_id,environment,campaign_id,ad_id,title,asset_ref,asset_hash,landing_url,status,state_revision,authorization_ref,authorization_expires_at,reviewer,reviewed_at,created_by FROM marketing_creatives WHERE id=?`,[alternate,f.creative]);
        const at=new Date(Date.now()-10000);
        await raw(f,'order_paid',new Date(at.getTime()+5000));await worker(f);
        assert.equal((await attribution(f))[0].status,'unattributed');assert.deepEqual(await metrics(f),[]);
        await raw(f,'click',at,{click:'first'});await worker(f);
        await raw(f,'click',new Date(at.getTime()+1000),{click:'later',creative:alternate});await worker(f);
        const m=await metrics(f);assert.equal(m.length,2);assert.equal(m.reduce((sum,r)=>sum+Number(r.paid_orders),0),1);assert.equal(m.reduce((sum,r)=>sum+Number(r.revenue_minor),0),1000);
        assert.equal(Number(m.find(r=>r.creative_id===f.creative).paid_orders),0);assert.equal(Number(m.find(r=>r.creative_id===alternate).paid_orders),1);
        const [links]=await f.db.connection.query('SELECT creative_id FROM marketing_click_orders');assert.equal(links.length,1);assert.equal(links[0].creative_id,alternate);
    }finally{await f.stop()}
});

test('CP04 migration 11 -> 12: failed DDL resumes, concurrent upgrade and schema/checksum drift rejection',async t=>{
    const {database}=require('./helpers/mysql.cjs');const db=await database(t);if(!db)return;
    const fs=require('node:fs');
    try{
        await migrateMarketingSchema({target:11});
        await assert.rejects(requireMarketingSchema(12),/marketing_migration_required/);
        await assert.rejects(migrateMarketingSchema({target:12,afterStatement(i){if(i===1)throw Error('interrupt')}}),/marketing_migration_step_failed/);
        assert.equal((await marketingMigrationStatus({target:12})).state,'failed');
        const blocked=await child({db},['-m','marketing.aggregation.worker','--environment','synthetic','--once']).done;assert.equal(blocked.code,1);assert.match(blocked.output,/marketing_migration_required/);
        await Promise.all([migrateMarketingSchema({target:12}),migrateMarketingSchema({target:12})]);assert.equal((await marketingMigrationStatus({target:12})).state,'applied');
        const directory=path.join(db.root,'drift');fs.mkdirSync(path.join(directory,'legacy'),{recursive:true});
        for(const file of ['legacy/003_marketing_events.sql','010_marketing_events.sql','011_marketing_ingestion.sql','012_marketing_aggregation.sql','013_marketing_reports.sql'])fs.copyFileSync(path.join(__dirname,'../migrations',file),path.join(directory,file));
        fs.appendFileSync(path.join(directory,'012_marketing_aggregation.sql'),'\n-- changed checksum\n');
        await assert.rejects(marketingMigrationStatus({directory}),/checksum_mismatch/);
        await db.connection.query('ALTER TABLE marketing_click_orders DROP INDEX ix_click_order_cohort');
        await assert.rejects(marketingMigrationStatus(),/schema_index_mismatch/);
    }finally{await db.stop()}
});
