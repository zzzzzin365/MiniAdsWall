const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { database } = require('./helpers/mysql.cjs');

const legacyAd = { id: 'legacy-ad', title: '旧广告', publisher: 'operator', content: '旧内容', url: 'https://example.com', price: 5, clicks: 7, videos: ['video.mp4'] };
const key = 'legacy-operation-1234';
const record = { fingerprint: 'a'.repeat(64), status: 201, body: legacyAd, before: [], after: [legacyAd], createdAt: '2026-09-23T00:00:00.000Z' };

test('real MySQL: safe legacy import, persistence and rollback', async t => {
    const source = { ads: [legacyAd], operations: { ['owner:with:colon:' + key]: record } };
    const db = await database(t, source); if (!db) return;
    const model = require('../dist/models/ads.model').default;
    const { migrateAds } = require('../dist/scripts/migrate-ads');
    try {
        await t.test('legacy ads, click counts, historical results and audit are preserved', async () => {
            assert.deepEqual(await model.getAllAds(), [{ ...legacyAd, version: 1 }]);
            const result = await model.getOperation('owner:with:colon', key);
            assert.deepEqual(result.body, record.body); assert.equal(result.createdAt, record.createdAt);
            const [rows] = await db.connection.query('SELECT before_data, after_data FROM ads_business_audit');
            assert.deepEqual(rows[0].before_data, []); assert.deepEqual(rows[0].after_data, [legacyAd]);
        });
        await t.test('repeating migration leaves live edits intact and retains the source file', async () => {
            const before = fs.readFileSync(db.sourcePath);
            const operation = await model.executeOperation('operator', randomUUID(), 'b'.repeat(64), 'PUT /api/ads/legacy-ad', async () => ({ status: 200, body: await model.update(legacyAd.id, { ...legacyAd, id: undefined, clicks: undefined, price: 10, version: 1 }) }));
            assert.equal(operation.body.version, 2);
            const result = await migrateAds(db.sourcePath); assert.equal(result.imported, false);
            assert.equal((await model.getAllAds())[0].price, 10);
            assert.deepEqual(fs.readFileSync(db.sourcePath), before);
            const altered = path.join(db.root, 'altered.json'); fs.writeFileSync(altered, JSON.stringify([legacyAd]));
            await assert.rejects(migrateAds(altered), /已导入版本不同/);
        });
        await t.test('exceptions after the ad write leave neither a new ad nor operation nor audit', async () => {
            const key = randomUUID();
            const [before] = await db.connection.query('SELECT COUNT(*) AS n FROM ads_business_audit');
            await assert.rejects(model.executeOperation('operator', key, 'c'.repeat(64), 'POST /api/ads', async () => {
                await model.create(legacyAd); throw Object.assign(new Error('injected transaction abort'), { status: 409 });
            }), /injected transaction abort/);
            assert.equal((await model.getAllAds()).length, 1); assert.equal(await model.getOperation('operator', key), undefined);
            const [after] = await db.connection.query('SELECT COUNT(*) AS n FROM ads_business_audit'); assert.equal(after[0].n, before[0].n);
            const [unfinished] = await db.connection.query('SELECT COUNT(*) AS n FROM ads_business_operations WHERE status IS NULL'); assert.equal(unfinished[0].n, 0);
        });
        await t.test('ranking matches the existing score formula and provides a ranking index', async () => {
            await model.executeOperation('operator', randomUUID(), 'd'.repeat(64), 'POST /api/ads', async () => ({ status: 201, body: await model.create({ ...legacyAd, title: 'new', price: 20 }) }));
            const ads = await model.getAllAds(); const score = a => a.price * (1 + a.clicks * .42);
            assert.ok(score(ads[0]) >= score(ads[1]));
            const [plan] = await db.connection.query('EXPLAIN SELECT id FROM ads_business_ads ORDER BY ranking_score DESC, id ASC LIMIT 20');
            assert.equal(plan[0].key, 'ix_ads_ranking'); assert.doesNotMatch(plan[0].Extra, /filesort/);
        });
        await t.test('operator identities are exact, including trailing spaces', async () => {
            const key = randomUUID();
            await model.executeOperation('operator', key, 'e'.repeat(64), 'POST /api/ads', async () => ({ status: 400, body: { error: 'validation' } }));
            assert.equal(await model.getOperation('operator ', key), undefined);
            await model.executeOperation('operator ', key, 'f'.repeat(64), 'POST /api/ads', async () => ({ status: 400, body: { error: 'separate identity' } }));
            assert.equal((await model.getOperation('operator', key)).body.error, 'validation');
        });
        await t.test('malformed legacy data fails before writes', async () => {
            const bad = path.join(db.root, 'bad.json'); fs.writeFileSync(bad, JSON.stringify([{ ...legacyAd, clicks: -1 }]));
            await assert.rejects(migrateAds(bad), /点击或视频无效/);
            assert.equal((await model.getAllAds()).length, 2);
        });
    } finally { await db.stop(); }
});

test('real MySQL: import refuses to merge existing business records', async t => {
    const db = await database(t, []); if (!db) return;
    try {
        const { migrateAds } = require('../dist/scripts/migrate-ads');
        // Simulate a database that started accepting traffic before the first import.
        await db.connection.query("DELETE FROM ads_business_migrations WHERE name = 'legacy-json'");
        await db.connection.query('INSERT INTO ads_business_ads (id,title,publisher,content,url,price,videos) VALUES (?,?,?,?,?,?,?)', ['live', 'live', 'operator', 'text', 'https://example.com', 1, '[]']);
        fs.writeFileSync(db.sourcePath, JSON.stringify([legacyAd]));
        await assert.rejects(migrateAds(db.sourcePath), /已有业务记录/);
        const [rows] = await db.connection.query('SELECT id FROM ads_business_ads'); assert.deepEqual(rows.map(r => r.id), ['live']);
    } finally { await db.stop(); }
});
