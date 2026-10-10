const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const { database } = require('./helpers/mysql.cjs');
const { loadMarketing, marketingFlags } = require('../dist/config/marketing');
const { validContract } = require('../dist/services/marketing/contracts');
const { migrateMarketingSchema, marketingMigrationStatus } = require('../dist/services/marketing/migrations');
const { seedMarketingScope } = require('../dist/services/marketing/seed');
const { closeAdsDatabase } = require('../dist/services/ads.database');

test('marketing v1 fixtures have the same expectations in Node/Python; flags fail closed', () => {
    const cases = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../../contracts/marketing/v1/examples.json')));
    for (const c of cases) assert.equal(validContract(c.name, c.value), c.valid, c.label);
    assert.ok(Object.values(loadMarketing({}).features).every(v => !v));
    assert.equal(loadMarketing({ MARKETING_EVENTS_ENABLED: 'true' }).environment, 'synthetic');
    for (const env of [{ MARKETING_EVENTS_ENABLED: 'maybe' }, { MARKETING_CONTENT_ENABLED: 'true' }, { MARKETING_EVENTS_ENABLED: 'true', MARKETING_CONTENT_ENABLED: 'true' }, { MARKETING_PROTOCOL_VERSION: '2' }, { MARKETING_ENVIRONMENT: 'bad' }]) assert.throws(() => loadMarketing(env));
});

test('real MySQL marketing migration resumes partial DDL, preserves ads and rejects schema/checksum drift', async t => {
    const db = await database(t); if (!db) return;
    try {
        const [before] = await db.connection.query('SELECT id,title,price,clicks,version FROM ads_business_ads ORDER BY id');
        assert.equal((await marketingMigrationStatus()).state, 'pending');
        await assert.rejects(migrateMarketingSchema({ afterStatement(index) { if (index === 2) throw Error('fault'); } }), /marketing_migration_step_failed/);
        assert.equal((await marketingMigrationStatus()).state, 'failed');
        const [partial] = await db.connection.query("SHOW TABLES LIKE 'marketing_products'"); assert.equal(partial.length, 1);
        await Promise.all([migrateMarketingSchema(), migrateMarketingSchema()]);
        assert.equal((await marketingMigrationStatus()).state, 'applied');
        const [attempts] = await db.connection.query('SELECT state FROM marketing_migration_attempts ORDER BY started_at');
        assert.deepEqual(attempts.map(v => v.state), ['failed', 'applied']);
        assert.equal((await migrateMarketingSchema()).changed, false);
        const [after] = await db.connection.query('SELECT id,title,price,clicks,version FROM ads_business_ads ORDER BY id'); assert.deepEqual(after, before);
        const [count] = await db.connection.query('SELECT COUNT(*) AS total FROM marketing_events'); assert.equal(Number(count[0].total), 0);
        const directory = path.join(db.root, 'changed'); fs.mkdirSync(directory);
        fs.writeFileSync(path.join(directory, '003_marketing_events.sql'), fs.readFileSync(path.join(__dirname, '../migrations/003_marketing_events.sql')) + '\n-- drift\n');
        await assert.rejects(migrateMarketingSchema({ directory }), /checksum_mismatch/);
        await db.connection.query('ALTER TABLE marketing_events DROP INDEX uq_valid_click');
        await assert.rejects(marketingMigrationStatus(), /schema_index_mismatch/);
        await db.connection.query('ALTER TABLE marketing_events ADD UNIQUE KEY uq_valid_click(environment,valid_click_impression)');
        assert.equal((await marketingMigrationStatus()).state, 'applied');
        await db.connection.query("INSERT INTO marketing_schema_migrations VALUES (99,'future',REPEAT('a',64),'applied',UUID(),NOW(3),NOW(3),NULL)");
        await assert.rejects(migrateMarketingSchema(), /unknown_marketing_migration/);
    } finally { await db.stop(); }
});

test('real MySQL empty-db status is read-only and explicit marketing up initializes without ads or seeds', async t => {
    const db = await database(t); if (!db) return;
    try {
        await closeAdsDatabase();
        await db.connection.query('CREATE DATABASE marketing_empty');
        process.env.ADS_MYSQL_URL = 'mysql://root@localhost/marketing_empty';
        assert.equal((await marketingMigrationStatus()).state, 'pending');
        const [empty] = await db.connection.query("SELECT table_name FROM information_schema.tables WHERE table_schema='marketing_empty'"); assert.equal(empty.length, 0);
        await migrateMarketingSchema();
        assert.equal((await marketingMigrationStatus()).state, 'applied');
        const [r] = await db.connection.query('SELECT COUNT(*) AS total FROM marketing_empty.ads_business_ads'); assert.equal(Number(r[0].total), 0);
        const [b] = await db.connection.query('SELECT COUNT(*) AS total FROM marketing_empty.marketing_brands'); assert.equal(Number(b[0].total), 0);
    } finally { await db.stop(); }
});

test('real MySQL and two Koa processes: scope, idempotency, resource lifecycle and storage errors', async t => {
    const db = await database(t); if (!db) return;
    const children = [];
    async function start(enabled = true) {
        const child = spawn(process.execPath, ['-e', "const s=require('./dist/app').default.listen(0,'127.0.0.1',()=>console.log('PORT='+s.address().port));"], {
            cwd: path.join(__dirname, '..'), env: { ...process.env, ...db.env, MARKETING_EVENTS_ENABLED: String(enabled), MARKETING_ENVIRONMENT: 'synthetic', ADS_OPERATOR_TOKEN: 'test-operator', ADS_OPERATOR_TOKENS: '{"alice":"alice-token"}' }, stdio: ['ignore', 'pipe', 'pipe']
        });
        children.push(child); let error = '';
        child.stderr.on('data', data => { error += data; });
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(Error('startup: ' + error)), 10000);
            child.once('exit', () => { clearTimeout(timer); reject(Error(error)); });
            child.stdout.on('data', data => { const found = data.toString().match(/PORT=(\d+)/); if (found) { clearTimeout(timer); resolve('http://127.0.0.1:' + found[1]); } });
        });
    }
    async function call(base, method, suffix, body, key, token = 'test-operator') {
        const res = await fetch(base + (suffix.startsWith('/api/') ? suffix : '/api/marketing' + suffix), { method, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(key ? { 'Idempotency-Key': key } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
        const raw = await res.text(); return { status: res.status, body: raw ? (() => { try { return JSON.parse(raw); } catch { return raw; } })() : null };
    }
    try {
        const a = await start(), b = await start(), off = await start(false);
        assert.equal((await call(off, 'POST', '/campaigns', {}, randomUUID())).status, 404);
        assert.equal((await call(a, 'GET', '/campaigns?brand_id=brand_a', undefined, undefined, '')).status, 401);
        assert.equal((await call(a, 'GET', '/campaigns?brand_id=brand_a')).status, 503);
        await migrateMarketingSchema();
        const seed = { brand_id: 'brand_a', product_id: 'product_a', principal: 'operator', name: '品牌 A', environment: 'synthetic', ad_ids: ['1'] };
        await seedMarketingScope(seed); await seedMarketingScope(seed);
        await seedMarketingScope({ ...seed, brand_id: 'brand_b', product_id: 'product_b', principal: 'alice', name: '品牌 B', ad_ids: ['2'] });
        await assert.rejects(seedMarketingScope({ ...seed, environment: 'production' }), /production/);
        await assert.rejects(seedMarketingScope({ ...seed, brand_id: 'brand_c', product_id: 'product_c', name: '品牌 C' }), /其他品牌/);
        const [rolled] = await db.connection.query("SELECT id FROM marketing_brands WHERE id='brand_c'"); assert.equal(rolled.length, 0);
        const [before] = await db.connection.query('SELECT id,price,clicks,version FROM ads_business_ads ORDER BY id');
        const now = Date.now(), input = { brand_id: 'brand_a', product_id: 'product_a', name: '活动 A', objective: 'traffic', starts_at: new Date(now - 60000).toISOString(), ends_at: new Date(now + 3600000).toISOString() };
        assert.equal((await call(a, 'POST', '/campaigns', { ...input, environment: 'production' }, randomUUID())).status, 400);
        assert.equal((await call(a, 'POST', '/campaigns', { ...input, product_id: 'product_b' }, randomUUID())).status, 422);
        assert.equal((await call(a, 'POST', '/campaigns', input, randomUUID(), 'alice-token')).status, 403);
        const key = randomUUID();
        const results = await Promise.all([call(a, 'POST', '/campaigns', input, key), call(b, 'POST', '/campaigns', input, key)]);
        assert.deepEqual(results[0], results[1]); assert.equal(results[0].status, 201);
        const activity = results[0].body;
        assert.equal((await call(a, 'POST', '/campaigns', { ...input, name: 'different' }, key)).status, 409);
        assert.equal((await call(a, 'GET', '/campaigns/' + activity.id, undefined, undefined, 'alice-token')).status, 403);
        assert.equal((await call(a, 'GET', '/campaigns?brand_id=brand_a&limit=101')).status, 400);
        const listed = await call(a, 'GET', '/campaigns?brand_id=brand_a'); assert.equal(listed.body.items.length, 1);
        assert.equal((await call(a, 'POST', `/campaigns/${activity.id}/status`, { expected_version: 1, status: 'active' }, randomUUID())).status, 422);
        const material = { brand_id: 'brand_a', campaign_id: activity.id, ad_id: '1', title: '素材 A', asset_ref: 'uploads/test.mp4', asset_hash: 'a'.repeat(64), landing_url: 'https://example.com', authorization_ref: 'license_a', authorization_expires_at: new Date(now + 86400000).toISOString() };
        assert.equal((await call(a, 'POST', '/creatives', { ...material, ad_id: '2' }, randomUUID())).status, 403);
        assert.equal((await call(a, 'POST', '/creatives', { ...material, authorization_expires_at: new Date(now - 1000).toISOString() }, randomUUID())).status, 422);
        const made = await call(a, 'POST', '/creatives', material, randomUUID()); assert.equal(made.status, 201);
        const id = made.body.id;
        assert.equal((await call(a, 'POST', `/creatives/${id}/status`, { expected_revision: 1, status: 'approved' }, randomUUID())).status, 422);
        assert.equal((await call(a, 'POST', `/creatives/${id}/status`, { expected_revision: 1, status: 'pending_review' }, randomUUID())).status, 200);
        assert.equal((await call(a, 'POST', `/creatives/${id}/status`, { expected_revision: 1, status: 'approved' }, randomUUID())).status, 409);
        const approved = await call(a, 'POST', `/creatives/${id}/status`, { expected_revision: 2, status: 'approved' }, randomUUID()); assert.equal(approved.status, 200); assert.equal(approved.body.reviewer, 'operator'); assert.equal(approved.body.version, 1);
        assert.equal((await call(a, 'POST', `/campaigns/${activity.id}/status`, { expected_version: 1, status: 'active' }, randomUUID())).status, 200);
        assert.equal((await call(a, 'POST', '/creatives', material, randomUUID())).status, 409);
        const [after] = await db.connection.query('SELECT id,price,clicks,version FROM ads_business_ads ORDER BY id'); assert.deepEqual(after, before);
        const [links] = await db.connection.query('SELECT * FROM marketing_ad_links'); assert.equal(links.length, 1);
        const [audit] = await db.connection.query("SELECT after_data FROM ads_business_audit WHERE action LIKE '%marketing/campaigns'"); assert.ok(audit.length > 0);
        await db.connection.query('RENAME TABLE marketing_campaigns TO unavailable_campaigns');
        const unavailable = await call(a, 'GET', '/campaigns?brand_id=brand_a'); assert.equal(unavailable.status, 503); assert.ok(unavailable.body.error.code); assert.ok(unavailable.body.error.request_id);
        await db.connection.query('RENAME TABLE unavailable_campaigns TO marketing_campaigns');
        assert.equal((await call(a, 'GET', '/api/ads')).status, 200);
    } finally {
        for (const child of children) if (child.exitCode === null && child.signalCode === null) { const done = new Promise(r => child.once('exit', r)); child.kill('SIGKILL'); await done; }
        await db.stop();
    }
});
