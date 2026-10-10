import fs from 'fs';
import path from 'path';
import { createHash, randomUUID } from 'crypto';
import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { adsPool } from './ads.database';
import { splitSqlStatements } from './sql-statements';

export class AdsMigrationError extends Error {}
const root = path.resolve(__dirname, fs.existsSync(path.join(__dirname, '../package.json')) ? '..' : '../..');
const lockSql = "CONCAT('ads:', LEFT(SHA2(DATABASE(), 256), 50))"; // same lock as the legacy importer
type Options = { directory?: string; target?: number; lockTimeout?: number; afterStatement?: (version: number, index: number) => Promise<void> | void };
type Migration = { version: number; name: string; sql: string; checksum: string };
// Recall 002 and marketing 003 have separate explicit entry points; do not execute them here.
const files = ['001_ads.sql', '004_request_protocol.sql'];

function definitions(directory = path.join(root, 'migrations')): Migration[] {
    return files.map(name => {
        const sql = fs.readFileSync(path.join(directory, name), 'utf8');
        return { version: Number(name.slice(0, 3)), name, sql, checksum: createHash('sha256').update(sql).digest('hex') };
    });
}
async function exists(c: PoolConnection, table: string): Promise<boolean> {
    const [rows] = await c.execute<RowDataPacket[]>('SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=?', [table]);
    return rows.length > 0;
}
async function protocolColumn(c: PoolConnection): Promise<boolean> {
    const [rows] = await c.execute<RowDataPacket[]>("SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='ads_business_operations' AND column_name='request_protocol_version'");
    if (!rows.length) return false;
    if (rows[0].DATA_TYPE !== 'int' || rows[0].IS_NULLABLE !== 'YES' || String(rows[0].COLUMN_DEFAULT) !== '1') throw new AdsMigrationError('incompatible_request_protocol_column');
    return true;
}
async function verify(c: PoolConnection, version: number): Promise<void> {
    if (version === 4) {
        if (!await protocolColumn(c)) throw new AdsMigrationError('missing_request_protocol_column');
        return;
    }
    const tables: Record<string, string[]> = {
        ads_business_ads: ['id', 'title', 'publisher', 'content', 'url', 'price', 'clicks', 'videos', 'version', 'ranking_score'],
        ads_business_operations: ['owner', 'operation_key', 'fingerprint', 'status', 'body', 'created_at'],
        ads_business_audit: ['id', 'owner', 'operation_key', 'action', 'before_data', 'after_data', 'created_at'],
        ads_business_migrations: ['name', 'checksum', 'created_at']
    };
    for (const [table, columns] of Object.entries(tables)) {
        const [rows] = await c.execute<RowDataPacket[]>('SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=?', [table]);
        const actual = new Set(rows.map(row => row.COLUMN_NAME ?? row.column_name));
        if (columns.some(column => !actual.has(column))) throw new AdsMigrationError('missing_columns:' + table);
    }
    const [indexes] = await c.query<RowDataPacket[]>("SELECT DISTINCT INDEX_NAME FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='ads_business_ads'");
    if (!indexes.some(row => row.INDEX_NAME === 'ix_ads_ranking')) throw new AdsMigrationError('missing_ads_ranking_index');
    const [keys] = await c.query<RowDataPacket[]>("SELECT COLUMN_NAME FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='ads_business_operations' AND INDEX_NAME='PRIMARY' ORDER BY SEQ_IN_INDEX");
    if (keys.map(row => row.COLUMN_NAME).join(',') !== 'owner,operation_key') throw new AdsMigrationError('invalid_operations_primary_key');
}
async function audit(c: PoolConnection, migrations: Migration[]): Promise<Map<number, RowDataPacket>> {
    if (await exists(c, 'ads_business_migrations')) {
        const [old] = await c.execute<RowDataPacket[]>("SELECT checksum FROM ads_business_migrations WHERE name='schema-ads-v1'");
        if (old.length && old[0].checksum !== migrations[0].checksum) throw new AdsMigrationError('legacy_schema_checksum_mismatch');
    }
    if (!await exists(c, 'ads_schema_migrations')) return new Map();
    const [rows] = await c.query<RowDataPacket[]>('SELECT * FROM ads_schema_migrations ORDER BY version');
    for (const row of rows) {
        const m = migrations.find(item => item.version === row.version);
        if (!m) throw new AdsMigrationError('unknown_migration:' + row.version);
        if (m.name !== row.name || m.checksum !== row.checksum) throw new AdsMigrationError('migration_checksum_mismatch:' + row.version);
    }
    return new Map(rows.map(row => [row.version, row]));
}
async function readStatus(c: PoolConnection, migrations: Migration[]) {
    const rows = await audit(c, migrations);
    const result = [];
    for (const m of migrations) {
        const row = rows.get(m.version);
        if (row?.state === 'applied') await verify(c, m.version);
        result.push({ version: m.version, name: m.name, checksum: m.checksum, state: row?.state || 'pending', batch_id: row?.batch_id || null, error_code: row?.error_code || null });
    }
    return result;
}
export async function adsMigrationStatus(options: Options = {}) {
    const c = await adsPool().getConnection();
    try { return await readStatus(c, definitions(options.directory)); }
    finally { c.release(); }
}
export async function migrateAdsSchema(options: Options = {}) {
    const migrations = definitions(options.directory), target = options.target ?? migrations[migrations.length - 1].version;
    const timeout = options.lockTimeout ?? 30;
    if (!migrations.some(m => m.version === target)) throw new AdsMigrationError('unknown_target_version');
    if (!Number.isInteger(timeout) || timeout < 0 || timeout > 60) throw new AdsMigrationError('invalid_lock_timeout');
    const c = await adsPool().getConnection(), batch = randomUUID();
    let locked = false;
    try {
        const [locks] = await c.query<RowDataPacket[]>(`SELECT GET_LOCK(${lockSql}, ?) AS acquired`, [timeout]);
        if (Number(locks[0].acquired) !== 1) throw new AdsMigrationError('migration_lock_timeout');
        locked = true;
        const rows = await audit(c, migrations); // before any DDL
        if ([...rows.keys()].some(v => v > target)) throw new AdsMigrationError('downgrade_not_supported');
        await c.query(`CREATE TABLE IF NOT EXISTS ads_schema_migrations (
            version INT PRIMARY KEY, name VARCHAR(100) NOT NULL, checksum CHAR(64) NOT NULL,
            state VARCHAR(16) NOT NULL, batch_id CHAR(36) NOT NULL,
            started_at DATETIME(6) NOT NULL, applied_at DATETIME(6) NULL, error_code VARCHAR(100) NULL
        ) ENGINE=InnoDB`);
        await c.query(`CREATE TABLE IF NOT EXISTS ads_migration_attempts (
            version INT NOT NULL, batch_id CHAR(36) NOT NULL, state VARCHAR(16) NOT NULL,
            started_at DATETIME(6) NOT NULL, finished_at DATETIME(6) NULL, error_code VARCHAR(100) NULL,
            PRIMARY KEY(version,batch_id)
        ) ENGINE=InnoDB`);
        for (const m of migrations.filter(item => item.version <= target)) {
            if (rows.get(m.version)?.state === 'applied') { await verify(c, m.version); continue; }
            await c.execute(`INSERT INTO ads_schema_migrations (version,name,checksum,state,batch_id,started_at)
                VALUES (?,?,?,'applying',?,NOW(6)) ON DUPLICATE KEY UPDATE state='applying',batch_id=VALUES(batch_id),started_at=NOW(6),applied_at=NULL,error_code=NULL`, [m.version, m.name, m.checksum, batch]);
            await c.execute("INSERT INTO ads_migration_attempts (version,batch_id,state,started_at) VALUES (?,?,'applying',NOW(6))", [m.version, batch]);
            try {
                // A semicolon in a full-line SQL comment is not a statement boundary.
                const statements = splitSqlStatements(m.sql);
                for (const [i, statement] of statements.entries()) {
                    if (m.version !== 4 || !await protocolColumn(c)) await c.query(statement);
                    await options.afterStatement?.(m.version, i);
                }
                await verify(c, m.version);
                await c.beginTransaction();
                await c.execute("UPDATE ads_schema_migrations SET state='applied',applied_at=NOW(6) WHERE version=?", [m.version]);
                await c.execute("UPDATE ads_migration_attempts SET state='applied',finished_at=NOW(6) WHERE version=? AND batch_id=?", [m.version, batch]);
                if (m.version === 1) await c.execute("INSERT IGNORE INTO ads_business_migrations (name,checksum) VALUES ('schema-ads-v1',?)", [m.checksum]);
                await c.commit();
            } catch (error) {
                await c.rollback();
                const code = error instanceof AdsMigrationError ? error.message : 'migration_step_failed';
                await c.execute("UPDATE ads_schema_migrations SET state='failed',error_code=? WHERE version=?", [code, m.version]);
                await c.execute("UPDATE ads_migration_attempts SET state='failed',error_code=?,finished_at=NOW(6) WHERE version=? AND batch_id=?", [code, m.version, batch]);
                throw error;
            }
        }
        return { batch_id: batch, migrations: await readStatus(c, migrations) };
    } finally {
        if (locked) await c.query(`SELECT RELEASE_LOCK(${lockSql})`).catch(() => undefined);
        c.release();
    }
}
