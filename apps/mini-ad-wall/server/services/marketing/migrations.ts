import fs from 'fs';
import path from 'path';
import { createHash, randomUUID } from 'crypto';
import { PoolConnection, RowDataPacket } from 'mysql2/promise';
import { adsPool } from '../ads.database';
import { migrateAdsSchema } from '../ads.migrations';
import { splitSqlStatements } from '../sql-statements';

const serverRoot = path.resolve(__dirname, fs.existsSync(path.join(__dirname, '../../package.json')) ? '../..' : '../../..');
export const marketingMigrationVersion = 13;
const filenames = ['010_marketing_events.sql', '011_marketing_ingestion.sql', '012_marketing_aggregation.sql', '013_marketing_reports.sql'];
const lockSql = "CONCAT('ads:', LEFT(SHA2(DATABASE(), 256), 50))";
type Options = { directory?: string; target?: number; afterStatement?: (index: number) => void | Promise<void> };
function definitions(directory = path.join(serverRoot, 'migrations')) {
    return ['legacy/003_marketing_events.sql', ...filenames].map(relative => {
        const name = path.basename(relative);
        const sql = fs.readFileSync(path.join(directory, relative), 'utf8');
        return { version: Number(name.slice(0, 3)), name, checksum: createHash('sha256').update(sql).digest('hex'), statements: splitSqlStatements(sql) };
    });
}
function failure(code: string) { return Object.assign(new Error(code), { status: 503, code }); }
const normalize = (value: string) => value.toLowerCase().replace(/_utf8mb4/g, '').replace(/\\'/g, "'").replace(/[`\s()]/g, '');

async function exists(c: PoolConnection) {
    const [r] = await c.query<RowDataPacket[]>("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='marketing_schema_migrations'");
    return r.length > 0;
}
async function audit(c: PoolConnection, migrations: ReturnType<typeof definitions>) {
    if (!await exists(c)) return new Map<number, RowDataPacket>();
    const [r] = await c.query<RowDataPacket[]>('SELECT * FROM marketing_schema_migrations ORDER BY version');
    for (const row of r) {
        const definition = migrations.find(m => m.version === row.version);
        if (!definition) throw failure('unknown_marketing_migration');
        if (row.name !== definition.name || row.checksum !== definition.checksum) throw failure('marketing_migration_checksum_mismatch');
        if (!['applying', 'failed', 'applied'].includes(row.state)) throw failure('invalid_marketing_migration_state');
    }
    return new Map<number, RowDataPacket>(r.map(row => [row.version, row]));
}
// Inspect types, nullability, generated expressions, indexes, FKs and CHECKs.
// CREATE IF NOT EXISTS alone must not adopt an incompatible pre-existing table.
async function verify(c: PoolConnection, statements: string[]) {
    for (const statement of statements) {
        const match = statement.match(/^CREATE TABLE IF NOT EXISTS (marketing_\w+) \(/);
        if (!match) throw failure('invalid_marketing_migration_definition');
        const table = match[1], lines = statement.split('\n').slice(1, -1).map(s => s.trim().replace(/,$/, ''));
        const expected = lines.map(s => ({ line: s, match: s.match(/^(\w+) (ENUM\([^)]*\)|(?:VARCHAR|CHAR|DATETIME|DATE|INT|BIGINT|BOOLEAN|JSON|TEXT)(?:\(\d+\))?(?: UNSIGNED)?)(?: |$)/) })).filter(v => v.match);
        const [columns] = await c.execute<RowDataPacket[]>('SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,GENERATION_EXPRESSION FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=? ORDER BY ORDINAL_POSITION', [table]);
        if (columns.length !== expected.length) throw failure('marketing_schema_columns_mismatch');
        for (const [i, e] of expected.entries()) {
            const type = e.match![2].toLowerCase().replace('boolean', 'tinyint(1)');
            const generated = e.line.match(/GENERATED ALWAYS AS \((.*)\) STORED/);
            if (columns[i].COLUMN_NAME !== e.match![1] || columns[i].COLUMN_TYPE !== type || columns[i].IS_NULLABLE !== (e.line.includes('NOT NULL') ? 'NO' : 'YES') || normalize(columns[i].GENERATION_EXPRESSION || '') !== normalize(generated?.[1] || '')) throw failure('marketing_schema_column_mismatch');
        }
        const [indexes] = await c.execute<RowDataPacket[]>('SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME,SEQ_IN_INDEX FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=? ORDER BY INDEX_NAME,SEQ_IN_INDEX', [table]);
        for (const line of lines) {
            const key = line.match(/^(PRIMARY KEY|UNIQUE KEY (\w+)|INDEX (\w+))\(([^)]+)\)/);
            if (!key) continue;
            const keyName = key[1] === 'PRIMARY KEY' ? 'PRIMARY' : key[2] || key[3];
            const actual = indexes.filter(r => r.INDEX_NAME === keyName);
            if (actual.map(r => r.COLUMN_NAME).join(',') !== key[4].replace(/\s/g, '') || actual.some(r => r.NON_UNIQUE !== (key[3] ? 1 : 0))) throw failure('marketing_schema_index_mismatch');
        }
        const [foreign] = await c.execute<RowDataPacket[]>('SELECT CONSTRAINT_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME,ORDINAL_POSITION FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name=? AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY CONSTRAINT_NAME,ORDINAL_POSITION', [table]);
        const actualForeign = new Map<string, RowDataPacket[]>();
        for (const row of foreign) actualForeign.set(row.CONSTRAINT_NAME, [...(actualForeign.get(row.CONSTRAINT_NAME) || []), row]);
        const expectedForeign = lines.map(s => s.match(/^FOREIGN KEY \(([^)]+)\) REFERENCES (\w+)\(([^)]+)\)/)).filter(Boolean);
        if (actualForeign.size !== expectedForeign.length) throw failure('marketing_schema_fk_mismatch');
        for (const f of expectedForeign) {
            if (![...actualForeign.values()].some(rows => rows.map(r => r.COLUMN_NAME).join(',') === f![1] && rows.every(r => r.REFERENCED_TABLE_NAME === f![2]) && rows.map(r => r.REFERENCED_COLUMN_NAME).join(',') === f![3])) throw failure('marketing_schema_fk_mismatch');
        }
        const [checks] = await c.execute<RowDataPacket[]>(`SELECT cc.CHECK_CLAUSE,tc.ENFORCED FROM information_schema.table_constraints tc JOIN information_schema.check_constraints cc ON tc.CONSTRAINT_SCHEMA=cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME=cc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA=DATABASE() AND tc.TABLE_NAME=? AND tc.CONSTRAINT_TYPE='CHECK'`, [table]);
        const expectedChecks = lines.filter(s => s.startsWith('CHECK (')).map(s => normalize(s.slice(6)));
        if (checks.length !== expectedChecks.length || checks.some(r => r.ENFORCED !== 'YES') || expectedChecks.some(s => !checks.some(r => normalize(r.CHECK_CLAUSE) === s))) throw failure('marketing_schema_check_mismatch');
    }
}
export async function marketingMigrationStatus(options: Options = {}) {
    const migrations = definitions(options.directory), target = options.target ?? marketingMigrationVersion;
    if (!migrations.some(m => m.version === target)) throw failure('unknown_marketing_target');
    const c = await adsPool().getConnection();
    try {
        const rows = await audit(c, migrations);
        for (const m of migrations) if (rows.get(m.version)?.state === 'applied') await verify(c, m.statements);
        const d = migrations.find(m => m.version === target)!;
        return { version: target, name: d.name, checksum: d.checksum, state: rows.get(target)?.state || 'pending', error_code: rows.get(target)?.error_code || null };
    } finally { c.release(); }
}
export async function requireMarketingSchema(minimum = 10) {
    const c = await adsPool().getConnection().catch(() => { throw failure('marketing_storage_unavailable'); });
    try {
        const rows = await audit(c, definitions());
        if (rows.get(minimum)?.state !== 'applied') throw failure('marketing_migration_required');
    } catch (e) {
        if ((e as any).code?.startsWith('marketing_') || (e as any).code === 'unknown_marketing_migration') throw e;
        throw failure('marketing_storage_unavailable');
    } finally { c.release(); }
}
export async function migrateMarketingSchema(options: Options = {}) {
    const migrations = definitions(options.directory), target = options.target ?? marketingMigrationVersion;
    await marketingMigrationStatus({ ...options, target });
    await migrateAdsSchema();
    const c = await adsPool().getConnection(), batch = randomUUID();
    let locked = false;
    try {
        const [r] = await c.query<RowDataPacket[]>(`SELECT GET_LOCK(${lockSql},30) AS acquired`);
        if (Number(r[0].acquired) !== 1) throw failure('marketing_migration_lock_timeout');
        locked = true;
        const rows = await audit(c, migrations);
        if ([...rows.keys()].some(version => version > target)) throw failure('marketing_downgrade_not_supported');
        await c.query(`CREATE TABLE IF NOT EXISTS marketing_schema_migrations (
            version INT NOT NULL PRIMARY KEY,name VARCHAR(100) NOT NULL,checksum CHAR(64) NOT NULL,
            state ENUM('applying','failed','applied') NOT NULL,batch_id CHAR(36) NOT NULL,
            started_at DATETIME(3) NOT NULL,applied_at DATETIME(3) NULL,error_code VARCHAR(100) NULL
        ) ENGINE=InnoDB`);
        await c.query(`CREATE TABLE IF NOT EXISTS marketing_migration_attempts (
            version INT NOT NULL,batch_id CHAR(36) NOT NULL,state VARCHAR(16) NOT NULL,
            started_at DATETIME(3) NOT NULL,finished_at DATETIME(3) NULL,error_code VARCHAR(100) NULL,
            PRIMARY KEY(version,batch_id)
        ) ENGINE=InnoDB`);
        let changed = false;
        // Never apply the archived namespace to a fresh database. Existing v3 rows
        // retain their exact checksum and audit history before advancing to v10/v11.
        for (const d of migrations.filter(m => m.version <= target && (m.version !== 3 || rows.has(3)))) {
            if (rows.get(d.version)?.state === 'applied') { await verify(c, d.statements); continue; }
            changed = true;
            await c.execute(`INSERT INTO marketing_schema_migrations(version,name,checksum,state,batch_id,started_at) VALUES (?,?,?,'applying',?,NOW(3)) ON DUPLICATE KEY UPDATE state='applying',batch_id=VALUES(batch_id),started_at=NOW(3),applied_at=NULL,error_code=NULL`, [d.version, d.name, d.checksum, batch]);
            await c.execute("INSERT INTO marketing_migration_attempts(version,batch_id,state,started_at) VALUES (?,?,'applying',NOW(3))", [d.version, batch]);
            try {
                for (const [index, sql] of d.statements.entries()) { await c.query(sql); await options.afterStatement?.(index); }
                await verify(c, d.statements);
                await c.beginTransaction();
                await c.execute("UPDATE marketing_schema_migrations SET state='applied',applied_at=NOW(3) WHERE version=?", [d.version]);
                await c.execute("UPDATE marketing_migration_attempts SET state='applied',finished_at=NOW(3) WHERE version=? AND batch_id=?", [d.version, batch]);
                await c.commit();
            } catch (error) {
                await c.rollback();
                const code = (error as any).code?.startsWith('marketing_') ? (error as any).code : 'marketing_migration_step_failed';
                await c.execute("UPDATE marketing_schema_migrations SET state='failed',error_code=? WHERE version=?", [code, d.version]);
                await c.execute("UPDATE marketing_migration_attempts SET state='failed',finished_at=NOW(3),error_code=? WHERE version=? AND batch_id=?", [code, d.version, batch]);
                throw failure(code);
            }
        }
        return { version: target, state: 'applied', changed, batch_id: batch };
    } finally {
        if (locked) await c.query(`SELECT RELEASE_LOCK(${lockSql})`).catch(() => undefined);
        c.release();
    }
}
