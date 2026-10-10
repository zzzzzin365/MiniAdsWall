import fs from 'fs';
import path from 'path';
import { createHash } from 'crypto';
import { RowDataPacket } from 'mysql2/promise';
import { storedPrice } from '../services/ad-value';
import { adsPool, closeAdsDatabase } from '../services/ads.database';
import { migrateAdsSchema } from '../services/ads.migrations';

function legacyData(source: string) {
    const bytes = fs.readFileSync(source);
    const stored = JSON.parse(bytes.toString('utf8'));
    const ads = Array.isArray(stored) ? stored : stored.ads;
    const operations = Array.isArray(stored) ? {} : stored.operations || {};
    if (!Array.isArray(ads) || !operations || typeof operations !== 'object' || Array.isArray(operations)) throw new Error('旧文件必须是广告数组或 { ads, operations }');
    const ids = new Set<string>();
    for (const ad of ads) {
        if (typeof ad.id !== 'string' || !ad.id || ad.id.length > 100 || ids.has(ad.id)) throw new Error('旧广告 ID 无效或重复');
        ids.add(ad.id);
        if (['title', 'publisher', 'content', 'url'].some(k => typeof ad[k] !== 'string' || !ad[k].trim()) || ad.title.length > 500 || ad.publisher.length > 255 || Buffer.byteLength(ad.url) > 65535) throw new Error('旧广告字段无效');
        if (!['http:', 'https:'].includes(new URL(ad.url).protocol)) throw new Error('旧广告链接无效');
        if (!storedPrice(ad.price)) throw new Error('旧广告出价无效或超过 8 位小数');
        if (!Number.isSafeInteger(ad.clicks) || ad.clicks < 0 || !Array.isArray(ad.videos || []) || (ad.videos || []).some(v => typeof v !== 'string')) throw new Error('旧广告点击或视频无效');
        if (ad.version !== undefined && (!Number.isInteger(ad.version) || ad.version < 1 || ad.version > 4294967294)) throw new Error('旧广告版本无效');
    }
    const records = Object.entries(operations).map(([key, raw]) => {
        const record = raw as any;
        const split = key.lastIndexOf(':');
        const owner = key.slice(0, split), operationKey = key.slice(split + 1);
        if (split < 1 || owner.length > 190 || !/^[a-zA-Z0-9_-]{16,100}$/.test(operationKey) || !/^[a-f0-9]{64}$/.test(record.fingerprint) || !Number.isInteger(record.status) || record.status < 200 || record.status > 599 || record.body === undefined || !Number.isFinite(Date.parse(record.createdAt))) throw new Error('旧操作记录无效');
        return { owner, operationKey, record };
    });
    return { ads, records, checksum: createHash('sha256').update(bytes).digest('hex') };
}

export async function migrateAds(source?: string): Promise<{ imported: boolean; ads: number; operations: number }> {
    // Validate the entire input before importing. Source is read-only and never renamed/deleted.
    const legacy = source ? legacyData(source) : undefined;
    await migrateAdsSchema();
    const serverRoot = path.resolve(__dirname, fs.existsSync(path.join(__dirname, '../package.json')) ? '..' : '../..');
    const sql = fs.readFileSync(path.join(serverRoot, 'migrations/001_ads.sql'), 'utf8');
    const checksum = createHash('sha256').update(sql).digest('hex');
    const connection = await adsPool().getConnection();
    let locked = false;
    try {
        const [locks] = await connection.query<RowDataPacket[]>("SELECT GET_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(), 256), 50)), 30) AS acquired");
        if (Number(locks[0].acquired) !== 1) throw new Error('广告迁移锁超时');
        locked = true;
        const [versions] = await connection.execute<RowDataPacket[]>("SELECT checksum FROM ads_business_migrations WHERE name = 'schema-ads-v1'");
        if (versions[0] && versions[0].checksum !== checksum) throw new Error('已执行的迁移文件被修改，请使用新迁移版本');
        await connection.execute("INSERT IGNORE INTO ads_business_migrations (name, checksum) VALUES ('schema-ads-v1', ?)", [checksum]);
        if (!legacy) return { imported: false, ads: 0, operations: 0 };
        await connection.beginTransaction();
        try {
            const [markers] = await connection.query<RowDataPacket[]>("SELECT checksum FROM ads_business_migrations WHERE name = 'legacy-json'");
            if (markers[0]) {
                if (markers[0].checksum !== legacy.checksum) throw new Error('旧文件与已导入版本不同，拒绝覆盖数据库');
                await connection.rollback();
                return { imported: false, ads: legacy.ads.length, operations: legacy.records.length };
            }
            const [counts] = await connection.query<RowDataPacket[]>('SELECT (SELECT COUNT(*) FROM ads_business_ads) + (SELECT COUNT(*) FROM ads_business_operations) AS total');
            if (Number(counts[0].total)) throw new Error('广告数据库已有业务记录，拒绝合并或覆盖旧文件');
            for (const ad of legacy.ads) {
                await connection.execute('INSERT INTO ads_business_ads (id, title, publisher, content, url, price, clicks, videos, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    [ad.id, ad.title, ad.publisher, ad.content, ad.url, storedPrice(ad.price), ad.clicks, JSON.stringify(ad.videos || []), ad.version || 1]);
            }
            for (const { owner, operationKey, record } of legacy.records) {
                await connection.execute('INSERT INTO ads_business_operations (owner, operation_key, fingerprint, status, body, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                    [owner, operationKey, record.fingerprint, record.status, JSON.stringify(record.body), new Date(record.createdAt)]);
                await connection.execute("INSERT INTO ads_business_audit (owner, operation_key, action, before_data, after_data, created_at) VALUES (?, ?, 'legacy.import', ?, ?, ?)",
                    [owner, operationKey, JSON.stringify(record.before ?? null), JSON.stringify(record.after ?? null), new Date(record.createdAt)]);
            }
            await connection.execute("INSERT INTO ads_business_migrations (name, checksum) VALUES ('legacy-json', ?)", [legacy.checksum]);
            await connection.commit();
            return { imported: true, ads: legacy.ads.length, operations: legacy.records.length };
        } catch (error) { await connection.rollback(); throw error; }
    } finally {
        if (locked) await connection.query("SELECT RELEASE_LOCK(CONCAT('ads:', LEFT(SHA2(DATABASE(), 256), 50)))").catch(() => undefined);
        connection.release();
    }
}

if (require.main === module) {
    const args = process.argv.slice(2);
    if (args.length && (args.length !== 2 || args[0] !== '--source')) {
        console.error('用法: npm run migrate:ads -- [--source /absolute/path/to/data.json]'); process.exitCode = 1;
    } else {
        migrateAds(args[1]).then(result => console.log(JSON.stringify(result))).catch(error => {
            // Do not dump a driver's error (it may contain SQL or credentials).
            console.error(error.code ? '广告迁移失败，请检查数据库连接和数据库权限' : error.message); process.exitCode = 1;
        }).finally(closeAdsDatabase);
    }
}
