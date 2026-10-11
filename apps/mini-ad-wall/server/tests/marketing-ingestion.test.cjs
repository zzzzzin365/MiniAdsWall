const { test } = require('node:test');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { fixture } = require('./helpers/marketing.cjs');
const { coverageDigest } = require('../dist/services/marketing/spend.service');
const { signToken } = require('../dist/services/marketing/security');
const { migrateMarketingSchema, marketingMigrationStatus } = require('../dist/services/marketing/migrations');

const economicReceipt = r => ({status:r.status,body:r.body});
test('CP02: committed decisions, visitor-bound signatures, two-process deduplication, expiry and legacy isolation',async t=>{
    const f=await fixture(t);if(!f)return;
    const {db,a,b,call,campaign,now,logs}=f;
    try{
        const [legacy]=await db.connection.query('SELECT id,price,clicks,version FROM ads_business_ads ORDER BY id');
        const display=await call(a,'POST','/serve',{campaign_id:campaign});assert.equal(display.status,200);
        const d=display.body,cookie=display.cookie;assert.ok(cookie);assert.equal(d.environment,'synthetic');
        const input={event_id:randomUUID(),impression_token:d.impression_token,occurred_at:new Date(now).toISOString()};
        const impressions=await Promise.all([call(a,'POST','/events/impressions',input,{cookie}),call(b,'POST','/events/impressions',input,{cookie})]);
        assert.equal(impressions[0].status,201);assert.deepEqual(economicReceipt(impressions[0]),economicReceipt(impressions[1]));
        assert.equal((await call(a,'POST','/events/impressions',{...input,occurred_at:new Date(now-1000).toISOString()},{cookie})).status,409);
        const alternate=await call(b,'POST','/events/impressions',{...input,event_id:randomUUID()},{cookie});assert.equal(alternate.body.receipt_id,impressions[0].body.receipt_id);
        const clicks=await Promise.all([call(a,'GET','/click/'+d.impression_token,undefined,{cookie}),call(b,'GET','/click/'+d.impression_token,undefined,{cookie})]);
        assert.equal(clicks[0].status,302);assert.equal(clicks[0].location,clicks[1].location);assert.match(clicks[0].location,/miniads_click=/);
        const foreign=await call(b,'POST','/serve',{campaign_id:campaign});
        assert.equal((await call(a,'POST','/events/impressions',input,{cookie:foreign.cookie})).status,403);
        assert.equal((await call(a,'GET','/click/'+d.impression_token,undefined,{cookie:foreign.cookie})).status,403);
        assert.equal((await call(a,'POST','/events/impressions',input)).status,401);
        assert.equal((await call(a,'GET','/click/'+d.impression_token.slice(0,-3)+'bad',undefined,{cookie})).status,403);
        process.env.MARKETING_SIGNING_KEY=f.signingKey;
        const claims=JSON.parse(Buffer.from(d.impression_token.split('.')[0],'base64url'));
        for(const [replacement,status]of[[{...claims,exp:Date.now()-1},410],[{...claims,environment:'sandbox'},403],[{...claims,impression_id:'fake'},403],[{...claims,kind:'click'},403]]){
            assert.equal((await call(a,'GET','/click/'+signToken(replacement),undefined,{cookie})).status,status);
        }
        const future=await call(a,'POST','/serve',{campaign_id:campaign},{cookie});
        const invalid=await call(a,'POST','/events/impressions',{event_id:randomUUID(),impression_token:future.body.impression_token,occurred_at:new Date(now+600000).toISOString()},{cookie});assert.equal(invalid.status,202);assert.equal(invalid.body.validation_status,'quarantined');
        const beforeExposure=await call(a,'GET','/click/'+future.body.impression_token,undefined,{cookie});assert.equal(beforeExposure.status,302);
        const [counts]=await db.connection.query("SELECT type,validation_status,COUNT(*) AS n FROM marketing_events GROUP BY type,validation_status");assert.equal(Number(counts.find(r=>r.type==='impression'&&r.validation_status==='valid').n),1);assert.equal(Number(counts.find(r=>r.type==='click').n),2);
        const [after]=await db.connection.query('SELECT id,price,clicks,version FROM ads_business_ads ORDER BY id');assert.deepEqual(after,legacy);
        const [atomic]=await db.connection.query("SELECT COUNT(*) AS n FROM marketing_events e LEFT JOIN marketing_outbox o ON o.aggregate_id=e.id AND o.kind='marketing.event' WHERE o.id IS NULL");assert.equal(Number(atomic[0].n),0);
        await db.connection.query('RENAME TABLE marketing_outbox TO unavailable_outbox');
        const failedDisplay=await call(a,'POST','/serve',{campaign_id:campaign},{cookie});
        const failure=await call(a,'POST','/events/impressions',{event_id:randomUUID(),impression_token:failedDisplay.body.impression_token,occurred_at:new Date().toISOString()},{cookie});assert.equal(failure.status,503);
        const [rolled]=await db.connection.query('SELECT id FROM marketing_events WHERE impression_id=?',[failedDisplay.body.impression_id]);assert.equal(rolled.length,0);
        await db.connection.query('RENAME TABLE unavailable_outbox TO marketing_outbox');
        await db.connection.query("UPDATE marketing_campaigns SET status='paused' WHERE id=?",[campaign]);assert.equal((await call(a,'POST','/serve',{campaign_id:campaign},{cookie})).status,404);
        // Already issued opportunities use frozen decisions after pausing.
        assert.equal((await call(a,'GET','/click/'+d.impression_token,undefined,{cookie})).status,302);
        assert.ok(!logs.join('').includes(d.impression_token));assert.ok(!logs.join('').includes(f.sourceToken));
        await Promise.all([migrateMarketingSchema(),migrateMarketingSchema()]);assert.equal((await marketingMigrationStatus()).state,'applied');
    }finally{await f.stop()}
});

test('CP03: scoped sources, payment identities, concurrent refunds, quarantine and atomic validation revisions',async t=>{
    const f=await fixture(t);if(!f)return;
    const {db,a,b,call,campaign,now,sourceToken}=f;
    const options={token:sourceToken};
    const base={source_event_id:randomUUID(),type:'order_paid',order_id:randomUUID(),brand_id:'brand_a',product_id:'product_a',occurred_at:new Date(now).toISOString(),amount_minor:5000,currency:'CNY'};
    try{
        for(const [options,status]of[[{},401],[{token:'test-operator'},401],[{token:f.secondToken},403],[{token:'production-source-token-long-enough'},403]]) assert.equal((await call(a,'POST','/conversions',base,options)).status,status);
        assert.equal((await call(a,'POST','/conversions',{...base,brand_id:'brand_b'},options)).status,403);
        assert.equal((await call(a,'POST','/conversions',{...base,environment:'production'},options)).status,400);
        assert.equal((await call(a,'POST','/conversions',{...base,amount_minor:1.1},options)).status,400);
        const display=await call(a,'POST','/serve',{campaign_id:campaign});
        const clicked=await call(a,'GET','/click/'+display.body.impression_token,undefined,{cookie:display.cookie});
        const click_token=new URL(clicked.location).searchParams.get('miniads_click');
        const paid={...base,click_token};
        const concurrent=await Promise.all([call(a,'POST','/conversions',paid,options),call(b,'POST','/conversions',paid,options)]);assert.equal(concurrent[0].status,201);assert.deepEqual(economicReceipt(concurrent[0]),economicReceipt(concurrent[1]));
        const alias=await call(a,'POST','/conversions',{...paid,source_event_id:randomUUID()},options);assert.equal(alias.body.receipt_id,concurrent[0].body.receipt_id);
        assert.equal((await call(a,'POST','/conversions',{...paid,source_event_id:randomUUID(),amount_minor:5001},options)).status,409);
        assert.equal((await call(a,'POST','/conversions',{...paid,source_event_id:randomUUID(),order_id:randomUUID(),product_id:'wrong'},options)).status,403);
        const refund={...base,type:'order_refunded',source_event_id:randomUUID(),amount_minor:3000};
        const refunds=await Promise.all([call(a,'POST','/conversions',refund,options),call(b,'POST','/conversions',{...refund,source_event_id:randomUUID()},options)]);assert.deepEqual(refunds.map(r=>r.status).sort(),[201,422]);
        const winning=refunds[0].status===201?refund:null;
        if(winning)assert.deepEqual(economicReceipt(await call(b,'POST','/conversions',winning,options)),economicReceipt(refunds[0]));
        const order_id=randomUUID(),early={...base,order_id,type:'order_refunded',source_event_id:randomUUID(),amount_minor:400};
        const pending=await call(a,'POST','/conversions',early,options);assert.equal(pending.status,202);assert.equal(pending.body.validation_status,'pending');
        const excess=await call(a,'POST','/conversions',{...early,source_event_id:randomUUID(),amount_minor:900,occurred_at:new Date(now+1).toISOString()},options);assert.equal(excess.status,202);
        assert.equal((await call(b,'POST','/conversions',{...base,order_id,source_event_id:randomUUID(),amount_minor:1000},options)).status,201);
        const [validation]=await db.connection.query('SELECT event_id,revision,status FROM marketing_event_validation WHERE event_id IN (?,?) ORDER BY event_id,revision',[pending.body.receipt_id,excess.body.receipt_id]);
        assert.equal(validation.length,4);assert.equal(validation.find(r=>r.event_id===pending.body.receipt_id&&r.revision===2).status,'valid');assert.equal(validation.find(r=>r.event_id===excess.body.receipt_id&&r.revision===2).status,'quarantined');
        const [raw]=await db.connection.query('SELECT validation_status FROM marketing_events WHERE id=?',[pending.body.receipt_id]);assert.equal(raw[0].validation_status,'pending');
        for(const time of [now+600000,now-31*86400000]){const result=await call(a,'POST','/conversions',{...base,order_id:randomUUID(),source_event_id:randomUUID(),occurred_at:new Date(time).toISOString()},options);assert.equal(result.status,202);assert.equal(result.body.validation_status,'quarantined')}
        const [headers]=await db.connection.query('SELECT * FROM marketing_order_ingestion WHERE order_id=?',[order_id]);assert.equal(Number(headers[0].refunded_minor),400);
        const [outbox]=await db.connection.query("SELECT COUNT(*) AS n FROM marketing_event_validation v LEFT JOIN marketing_outbox o ON o.aggregate_id=v.event_id AND o.revision=v.revision AND o.kind='marketing.event' WHERE o.id IS NULL");assert.equal(Number(outbox[0].n),0);
        await db.connection.query('RENAME TABLE marketing_outbox TO unavailable_outbox');
        const rollback={...base,source_event_id:randomUUID(),order_id:randomUUID()};assert.equal((await call(a,'POST','/conversions',rollback,options)).status,503);
        const [missing]=await db.connection.query('SELECT id FROM marketing_events WHERE source_event_id=?',[rollback.source_event_id]);assert.equal(missing.length,0);
        await db.connection.query('RENAME TABLE unavailable_outbox TO marketing_outbox');
    }finally{await f.stop()}
});

test('CP03: append-only spend, checksum coverage, missing sources, invalidation and failed reconciliation',async t=>{
    const f=await fixture(t);if(!f)return;
    const {db,a,b,call,campaign,creative,now}=f;
    const opt={token:f.sourceToken},second={token:f.secondToken};
    const entry={source_entry_id:randomUUID(),campaign_id:campaign,creative_id:creative,creative_version:1,occurred_at:new Date(now).toISOString(),amount_minor:1000,currency:'CNY'};
    const interval={campaign_id:campaign,start:new Date(now-60000).toISOString(),end:new Date(now+60000).toISOString()};
    async function marker(source='shop'){const [rows]=await db.connection.query('SELECT * FROM marketing_spend_ledger WHERE source=? AND campaign_id=? AND occurred_at>=? AND occurred_at<?',[source+'_synthetic',campaign,new Date(interval.start),new Date(interval.end)]);const d=coverageDigest(rows);return {...interval,source_marker_id:randomUUID(),...d,amount_minor:Number(d.amount_minor)}}
    try{
        const results=await Promise.all([call(a,'POST','/spend-entries',entry,opt),call(b,'POST','/spend-entries',entry,opt)]);assert.equal(results[0].status,201);assert.deepEqual(economicReceipt(results[0]),economicReceipt(results[1]));
        assert.equal((await call(a,'POST','/spend-entries',{...entry,amount_minor:1001},opt)).status,409);
        assert.equal((await call(a,'POST','/spend-entries',{...entry,source_entry_id:randomUUID(),amount_minor:-1},opt)).status,422);
        assert.equal((await call(a,'POST','/spend-entries',{...entry,source_entry_id:randomUUID(),creative_id:'wrong'},opt)).status,422);
        const firstMarker=await marker();const first=await call(a,'POST','/spend-coverage',firstMarker,opt);assert.equal(first.status,201);assert.equal(first.body.source_status,'complete');assert.equal(first.body.coverage_status_at_acceptance,'incomplete');assert.deepEqual(first.body.missing_sources,['agency']);
        assert.equal((await call(a,'POST','/spend-entries',{...entry,source_entry_id:randomUUID(),amount_minor:0},second)).status,201);
        const secondMarker=await marker('agency');const all=await call(b,'POST','/spend-coverage',secondMarker,second);assert.equal(all.body.coverage_status_at_acceptance,'complete');
        const correction={...entry,source_entry_id:randomUUID(),amount_minor:-200,adjustment_ref:results[0].body.receipt_id};assert.equal((await call(b,'POST','/spend-entries',correction,opt)).status,201);
        assert.equal((await call(a,'POST','/spend-entries',{...correction,source_entry_id:randomUUID(),occurred_at:new Date(now+1000).toISOString()},opt)).status,422);
        const [invalidated]=await db.connection.query("SELECT status FROM marketing_source_coverage WHERE source='shop'");assert.equal(invalidated[0].status,'invalidated');
        assert.equal((await call(a,'POST','/spend-entries',{...correction,source_entry_id:randomUUID(),amount_minor:-801},opt)).status,422);
        const fresh=await marker();const complete=await call(a,'POST','/spend-coverage',fresh,opt);assert.equal(complete.body.coverage_status_at_acceptance,'complete');assert.equal(complete.body.revision,2);
        const failed=await call(a,'POST','/spend-coverage',{...fresh,source_marker_id:randomUUID(),checksum:'0'.repeat(64)},opt);assert.equal(failed.status,422);assert.equal(failed.body.coverage_status_at_acceptance,'incomplete');
        const [old]=await db.connection.query("SELECT status FROM marketing_source_coverage WHERE source='shop' ORDER BY revision");assert.deepEqual(old.map(r=>r.status),['invalidated','invalidated','incomplete']);
        assert.deepEqual(economicReceipt(await call(b,'POST','/spend-coverage',fresh,opt)),economicReceipt(complete)); // immutable acceptance receipt, not current coverage
        const repaired=await call(b,'POST','/spend-coverage',{...fresh,source_marker_id:randomUUID()},opt);assert.equal(repaired.body.coverage_status_at_acceptance,'complete');assert.equal(repaired.body.revision,4);
        assert.equal((await call(a,'POST','/spend-coverage',{...fresh,amount_minor:999},opt)).status,409);
        const isolated=await call(a,'POST','/spend-entries',{...entry,source_entry_id:randomUUID(),occurred_at:new Date(now-31*86400000).toISOString()},opt);assert.equal(isolated.status,202);
        const empty=coverageDigest([]);const blocked=await call(a,'POST','/spend-coverage',{campaign_id:campaign,source_marker_id:randomUUID(),start:new Date(now-31*86400000-1).toISOString(),end:new Date(now-31*86400000+1).toISOString(),...empty,amount_minor:0},opt);assert.equal(blocked.status,422);assert.equal(blocked.body.reason,'quarantined_spend_present');
        const [ledger]=await db.connection.query("SELECT COUNT(*) AS n,SUM(amount_minor) AS total FROM marketing_spend_ledger WHERE source='shop_synthetic'");assert.equal(Number(ledger[0].n),2);assert.equal(Number(ledger[0].total),800);
        const [atomic]=await db.connection.query("SELECT COUNT(*) AS n FROM marketing_spend_ledger s LEFT JOIN marketing_outbox o ON o.aggregate_id=s.id AND o.kind='marketing.spend' WHERE o.id IS NULL");assert.equal(Number(atomic[0].n),0);
    }finally{await f.stop()}
});

test('CP03 synthetic generator replays cases and cannot target production',async t=>{
    const f=await fixture(t);if(!f)return;
    const {runSynthetic}=require('../dist/services/marketing/synthetic');
    try{
        const scope={campaign_id:f.campaign,creative_id:f.creative,creative_version:1,brand_id:'brand_a',product_id:'product_a',run_id:randomUUID(),occurred_at:new Date(f.now).toISOString()};
        const first=await runSynthetic(f.a,f.sourceToken,'synthetic',scope);
        const second=await runSynthetic(f.b,f.sourceToken,'synthetic',scope);assert.deepEqual(first,second);assert.equal(first.length,9);
        assert.equal(first.at(-1).coverage_status_at_acceptance,'incomplete');assert.deepEqual(first.at(-1).missing_sources,['agency']);
        await assert.rejects(runSynthetic(f.a,f.sourceToken,'production',scope),/local_environment_required/);
        const [orders]=await f.db.connection.query('SELECT paid_amount_minor,refunded_minor FROM marketing_order_ingestion WHERE order_id=?',[scope.run_id+'_order']);assert.equal(Number(orders[0].paid_amount_minor),5000);assert.equal(Number(orders[0].refunded_minor),1000);
    }finally{await f.stop()}
});

test('CP02/03 migration upgrades version 10, resumes interrupted version 11 and rejects schema drift',async t=>{
    const {database}=require('./helpers/mysql.cjs');const db=await database(t);if(!db)return;
    try{
        await migrateMarketingSchema({target:10});
        await assert.rejects(migrateMarketingSchema({target:11,afterStatement(i){if(i===2)throw Error('interrupt')}}),/marketing_migration_step_failed/);
        assert.equal((await marketingMigrationStatus({target:11})).state,'failed');
        await Promise.all([migrateMarketingSchema(),migrateMarketingSchema()]);
        assert.equal((await marketingMigrationStatus()).state,'applied');assert.equal((await migrateMarketingSchema()).changed,false);
        await assert.rejects(migrateMarketingSchema({target:10}),/downgrade_not_supported/);
        await db.connection.query('ALTER TABLE marketing_click_links DROP INDEX uq_marketing_click_id');
        await assert.rejects(marketingMigrationStatus(),/schema_index_mismatch/);
    }finally{await db.stop()}
});

test('CP01 compatibility: preserves an applied original v3 registry and advances without data deletion',async t=>{
    const {database}=require('./helpers/mysql.cjs');const db=await database(t);if(!db)return;
    const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
    try{
        await migrateMarketingSchema({target:10});
        const sql=fs.readFileSync(path.join(__dirname,'../migrations/legacy/003_marketing_events.sql'));
        const checksum=createHash('sha256').update(sql).digest('hex');
        await db.connection.query("UPDATE marketing_schema_migrations SET version=3,name='003_marketing_events.sql',checksum=? WHERE version=10",[checksum]);
        await seedForCompatibility();
        await migrateMarketingSchema();
        const [rows]=await db.connection.query('SELECT version,name,checksum,state FROM marketing_schema_migrations ORDER BY version');assert.deepEqual(rows.map(r=>r.version),[3,10,11,12,13]);assert.equal(rows[0].checksum,checksum);assert.equal(rows[0].state,'applied');
        const [brands]=await db.connection.query("SELECT name FROM marketing_brands WHERE id='kept_brand'");assert.equal(brands[0].name,'保留原品牌');
        async function seedForCompatibility(){await db.connection.query("INSERT INTO marketing_brands(id,name,environment) VALUES ('kept_brand','保留原品牌','synthetic')")}
    }finally{await db.stop()}
});

test('CP00/02/03 clocks: UTC defaults under a non-UTC server and restore pooled session timezone',async t=>{
    const f=await fixture(t);if(!f)return;
    const {withMarketingConnection}=require('../dist/services/marketing/clock');
    const {adsPool}=require('../dist/services/ads.database');
    try{
        const pooled=await adsPool().getConnection();await pooled.query("SET time_zone='+08:00'");pooled.release();
        await withMarketingConnection(async c=>{const [rows]=await c.query('SELECT @@session.time_zone AS zone');assert.equal(rows[0].zone,'+00:00')});
        const restored=await adsPool().getConnection();const [session]=await restored.query('SELECT @@session.time_zone AS zone');assert.equal(session[0].zone,'+08:00');restored.release();
        const display=await f.call(f.a,'POST','/serve',{campaign_id:f.campaign});
        const body={event_id:randomUUID(),impression_token:display.body.impression_token,occurred_at:new Date().toISOString()};
        const result=await f.call(f.a,'POST','/events/impressions',body,{cookie:display.cookie});assert.equal(result.status,201);
        const [rows]=await f.db.connection.query('SELECT ABS(TIMESTAMPDIFF(SECOND,received_at,UTC_TIMESTAMP())) AS delta FROM marketing_events WHERE id=?',[result.body.receipt_id]);assert.ok(Number(rows[0].delta)<5);
        const [outbox]=await f.db.connection.query('SELECT ABS(TIMESTAMPDIFF(SECOND,created_at,UTC_TIMESTAMP())) AS delta FROM marketing_outbox WHERE aggregate_id=?',[result.body.receipt_id]);assert.ok(Number(outbox[0].delta)<5);
        const [decision]=await f.db.connection.query('SELECT ABS(TIMESTAMPDIFF(SECOND,created_at,UTC_TIMESTAMP())) AS delta FROM marketing_serving_decisions WHERE impression_id=?',[display.body.impression_id]);assert.ok(Number(decision[0].delta)<5);
        const [campaign]=await f.db.connection.query('SELECT ABS(TIMESTAMPDIFF(SECOND,created_at,UTC_TIMESTAMP())) AS delta FROM marketing_campaigns WHERE id=?',[f.campaign]);assert.ok(Number(campaign[0].delta)<15);
    }finally{await f.stop()}
});
