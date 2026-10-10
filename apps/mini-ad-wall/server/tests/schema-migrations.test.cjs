const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { database } = require('./helpers/mysql.cjs');
const { migrateAdsSchema, adsMigrationStatus } = require('../dist/services/ads.migrations');
const { loadReliability, reliabilityFlags } = require('../dist/config/reliability');
const { splitSqlStatements } = require('../dist/services/sql-statements');

test('migration SQL boundaries ignore comments and preserve quoted semicolons', () => {
    assert.deepEqual(splitSqlStatements("-- ignored;\nINSERT INTO t VALUES ('a;b', 'it''s'); /* ignored; */ SELECT 1;"), ["INSERT INTO t VALUES ('a;b', 'it''s')", 'SELECT 1']);
    assert.throws(() => splitSqlStatements("SELECT 'unfinished"), /unterminated/);
    assert.throws(() => splitSqlStatements('DELIMITER $$'), /delimiter/);
});

test('CP02 defaults are disabled; unsupported releases fail explicitly', () => {
    assert.equal(loadReliability({}).deploymentMode, 'development');
    assert.ok(Object.values(loadReliability({}).features).every(value => !value));
    assert.equal(loadReliability({AD_CONTEXT_V1_ENABLED:'true'}).features.AD_CONTEXT_V1_ENABLED,true);
    for (const flag of reliabilityFlags.filter(flag=>flag!=='AD_CONTEXT_V1_ENABLED')) assert.throws(() => loadReliability({ [flag]: 'true' }), /not_implemented/);
    for (const env of [{ BACKEND_DEPLOYMENT_MODE: 'multi_node' }, { BACKEND_DEPLOYMENT_MODE: 'unknown' }, { HOSTING_INPUT_PROTOCOL_VERSION: '2' }, { HOSTING_EXECUTOR_PROTOCOL_VERSION: '2' }, { SSE_SHARED_READER_ENABLED: 'maybe' }]) assert.throws(() => loadReliability(env));
});

test('CP02 real MySQL upgrades preserve business data, serialize, resume and reject drift', async t => {
    const db = await database(t); if (!db) return;
    try {
        const [before] = await db.connection.query('SELECT id,title,price,clicks,version FROM ads_business_ads ORDER BY id');
        const initial = await adsMigrationStatus();
        assert.deepEqual(initial.map(v => v.state), ['applied', 'applied']);
        const [attempts] = await db.connection.query('SELECT * FROM ads_migration_attempts');
        const [lock] = await db.connection.query("SELECT GET_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(),256),50)),0) AS acquired");
        assert.equal(Number(lock[0].acquired), 1);
        try { await assert.rejects(migrateAdsSchema({ lockTimeout: 0 }), /lock_timeout/); }
        finally { await db.connection.query("SELECT RELEASE_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(),256),50)))"); }
        await Promise.all([migrateAdsSchema(), migrateAdsSchema()]);
        const [repeated] = await db.connection.query('SELECT * FROM ads_migration_attempts');
        assert.equal(repeated.length, attempts.length);
        const [after] = await db.connection.query('SELECT id,title,price,clicks,version FROM ads_business_ads ORDER BY id');
        assert.deepEqual(after, before);

        // Return to an actual v1 table layout; data and imported source are retained.
        await db.connection.query('ALTER TABLE ads_business_operations DROP COLUMN request_protocol_version');
        await db.connection.query('DELETE FROM ads_schema_migrations WHERE version=3');
        await db.connection.query('DELETE FROM ads_migration_attempts WHERE version=3');
        assert.equal((await adsMigrationStatus())[1].state, 'pending');
        await assert.rejects(migrateAdsSchema({ afterStatement(version) { if (version === 3) throw new Error('secret must not be logged'); } }));
        const failed = (await adsMigrationStatus())[1];
        assert.equal(failed.state, 'failed'); assert.equal(failed.error_code, 'migration_step_failed');
        await db.connection.query("UPDATE ads_schema_migrations SET state='applying' WHERE version=3");
        await migrateAdsSchema();
        const [retried] = await db.connection.query('SELECT state FROM ads_migration_attempts WHERE version=3 ORDER BY started_at');
        assert.deepEqual(retried.map(r => r.state), ['failed', 'applied']);

        const directory = path.join(db.root, 'migrations'); fs.mkdirSync(directory);
        for (const name of ['001_ads.sql', '003_request_protocol.sql']) fs.copyFileSync(path.join(__dirname, '../migrations', name), path.join(directory, name));
        fs.appendFileSync(path.join(directory, '003_request_protocol.sql'), '\n-- forbidden drift\n');
        await assert.rejects(migrateAdsSchema({ directory }), /checksum_mismatch/);
        await assert.rejects(migrateAdsSchema({ target: 1 }), /downgrade/);
        await db.connection.query("INSERT INTO ads_schema_migrations (version,name,checksum,state,batch_id,started_at) VALUES (99,'future',REPEAT('a',64),'applied',UUID(),NOW(6))");
        await assert.rejects(migrateAdsSchema(), /unknown_migration/);
    } finally { await db.stop(); }
});

test('CP02 adopts legacy business schema and status does not initialize an empty database', async t => {
    const db = await database(t); if (!db) return;
    try {
        await db.connection.query('DROP TABLE ads_schema_migrations');
        await db.connection.query('DROP TABLE ads_migration_attempts');
        await db.connection.query('ALTER TABLE ads_business_operations DROP COLUMN request_protocol_version');
        const [count] = await db.connection.query('SELECT COUNT(*) AS total FROM ads_business_ads');
        await migrateAdsSchema();
        assert.equal((await adsMigrationStatus())[1].state, 'applied');
        const [after] = await db.connection.query('SELECT COUNT(*) AS total FROM ads_business_ads');
        assert.equal(after[0].total, count[0].total);
        // Same isolated server; fresh DB has no tables before or after status.
        const { closeAdsDatabase } = require('../dist/services/ads.database');
        await closeAdsDatabase();
        await db.connection.query('CREATE DATABASE ads_empty_status');
        process.env.ADS_MYSQL_URL = 'mysql://root@localhost/ads_empty_status';
        assert.deepEqual((await adsMigrationStatus()).map(v => v.state), ['pending', 'pending']);
        const [tables] = await db.connection.query("SELECT table_name FROM information_schema.tables WHERE table_schema='ads_empty_status'");
        assert.equal(tables.length, 0);
    } finally { await db.stop(); }
});
